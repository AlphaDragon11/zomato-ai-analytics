"""
Zomato AI Analytics — Flask backend
------------------------------------
This is a 1:1 port of the original Streamlit app's data layer. Every query,
every Groq prompt, every fallback is unchanged — it's just returned as JSON
instead of being rendered by st.* calls, so a real HTML/CSS/JS frontend can
consume it. Nothing here is sample/mock data: every route reads from your
zomato.duckdb file.
"""

import os
import re
import time
import json
import traceback

import duckdb
import pandas as pd
from flask import Flask, jsonify, request, render_template
from dotenv import load_dotenv
from groq import Groq

# ==============================================================================
# BOOTSTRAP
# ==============================================================================
load_dotenv()
API_KEY = os.getenv("GROQ_API_KEY")

app = Flask(__name__)

# Resolve the DB path relative to this file, not the current working
# directory — otherwise "zomato.duckdb" only connects if you happen to
# launch `python app.py` from inside this exact folder.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "zomato.duckdb")

# Same fix as the DB path, applied to .env: load it explicitly from this
# file's own folder as a fallback, so GROQ_API_KEY is found regardless of
# which directory you launched `python app.py` from.
load_dotenv(os.path.join(BASE_DIR, ".env"))
API_KEY = os.getenv("GROQ_API_KEY") or API_KEY
print(f"[startup] GROQ_API_KEY loaded: {bool(API_KEY)}")

# zomato.duckdb is too large (~860MB) to live in the git repo, so it's
# hosted as a GitHub Release asset instead and downloaded here on first
# startup if it isn't already sitting next to this file. This runs the
# same way locally (skips — you already have the file) and on Render
# (downloads once, then reuses it on every restart within that instance).
DB_DOWNLOAD_URL = os.getenv(
    "DB_DOWNLOAD_URL",
    "https://github.com/AlphaDragon11/zomato-ai-analytics/releases/download/v1.0/zomato.duckdb",
)


def ensure_database():
    if os.path.exists(DB_PATH):
        return
    print(f"[startup] {DB_PATH} not found — downloading from {DB_DOWNLOAD_URL} ...")
    try:
        import urllib.request
        import shutil

        req = urllib.request.Request(DB_DOWNLOAD_URL, headers={"User-Agent": "zomato-dashboard"})
        with urllib.request.urlopen(req) as response, open(DB_PATH, "wb") as out_file:
            shutil.copyfileobj(response, out_file)
        size_mb = os.path.getsize(DB_PATH) / 1_000_000
        print(f"[startup] Database downloaded successfully ({size_mb:.1f} MB)")
    except Exception as e:
        print(f"[startup] Database download failed: {e}")


ensure_database()

try:
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"No zomato.duckdb found at {DB_PATH}")
    # read_only=True: this app never writes, and it lets this connection
    # coexist with another process (e.g. Streamlit) that might already
    # have the file open, instead of failing on DuckDB's write lock.
    conn = duckdb.connect(DB_PATH, read_only=True)
except Exception as e:  # pragma: no cover
    conn = None
    print(f"[startup] Database connection error: {e}")

client = None
if API_KEY:
    try:
        client = Groq(api_key=API_KEY)
    except Exception as e:
        print(f"[startup] Groq client init error: {e}")


def df_json(df: pd.DataFrame):
    """Convert a DataFrame to JSON-safe records (NaN -> null, numpy -> native)."""
    if df is None or df.empty:
        return []
    return json.loads(df.to_json(orient="records", date_format="iso"))


def error_response(exc, status=500):
    # Send the exact failing line back in the response itself (not just the
    # short message) so it shows up directly in the browser instead of
    # requiring a terminal screenshot to diagnose.
    tb = traceback.format_exc()
    print(tb)
    return jsonify({"error": str(exc), "trace": tb}), status


# ==============================================================================
# SAME HELPER FUNCTIONS AS THE STREAMLIT APP (unchanged logic)
# ==============================================================================
def get_schema():
    tables = ["staging.mart_orders", "gold_review_sentiments", "staging.stg_restaurants", "raw.orders"]
    schema = "Database: Zomato DuckDB\nAvailable Tables:\n"
    for table in tables:
        try:
            desc = conn.execute(f"DESCRIBE {table}").fetchall()
            schema += f"\n{table}:\n" + "\n".join([f"  - {col[0]} ({col[1]})" for col in desc])
        except Exception:
            pass
    return schema


def generate_sql(question, schema):
    prompt = f"""You are an expert SQL analyst. Write a valid DuckDB SELECT query based on this schema:
{schema}
STRICT RULES:
1. Return ONLY raw SQL. No markdown.
2. ALWAYS use ORDER BY [metric] DESC, [name_column] ASC.
3. ALWAYS use ROUND(..., 2) for financial metrics.
4. If impossible, return 'SCHEMA_LIMITATION'."""

    if client is None:
        return "SCHEMA_LIMITATION"

    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=[{"role": "user", "content": prompt + f"\n\nQuestion: {question}"}],
                temperature=0.0,
            )
            raw = response.choices[0].message.content.strip()
            # Defensive cleanup: strip markdown fences and any stray
            # reasoning/preamble text a model might add before the query,
            # so only the SQL itself is checked and returned.
            raw = raw.replace("```sql", "").replace("```", "")
            raw = re.sub(r"(?is)<think>.*?</think>", "", raw).strip()
            match = re.search(r"(?is)\bSELECT\b.*", raw)
            sql = match.group(0).strip() if match else raw
            if sql.upper().startswith("SELECT"):
                return sql
            print(f"[generate_sql] attempt {attempt + 1}: model reply didn't contain SELECT: {raw[:200]!r}")
        except Exception as e:
            print(f"[generate_sql] attempt {attempt + 1} failed: {e}")
            time.sleep(1)
    return "SCHEMA_LIMITATION"


def analyze_review(text):
    prompt = f"""Analyze this review and return JSON:
{{"sentiment": "Positive/Negative/Neutral", "topics": ["topic1", "topic2"], "summary": "5-word summary"}}
Review: "{text}"
"""
    if client is None:
        return None
    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        return json.loads(response.choices[0].message.content)
    except Exception:
        return None


# Only SELECT statements are ever allowed through the chatbot route — the
# model is instructed to only emit SELECT, this is a second, code-level gate.
def is_safe_select(sql: str) -> bool:
    s = sql.strip().rstrip(";")
    if not re.match(r"(?is)^\s*SELECT\b", s):
        return False
    forbidden = r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|COPY|PRAGMA|CALL|EXPORT|IMPORT)\b"
    return re.search(forbidden, s, re.IGNORECASE) is None


# ==============================================================================
# PAGE
# ==============================================================================
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    ok = conn is not None
    return jsonify({"db_connected": ok, "ai_connected": client is not None})


# ==============================================================================
# EXECUTIVE OVERVIEW
# ==============================================================================
@app.route("/api/overview/kpis")
def overview_kpis():
    out = {}
    try:
        out["total_revenue"] = conn.execute(
            "SELECT COALESCE(SUM(total_revenue), 0) FROM staging.mart_orders"
        ).fetchone()[0]
    except Exception:
        out["total_revenue"] = None
    try:
        out["total_orders"] = conn.execute(
            "SELECT COALESCE(SUM(total_orders), 0) FROM staging.mart_orders"
        ).fetchone()[0]
    except Exception:
        out["total_orders"] = None
    try:
        out["total_restaurants"] = conn.execute(
            "SELECT COUNT(*) FROM staging.stg_restaurants"
        ).fetchone()[0]
    except Exception:
        out["total_restaurants"] = None
    try:
        out["avg_rating"] = conn.execute(
            "SELECT COALESCE(ROUND(AVG(rating), 2), 0) FROM staging.stg_restaurants"
        ).fetchone()[0]
    except Exception:
        out["avg_rating"] = None
    return jsonify(out)


@app.route("/api/overview/top-cities")
def overview_top_cities():
    try:
        df = conn.execute(
            "SELECT city, total_revenue FROM staging.mart_orders ORDER BY total_revenue DESC LIMIT 10"
        ).df()
        return jsonify(df_json(df))
    except Exception as e:
        return error_response(e)


@app.route("/api/overview/top-restaurants")
def overview_top_restaurants():
    try:
        df = conn.execute(
            "SELECT restaurant_name, total_revenue FROM staging.mart_orders ORDER BY total_revenue DESC LIMIT 10"
        ).df()
        return jsonify(df_json(df))
    except Exception as e:
        return error_response(e)


@app.route("/api/overview/revenue-by-city")
def overview_revenue_by_city():
    try:
        df = conn.execute(
            """SELECT city, SUM(total_revenue) as rev FROM staging.mart_orders
               GROUP BY city ORDER BY rev DESC LIMIT 10"""
        ).df()
        return jsonify(df_json(df))
    except Exception as e:
        return error_response(e)


@app.route("/api/overview/orders-vs-revenue")
def overview_orders_vs_revenue():
    try:
        df = conn.execute(
            """SELECT city, total_orders, total_revenue FROM staging.mart_orders
               WHERE total_revenue > 0 ORDER BY total_revenue DESC LIMIT 20"""
        ).df()
        return jsonify(df_json(df))
    except Exception as e:
        return error_response(e)


@app.route("/api/overview/table")
def overview_table():
    try:
        df = conn.execute(
            """SELECT restaurant_name, city, total_orders,
                      ROUND(total_revenue, 2) as revenue,
                      ROUND(total_revenue / NULLIF(total_orders, 0), 2) as avg_order_value
               FROM staging.mart_orders
               ORDER BY total_revenue DESC
               LIMIT 20"""
        ).df()
        return jsonify(df_json(df))
    except Exception as e:
        return error_response(e)


# ==============================================================================
# ADVANCED ANALYTICS
# ==============================================================================
@app.route("/api/analytics")
def analytics():
    period = request.args.get("period", "all")
    try:
        min_revenue = int(float(request.args.get("min_revenue", 0)))
    except ValueError:
        min_revenue = 0
    sort_option = request.args.get("sort", "revenue")
    try:
        limit_val = max(1, min(500, int(request.args.get("limit", 20))))
    except ValueError:
        limit_val = 20

    sort_col = {"revenue": "total_revenue", "orders": "total_orders", "city": "city"}.get(
        sort_option, "total_revenue"
    )

    try:
        if period == "monthly":
            df = conn.execute(
                f"""SELECT
                        strftime('%Y-%m', CAST(order_date AS DATE)) as period_label,
                        COUNT(*) as total_orders,
                        ROUND(SUM(CAST(sales_amount AS FLOAT)), 2) as total_revenue,
                        ROUND(AVG(CAST(sales_amount AS FLOAT)), 2) as avg_order_value
                    FROM raw.orders
                    WHERE order_date IS NOT NULL AND sales_amount IS NOT NULL
                    GROUP BY strftime('%Y-%m', CAST(order_date AS DATE))
                    ORDER BY period_label DESC
                    LIMIT {limit_val}"""
            ).df()
        elif period == "yearly":
            df = conn.execute(
                f"""SELECT
                        strftime('%Y', CAST(order_date AS DATE)) as period_label,
                        COUNT(*) as total_orders,
                        ROUND(SUM(CAST(sales_amount AS FLOAT)), 2) as total_revenue,
                        ROUND(AVG(CAST(sales_amount AS FLOAT)), 2) as avg_order_value
                    FROM raw.orders
                    WHERE order_date IS NOT NULL AND sales_amount IS NOT NULL
                    GROUP BY strftime('%Y', CAST(order_date AS DATE))
                    ORDER BY period_label DESC
                    LIMIT {limit_val}"""
            ).df()
        else:
            df = conn.execute(
                f"""SELECT
                        restaurant_name, city, total_orders, total_revenue,
                        ROUND(total_revenue / NULLIF(total_orders, 0), 2) as avg_order_value
                    FROM staging.mart_orders
                    WHERE total_revenue >= {min_revenue}
                    ORDER BY {sort_col} DESC
                    LIMIT {limit_val}"""
            ).df()

        summary = {
            "total_revenue": float(df["total_revenue"].sum()) if not df.empty else 0,
            "total_orders": int(df["total_orders"].sum()) if not df.empty else 0,
            "avg_order_value": float(df["avg_order_value"].mean()) if not df.empty else 0,
        }
        return jsonify({"period": period, "rows": df_json(df), "summary": summary})
    except Exception as e:
        return error_response(e)


# ==============================================================================
# SENTIMENT HUB
# ==============================================================================
@app.route("/api/sentiment/summary")
def sentiment_summary():
    try:
        df_sent = conn.execute(
            "SELECT sentiment, COUNT(*) as count FROM gold_review_sentiments GROUP BY sentiment"
        ).df()
        if df_sent.empty:
            try:
                raw_count = conn.execute("SELECT COUNT(*) FROM raw.reviews").fetchone()[0]
            except Exception:
                raw_count = 0
            return jsonify({"enriched": False, "raw_count": raw_count, "counts": {}})

        counts = {row["sentiment"]: int(row["count"]) for _, row in df_sent.iterrows()}
        return jsonify({
            "enriched": True,
            "counts": {
                "Positive": counts.get("Positive", 0),
                "Negative": counts.get("Negative", 0),
                "Neutral": counts.get("Neutral", 0),
            },
        })
    except Exception as e:
        return error_response(e)


@app.route("/api/sentiment/reviews")
def sentiment_reviews():
    try:
        df = conn.execute(
            "SELECT original_text, sentiment, summary FROM gold_review_sentiments LIMIT 10"
        ).df()
        return jsonify(df_json(df))
    except Exception as e:
        return error_response(e)


@app.route("/api/sentiment/topics")
def sentiment_topics():
    try:
        df_topics = conn.execute("SELECT topics FROM gold_review_sentiments").df()
        all_topics = []
        for topics_str in df_topics["topics"]:
            if topics_str and pd.notna(topics_str):
                all_topics.extend([t.strip() for t in str(topics_str).split(",") if t.strip()])
        if not all_topics:
            return jsonify([])
        topic_counts = pd.Series(all_topics).value_counts().head(15).reset_index()
        topic_counts.columns = ["topic", "count"]
        return jsonify(df_json(topic_counts))
    except Exception as e:
        return error_response(e)


# ==============================================================================
# AI CHATBOT (text-to-SQL)
# ==============================================================================
@app.route("/api/chat", methods=["POST"])
def chat():
    payload = request.get_json(silent=True) or {}
    question = (payload.get("message") or "").strip()
    if not question:
        return error_response("Empty question", 400)

    schema = get_schema()
    sql = generate_sql(question, schema)

    if sql == "SCHEMA_LIMITATION":
        return jsonify({"limitation": True})

    if not is_safe_select(sql):
        return jsonify({"limitation": True, "sql": sql})

    try:
        res = conn.execute(sql).df()
        return jsonify({
            "sql": sql,
            "columns": list(res.columns),
            "rows": df_json(res),
            "row_count": len(res),
        })
    except Exception as e:
        return jsonify({"sql": sql, "db_error": str(e)}), 200


# ==============================================================================
# ALL DATA VIEWS
# ==============================================================================
@app.route("/api/data/restaurants")
def data_restaurants():
    search = (request.args.get("search") or "").strip()
    try:
        min_rating = float(request.args.get("min_rating", 0))
    except ValueError:
        min_rating = 0.0

    try:
        df = conn.execute(
            """SELECT restaurant_name, city, rating, cuisine, cost_for_two
               FROM staging.stg_restaurants
               ORDER BY rating DESC"""
        ).df()
        if search:
            df = df[df["restaurant_name"].str.contains(search, case=False, na=False)]
        if min_rating > 0:
            df = df[df["rating"] >= min_rating]
        return jsonify({"count": len(df), "rows": df_json(df.head(500))})
    except Exception as e:
        return error_response(e)


@app.route("/api/data/orders")
def data_orders():
    cities_param = request.args.get("cities", "")
    cities = [c for c in cities_param.split(",") if c]
    try:
        min_orders = float(request.args.get("min_orders", 0))
        min_revenue = float(request.args.get("min_revenue", 0))
    except ValueError:
        min_orders, min_revenue = 0, 0

    try:
        df = conn.execute(
            """SELECT restaurant_name, city, total_orders, total_revenue,
                      ROUND(total_revenue / NULLIF(total_orders, 0), 2) as avg_order_value
               FROM staging.mart_orders
               ORDER BY total_revenue DESC"""
        ).df()
        if cities:
            df = df[df["city"].isin(cities)]
        df = df[(df["total_orders"] >= min_orders) & (df["total_revenue"] >= min_revenue)]
        return jsonify({"count": len(df), "rows": df_json(df.head(500))})
    except Exception as e:
        return error_response(e)


@app.route("/api/data/cities")
def data_cities():
    try:
        df = conn.execute("SELECT DISTINCT city FROM staging.mart_orders ORDER BY city").df()
        return jsonify(df["city"].dropna().tolist())
    except Exception as e:
        return error_response(e)


@app.route("/api/data/reviews")
def data_reviews():
    try:
        df = conn.execute(
            """SELECT review_id, original_text as review, sentiment, topics, summary
               FROM gold_review_sentiments
               ORDER BY review_id"""
        ).df()
        return jsonify({"source": "enriched", "count": len(df), "rows": df_json(df.head(500))})
    except Exception:
        try:
            df = conn.execute(
                """SELECT review_id, comment as review, rating
                   FROM raw.reviews
                   ORDER BY review_id
                   LIMIT 1000"""
            ).df()
            return jsonify({"source": "raw", "count": len(df), "rows": df_json(df.head(500))})
        except Exception as e:
            return error_response(e)


if __name__ == "__main__":
    app.run(debug=True, port=5000)