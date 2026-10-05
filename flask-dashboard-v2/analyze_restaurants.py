"""
analyze_restaurants.py — ONE-TIME batch analysis script
---------------------------------------------------------
Run this once, locally, where your real zomato.duckdb and real GROQ_API_KEY
already live:

    python analyze_restaurants.py

What it does:
  1. Finds restaurants that actually have enough reviews to be worth
     analyzing (default: 5+), ordered by review count so the most-reviewed
     (most likely to be looked up) restaurants are done first.
  2. Pulls up to 30 of that restaurant's reviews from raw.reviews.
  3. Sends them to Groq, asking for a structured summary: sentiment counts,
     top recurring complaints, top recurring praise, one-line verdict.
  4. Saves the result PERMANENTLY into a new table inside zomato.duckdb
     called gold_restaurant_insights.

Tune MIN_REVIEWS_TO_BOTHER / TOP_N_RESTAURANTS / MAX_REVIEWS_PER_RESTAURANT
below if your dataset is very large — see the comments next to those
constants for why they matter.

After this finishes, the live website never calls Groq for this feature —
it just reads gold_restaurant_insights, which is now part of your database
file. Re-upload zomato.duckdb to your GitHub Release afterward so the
deployed Render app picks up the precomputed data too.

SAFE TO RE-RUN: restaurants already analyzed are skipped automatically
(checked by restaurant_id already present in gold_restaurant_insights), so
if this gets interrupted partway through, just run it again — it picks up
where it left off instead of starting over or wasting API calls.
"""

import os
import re
import json
import time

import duckdb
from dotenv import load_dotenv
from groq import Groq

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "zomato.duckdb")

load_dotenv(os.path.join(BASE_DIR, ".env"))
API_KEY = os.getenv("GROQ_API_KEY")

MODEL = "openai/gpt-oss-120b"

# --- Tuned for large datasets (100k+ restaurants) on Groq's free tier ---
# Most real-world restaurant tables have a long tail of entries with 0-1
# reviews, which aren't worth an LLM call anyway (no real pattern to find).
# These settings focus the (limited, rate-capped) budget on restaurants
# that actually have enough reviews to produce a meaningful summary.
MAX_REVIEWS_PER_RESTAURANT = 30     # fewer reviews per prompt = far fewer tokens per restaurant
MIN_REVIEWS_TO_BOTHER = 5           # skip restaurants with less signal than this
TOP_N_RESTAURANTS = 50             # set to None to process every qualifying restaurant
SLEEP_BETWEEN_CALLS = 0.6           # be polite to the rate limit between successful calls
MAX_RATE_LIMIT_RETRIES = 5          # how many times to wait-and-retry on a 429 before giving up on one restaurant


def get_connection():
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(
            f"No zomato.duckdb found at {DB_PATH}. Put your real database file "
            f"next to this script before running it."
        )
    # NOT read_only — this script is the one place in the whole project that
    # writes to the database, since it's precomputing data meant to be read
    # read-only by the actual web app afterward.
    return duckdb.connect(DB_PATH, read_only=False)


def ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gold_restaurant_insights (
            restaurant_id VARCHAR,
            restaurant_name VARCHAR,
            city VARCHAR,
            review_count INTEGER,
            positive_count INTEGER,
            negative_count INTEGER,
            neutral_count INTEGER,
            top_negative_themes VARCHAR,
            top_positive_themes VARCHAR,
            summary VARCHAR,
            analyzed_at TIMESTAMP
        )
    """)


def already_analyzed_ids(conn):
    try:
        rows = conn.execute("SELECT DISTINCT restaurant_id FROM gold_restaurant_insights").fetchall()
        return {str(r[0]) for r in rows}
    except Exception:
        return set()


def get_restaurants_by_review_volume(conn, min_reviews, top_n):
    """Only restaurants with enough reviews to actually produce a meaningful
    summary, ordered by review count descending so the most-reviewed (most
    likely to be looked up) restaurants get analyzed first. This is the key
    change that makes a 100k+ row restaurant table tractable: most rows
    have 0-1 reviews and would waste API budget for near-zero insight."""
    query = """
        SELECT s.restaurant_id, s.restaurant_name, s.city, COUNT(r.comment) AS review_count
        FROM staging.stg_restaurants s
        JOIN raw.reviews r
          ON CAST(r.restaurant_id AS VARCHAR) = CAST(s.restaurant_id AS VARCHAR)
        WHERE r.comment IS NOT NULL AND trim(r.comment) != ''
        GROUP BY s.restaurant_id, s.restaurant_name, s.city
        HAVING COUNT(r.comment) >= ?
        ORDER BY review_count DESC
    """
    if top_n:
        query += f" LIMIT {int(top_n)}"
    return conn.execute(query, [min_reviews]).fetchall()


def get_reviews_for_restaurant(conn, restaurant_id):
    df = conn.execute(
        """
        SELECT comment
        FROM raw.reviews
        WHERE CAST(restaurant_id AS VARCHAR) = CAST(? AS VARCHAR)
          AND comment IS NOT NULL AND trim(comment) != ''
        LIMIT ?
        """,
        [restaurant_id, MAX_REVIEWS_PER_RESTAURANT],
    ).fetchall()
    return [r[0] for r in df]


def build_prompt(restaurant_name, reviews):
    numbered = "\n".join(f"{i+1}. {r}" for i, r in enumerate(reviews))
    return f"""You are analyzing {len(reviews)} customer reviews for the restaurant "{restaurant_name}".

Reviews:
{numbered}

Return ONLY a JSON object with this exact shape, no markdown, no explanation:
{{
  "positive_count": <int, how many of these reviews are clearly positive>,
  "negative_count": <int, how many are clearly negative>,
  "neutral_count": <int, how many are mixed or neutral>,
  "top_negative_themes": [<up to 4 short phrases, e.g. "late delivery", "cold food", "rude staff">],
  "top_positive_themes": [<up to 4 short phrases, e.g. "fast delivery", "great taste", "good packaging">],
  "summary": "<one or two plain sentences giving the overall verdict on this restaurant, written for a business owner reading it>"
}}
If there are no clear negative or positive themes, return an empty list for that field."""


def extract_json(raw_text):
    """Same defensive cleanup pattern as the rest of the app: strip markdown
    fences and any stray reasoning text a model might wrap around the JSON."""
    cleaned = raw_text.replace("```json", "").replace("```", "").strip()
    cleaned = re.sub(r"(?is)<think>.*?</think>", "", cleaned).strip()
    match = re.search(r"(?s)\{.*\}", cleaned)
    if match:
        cleaned = match.group(0)
    return json.loads(cleaned)


def parse_retry_seconds(error_text):
    """Groq's 429 messages include a human-readable wait time like
    'Please try again in 4m1.05s' or 'in 23.4s' — pull that out so we can
    actually wait the right amount instead of guessing."""
    match = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", error_text)
    if not match:
        return 60  # fallback: wait a minute if we can't parse it
    minutes = int(match.group(1)) if match.group(1) else 0
    seconds = float(match.group(2))
    return minutes * 60 + seconds + 2  # +2s buffer


def analyze_one(client, restaurant_name, reviews):
    prompt = build_prompt(restaurant_name, reviews)
    last_error = None
    rate_limit_waits = 0

    attempt = 0
    while attempt < 3:
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )
            raw = response.choices[0].message.content
            return extract_json(raw)
        except Exception as e:
            last_error = e
            error_text = str(e)
            if "rate_limit_exceeded" in error_text or "429" in error_text:
                if rate_limit_waits >= MAX_RATE_LIMIT_RETRIES:
                    raise RuntimeError(f"Gave up after {MAX_RATE_LIMIT_RETRIES} rate-limit waits: {last_error}")
                wait_s = parse_retry_seconds(error_text)
                print(f"    rate limited — waiting {wait_s:.0f}s before retrying...")
                time.sleep(wait_s)
                rate_limit_waits += 1
                continue  # don't count this as one of the 3 normal attempts
            attempt += 1
            time.sleep(1.5)
    raise RuntimeError(f"Failed after 3 attempts: {last_error}")


def main():
    if not API_KEY:
        print("ERROR: GROQ_API_KEY not found. Make sure .env is next to this script.")
        return

    client = Groq(api_key=API_KEY)
    conn = get_connection()
    ensure_table(conn)

    print("Counting restaurants with enough reviews to analyze (this is a one-time scan)...")
    restaurants = get_restaurants_by_review_volume(conn, MIN_REVIEWS_TO_BOTHER, TOP_N_RESTAURANTS)
    done_ids = already_analyzed_ids(conn)

    total = len(restaurants)
    skipped_already_done = 0
    succeeded = 0
    failed = []

    print(f"\n{total} restaurants qualify (>= {MIN_REVIEWS_TO_BOTHER} reviews each"
          + (f", capped to top {TOP_N_RESTAURANTS} by review count" if TOP_N_RESTAURANTS else "")
          + f"). {len(done_ids)} already analyzed from a previous run — will skip those.\n")

    for i, (restaurant_id, restaurant_name, city, review_count) in enumerate(restaurants, start=1):
        rid_str = str(restaurant_id)

        if rid_str in done_ids:
            skipped_already_done += 1
            continue

        reviews = get_reviews_for_restaurant(conn, restaurant_id)

        try:
            result = analyze_one(client, restaurant_name, reviews)
            conn.execute(
                """
                INSERT INTO gold_restaurant_insights
                (restaurant_id, restaurant_name, city, review_count,
                 positive_count, negative_count, neutral_count,
                 top_negative_themes, top_positive_themes, summary, analyzed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, now())
                """,
                [
                    rid_str,
                    restaurant_name,
                    city,
                    len(reviews),
                    result.get("positive_count", 0),
                    result.get("negative_count", 0),
                    result.get("neutral_count", 0),
                    ", ".join(result.get("top_negative_themes", [])[:4]),
                    ", ".join(result.get("top_positive_themes", [])[:4]),
                    result.get("summary", ""),
                ],
            )
            succeeded += 1
            print(f"[{i}/{total}] {restaurant_name}: done ({len(reviews)} reviews analyzed)")
        except Exception as e:
            failed.append((restaurant_name, str(e)))
            print(f"[{i}/{total}] {restaurant_name}: FAILED — {e}")

        time.sleep(SLEEP_BETWEEN_CALLS)

    conn.close()

    print("\n" + "=" * 60)
    print("DONE")
    print(f"  Analyzed successfully: {succeeded}")
    print(f"  Already done (skipped): {skipped_already_done}")
    print(f"  Failed: {len(failed)}")
    if failed:
        print("\nFailed restaurants (re-run this script to retry just these):")
        for name, err in failed:
            print(f"  - {name}: {err}")
    print("=" * 60)
    print("\nNext step: re-upload this zomato.duckdb file to your GitHub Release")
    print("so the deployed Render app picks up these precomputed insights.")


if __name__ == "__main__":
    main()