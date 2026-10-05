"""
Zomato AI Analytics v2 — Flask backend
----------------------------------------
Same data layer and same reliability fixes as the first working version
(DB path resolution, .env loading, Groq model, error traces, auto-download
of zomato.duckdb from a GitHub Release). What's new in this version:

  - Analytics is one merged page instead of two (Overview + Analytics).
  - City/area drill-down: your `city` column is stored as "Area,City"
    (e.g. "Jayanagar,Bangalore"). These endpoints parse that so you can
    see real cities (Bangalore, Kolkata, ...) and drill into their areas.
  - Every list endpoint is paginated (page/page_size) instead of dumping
    everything at once.
  - Monthly/yearly trend is now ordered oldest -> newest (was backwards).
  - Data Explorer is one unified, filterable endpoint instead of three
    separate ones.
  - /api/insights/* reads precomputed restaurant review analysis from
    gold_restaurant_insights — written once by analyze_restaurants.py,
    never computed live, so this page never calls Groq at request time.
"""

import os
import re
import time
import json
import traceback

import duckdb
import pandas as pd
from flask import Flask, jsonify, request, render_template, g
from dotenv import load_dotenv
from groq import Groq

# ==============================================================================
# BOOTSTRAP (unchanged from the working version)
# ==============================================================================
load_dotenv()
API_KEY = os.getenv("GROQ_API_KEY")

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "zomato.duckdb")

load_dotenv(os.path.join(BASE_DIR, ".env"))
API_KEY = os.getenv("GROQ_API_KEY") or API_KEY
print(f"[startup] GROQ_API_KEY loaded: {bool(API_KEY)}")

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

# A single shared DuckDB connection, reused across every request, turned
# out to intermittently return empty/broken results for some queries while
# others on the same request batch succeeded — almost certainly some kind
# of connection/cursor state bleeding between requests. The fix: give every
# request its own fresh connection via Flask's `g` (request-scoped storage),
# closed automatically when the request ends. This fully isolates each
# request from every other one, at the cost of a small per-request connect
# overhead — entirely fine for a dashboard with human-driven traffic.
DB_READY = False
try:
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"No zomato.duckdb found at {DB_PATH}")
    _startup_check = duckdb.connect(DB_PATH, read_only=True)
    _startup_check.close()
    DB_READY = True
except Exception as e:  # pragma: no cover
    print(f"[startup] Database connection error: {e}")


def get_db():
    """Returns this request's own DuckDB connection, opening one on first
    use within the request and reusing it for the rest of that same
    request only."""
    if "db" not in g:
        g.db = duckdb.connect(DB_PATH, read_only=True)
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        try:
            db.close()
        except Exception:
            pass


client = None
if API_KEY:
    try:
        client = Groq(api_key=API_KEY)
    except Exception as e:
        print(f"[startup] Groq client init error: {e}")


def df_json(df: pd.DataFrame):
    if df is None or df.empty:
        return []
    return json.loads(df.to_json(orient="records", date_format="iso"))


def error_response(exc, status=500):
    tb = traceback.format_exc()
    print(tb)
    return jsonify({"error": str(exc), "trace": tb}), status


def get_page_params(default_size=10, max_size=100):
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    try:
        page_size = int(request.args.get("page_size", default_size))
        page_size = max(1, min(max_size, page_size))
    except ValueError:
        page_size = default_size
    return page, page_size


def paginate(df: pd.DataFrame, page: int, page_size: int):
    total = len(df)
    start = (page - 1) * page_size
    end = start + page_size
    total_pages = max(1, (total + page_size - 1) // page_size)
    return {
        "rows": df_json(df.iloc[start:end]),
        "page": page,
        "page_size": page_size,
        "total_count": total,
        "total_pages": total_pages,
    }


# City is stored as "Area,City" (e.g. "Jayanagar,Bangalore"). Some rows have
# no comma at all — those are treated as city-only, with area == city.
REAL_CITY_SQL = "CASE WHEN contains({col}, ',') THEN trim(split_part({col}, ',', 2)) ELSE trim({col}) END"
AREA_SQL = "CASE WHEN contains({col}, ',') THEN trim(split_part({col}, ',', 1)) ELSE trim({col}) END"


# ==============================================================================
# SAME AI HELPER FUNCTIONS AS THE WORKING VERSION (unchanged logic)
# ==============================================================================
def get_schema():
    tables = ["staging.mart_orders", "gold_review_sentiments", "staging.stg_restaurants", "raw.orders"]
    schema = "Database: Zomato DuckDB\nAvailable Tables:\n"
    for table in tables:
        try:
            desc = get_db().execute(f"DESCRIBE {table}").fetchall()
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


def table_exists(table_name):
    """Checks the catalog instead of running a SELECT that might fail —
    a failing statement on the shared connection was observed to leave it
    in a bad state for the *next* unrelated request, so this never
    executes a query against a table that might not exist."""
    try:
        row = get_db().execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?", [table_name]
        ).fetchone()
        return bool(row and row[0] > 0)
    except Exception:
        return False


@app.route("/api/health")
def health():
    return jsonify({
        "db_connected": DB_READY,
        "ai_connected": client is not None,
        "insights_available": table_exists("gold_restaurant_insights") if DB_READY else False,
    })


# ==============================================================================
# MERGED ANALYTICS — KPIs, city/area drill-down, trend, paginated table
# ==============================================================================
@app.route("/api/analytics/cities")
def analytics_cities():
    """Real cities (parsed from 'Area,City'), ranked by revenue. The 'all
    cities' view and the per-city dropdown both come from this one list."""
    real_city = REAL_CITY_SQL.format(col="city")
    try:
        df = get_db().execute(f"""
            SELECT {real_city} AS city,
                   SUM(total_revenue) AS revenue,
                   SUM(total_orders) AS orders,
                   COUNT(*) AS restaurant_count
            FROM staging.mart_orders
            GROUP BY {real_city}
            ORDER BY revenue DESC
        """).df()
        return jsonify(df_json(df))
    except Exception as e:
        return error_response(e)


@app.route("/api/analytics/kpis")
def analytics_kpis():
    """KPIs, optionally scoped to one real city. No city param = overall."""
    city = (request.args.get("city") or "").strip()
    real_city_orders = REAL_CITY_SQL.format(col="city")
    real_city_rest = REAL_CITY_SQL.format(col="city")

    def one(sql, params=None):
        # Each stat runs independently — one failing query returns null for
        # just that field instead of taking down the whole KPI row.
        try:
            row = get_db().execute(sql, params or []).fetchone()
            return row[0] if row else None
        except Exception:
            return None

    where = f"WHERE {real_city_orders} = ?" if city else ""
    where_rest = f"WHERE {real_city_rest} = ?" if city else ""
    params = [city] if city else []

    out = {
        "total_revenue": one(f"SELECT COALESCE(SUM(total_revenue),0) FROM staging.mart_orders {where}", params),
        "total_orders": one(f"SELECT COALESCE(SUM(total_orders),0) FROM staging.mart_orders {where}", params),
        "avg_order_value": one(
            f"SELECT COALESCE(ROUND(SUM(total_revenue)/NULLIF(SUM(total_orders),0),2),0) FROM staging.mart_orders {where}", params
        ),
        "total_restaurants": one(f"SELECT COUNT(*) FROM staging.stg_restaurants {where_rest}", params),
        "avg_rating": one(f"SELECT COALESCE(ROUND(AVG(rating),2),0) FROM staging.stg_restaurants {where_rest}", params),
    }
    return jsonify(out)


@app.route("/api/analytics/breakdown")
def analytics_breakdown():
    """No city param -> top real cities by revenue (the 'overall' chart).
    With a city param -> areas within that city, so you can compare e.g.
    South Kolkata vs Salt Lake once you've picked Kolkata."""
    city = (request.args.get("city") or "").strip()
    real_city = REAL_CITY_SQL.format(col="city")
    area = AREA_SQL.format(col="city")

    try:
        if city:
            df = get_db().execute(f"""
                SELECT {area} AS label, SUM(total_revenue) AS revenue, SUM(total_orders) AS orders
                FROM staging.mart_orders
                WHERE {real_city} = ?
                GROUP BY {area}
                ORDER BY revenue DESC
                LIMIT 20
            """, [city]).df()
        else:
            df = get_db().execute(f"""
                SELECT {real_city} AS label, SUM(total_revenue) AS revenue, SUM(total_orders) AS orders
                FROM staging.mart_orders
                GROUP BY {real_city}
                ORDER BY revenue DESC
                LIMIT 15
            """).df()
        return jsonify({"scope": "area" if city else "city", "rows": df_json(df)})
    except Exception as e:
        return error_response(e)


@app.route("/api/analytics/trend")
def analytics_trend():
    """Monthly/yearly revenue & order trend, oldest -> newest (left to
    right), optionally scoped to a real city."""
    period = request.args.get("period", "monthly")
    city = (request.args.get("city") or "").strip()
    real_city = REAL_CITY_SQL.format(col="restaurant_city")

    fmt = "%Y-%m" if period == "monthly" else "%Y"
    where_city = f"AND {real_city} = ?" if city else ""
    params = [city] if city else []

    try:
        df = get_db().execute(f"""
            SELECT
                strftime('{fmt}', CAST(order_date AS DATE)) AS period_label,
                COUNT(*) AS total_orders,
                ROUND(SUM(CAST(sales_amount AS FLOAT)), 2) AS total_revenue,
                ROUND(AVG(CAST(sales_amount AS FLOAT)), 2) AS avg_order_value
            FROM raw.orders
            WHERE order_date IS NOT NULL AND sales_amount IS NOT NULL
            {where_city}
            GROUP BY strftime('{fmt}', CAST(order_date AS DATE))
            ORDER BY period_label ASC
        """, params).df()

        summary = {
            "total_revenue": float(df["total_revenue"].sum()) if not df.empty else 0,
            "total_orders": int(df["total_orders"].sum()) if not df.empty else 0,
            "avg_order_value": float(df["avg_order_value"].mean()) if not df.empty else 0,
        }
        return jsonify({"period": period, "rows": df_json(df), "summary": summary})
    except Exception as e:
        return error_response(e)


@app.route("/api/analytics/aov-trend")
def analytics_aov_trend():
    """Average order value over time — same underlying data as the main
    trend endpoint, exposed separately so it has its own chart."""
    return analytics_trend()


@app.route("/api/analytics/top-restaurants")
def analytics_top_restaurants():
    """Top restaurants by revenue, optionally scoped to a real city."""
    city = (request.args.get("city") or "").strip()
    try:
        limit = max(1, min(25, int(request.args.get("limit", 10))))
    except ValueError:
        limit = 10
    real_city = REAL_CITY_SQL.format(col="city")

    try:
        where = f"WHERE {real_city} = ?" if city else ""
        params = [city] if city else []
        df = get_db().execute(f"""
            SELECT restaurant_name, total_revenue
            FROM staging.mart_orders
            {where}
            ORDER BY total_revenue DESC
            LIMIT {limit}
        """, params).df()
        return jsonify(df_json(df))
    except Exception as e:
        return error_response(e)


@app.route("/api/analytics/cuisine")
def analytics_cuisine():
    """Revenue share by cuisine, optionally scoped to a real city. Joins
    mart_orders (revenue) with stg_restaurants (cuisine) on restaurant
    name, since that's the shared key between the two tables."""
    city = (request.args.get("city") or "").strip()
    real_city = REAL_CITY_SQL.format(col="o.city")

    try:
        where = f"WHERE {real_city} = ?" if city else ""
        params = [city] if city else []
        df = get_db().execute(f"""
            SELECT COALESCE(NULLIF(trim(r.cuisine), ''), 'Unspecified') AS cuisine,
                   SUM(o.total_revenue) AS revenue
            FROM staging.mart_orders o
            LEFT JOIN staging.stg_restaurants r ON r.restaurant_name = o.restaurant_name
            {where}
            GROUP BY cuisine
            ORDER BY revenue DESC
            LIMIT 10
        """, params).df()
        return jsonify(df_json(df))
    except Exception as e:
        return error_response(e)


@app.route("/api/analytics/rating-distribution")
def analytics_rating_distribution():
    """How many restaurants fall into each rating bucket (1-2, 2-3, ...),
    optionally scoped to a real city. This is about restaurant quality
    distribution, not revenue, so it's always restaurant-count based."""
    city = (request.args.get("city") or "").strip()
    real_city = REAL_CITY_SQL.format(col="city")

    try:
        where = f"WHERE {real_city} = ? AND rating IS NOT NULL" if city else "WHERE rating IS NOT NULL"
        params = [city] if city else []
        df = get_db().execute(f"""
            SELECT bucket, COUNT(*) AS restaurant_count FROM (
                SELECT
                    CASE
                        WHEN rating < 2 THEN '< 2.0'
                        WHEN rating < 3 THEN '2.0 - 3.0'
                        WHEN rating < 4 THEN '3.0 - 4.0'
                        WHEN rating < 4.5 THEN '4.0 - 4.5'
                        ELSE '4.5+'
                    END AS bucket,
                    CASE
                        WHEN rating < 2 THEN 0
                        WHEN rating < 3 THEN 1
                        WHEN rating < 4 THEN 2
                        WHEN rating < 4.5 THEN 3
                        ELSE 4
                    END AS sort_order
                FROM staging.stg_restaurants
                {where}
            )
            GROUP BY bucket, sort_order
            ORDER BY sort_order
        """, params).df()
        return jsonify(df_json(df))
    except Exception as e:
        return error_response(e)


@app.route("/api/analytics/table")
def analytics_table():
    """Paginated restaurant performance table, optionally scoped to a real
    city, sortable by revenue/orders/name."""
    page, page_size = get_page_params(default_size=10)
    city = (request.args.get("city") or "").strip()
    sort = request.args.get("sort", "revenue")
    sort_col = {"revenue": "total_revenue", "orders": "total_orders", "name": "restaurant_name"}.get(sort, "total_revenue")
    real_city = REAL_CITY_SQL.format(col="city")

    try:
        where = f"WHERE {real_city} = ?" if city else ""
        params = [city] if city else []
        df = get_db().execute(f"""
            SELECT restaurant_name, city, total_orders, total_revenue,
                   ROUND(total_revenue / NULLIF(total_orders, 0), 2) AS avg_order_value
            FROM staging.mart_orders
            {where}
            ORDER BY {sort_col} DESC
        """, params).df()
        return jsonify(paginate(df, page, page_size))
    except Exception as e:
        return error_response(e)


# ==============================================================================
# SENTIMENT HUB — aggregate overview (unchanged) + precomputed per-restaurant
# insights (new; reads gold_restaurant_insights, written offline, never a
# live Groq call at request time)
# ==============================================================================
@app.route("/api/sentiment/summary")
def sentiment_summary():
    if not table_exists("gold_review_sentiments"):
        raw_count = 0
        try:
            row = get_db().execute("SELECT COUNT(*) FROM raw.reviews").fetchone()
            raw_count = row[0] if row else 0
        except Exception:
            pass
        return jsonify({"enriched": False, "raw_count": raw_count, "counts": {}})

    try:
        df_sent = get_db().execute(
            "SELECT sentiment, COUNT(*) as count FROM gold_review_sentiments GROUP BY sentiment"
        ).df()
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


@app.route("/api/sentiment/topics")
def sentiment_topics():
    if not table_exists("gold_review_sentiments"):
        return jsonify([])
    try:
        df_topics = get_db().execute("SELECT topics FROM gold_review_sentiments").df()
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


@app.route("/api/insights/list")
def insights_list():
    """Searchable list of restaurants that have precomputed insights
    available, for the Sentiment Hub's restaurant picker."""
    search = (request.args.get("search") or "").strip()
    page, page_size = get_page_params(default_size=20)

    if not table_exists("gold_restaurant_insights"):
        return jsonify({"rows": [], "page": 1, "page_size": page_size, "total_count": 0, "total_pages": 1})

    try:
        df = get_db().execute("""
            SELECT restaurant_id, restaurant_name, city, review_count,
                   positive_count, negative_count, neutral_count
            FROM gold_restaurant_insights
            ORDER BY review_count DESC
        """).df()
        if search:
            df = df[df["restaurant_name"].str.contains(search, case=False, na=False)]
        return jsonify(paginate(df, page, page_size))
    except Exception as e:
        return error_response(e)


@app.route("/api/insights/<restaurant_id>")
def insights_detail(restaurant_id):
    if not table_exists("gold_restaurant_insights"):
        return jsonify({"found": False})
    try:
        df = get_db().execute(
            "SELECT * FROM gold_restaurant_insights WHERE restaurant_id = ?", [str(restaurant_id)]
        ).df()
        if df.empty:
            return jsonify({"found": False})
        row = df_json(df)[0]
        return jsonify({"found": True, "insight": row})
    except Exception as e:
        return jsonify({"found": False, "error": str(e)})


# ==============================================================================
# AI CHATBOT (unchanged)
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
        res = get_db().execute(sql).df()
        return jsonify({
            "sql": sql,
            "columns": list(res.columns),
            "rows": df_json(res),
            "row_count": len(res),
        })
    except Exception as e:
        return jsonify({"sql": sql, "db_error": str(e)}), 200


# ==============================================================================
# DATA EXPLORER — one unified, paginated, filterable endpoint
# ==============================================================================
@app.route("/api/explorer")
def explorer():
    view = request.args.get("type", "restaurants")
    page, page_size = get_page_params(default_size=10)
    search = (request.args.get("search") or "").strip()
    city = (request.args.get("city") or "").strip()

    try:
        if view == "restaurants":
            try:
                min_rating = float(request.args.get("min_rating", 0))
            except ValueError:
                min_rating = 0.0
            real_city = REAL_CITY_SQL.format(col="city")
            where = f"WHERE {real_city} = ?" if city else ""
            params = [city] if city else []
            df = get_db().execute(f"""
                SELECT restaurant_name, city, rating, cuisine, cost_for_two
                FROM staging.stg_restaurants
                {where}
                ORDER BY rating DESC
            """, params).df()
            if search:
                df = df[df["restaurant_name"].str.contains(search, case=False, na=False)]
            if min_rating > 0:
                df = df[df["rating"] >= min_rating]
            return jsonify(paginate(df, page, page_size))

        elif view == "orders":
            try:
                min_orders = float(request.args.get("min_orders", 0))
                min_revenue = float(request.args.get("min_revenue", 0))
            except ValueError:
                min_orders, min_revenue = 0, 0
            real_city = REAL_CITY_SQL.format(col="city")
            where = f"WHERE {real_city} = ?" if city else ""
            params = [city] if city else []
            df = get_db().execute(f"""
                SELECT restaurant_name, city, total_orders, total_revenue,
                       ROUND(total_revenue / NULLIF(total_orders, 0), 2) AS avg_order_value
                FROM staging.mart_orders
                {where}
                ORDER BY total_revenue DESC
            """, params).df()
            if search:
                df = df[df["restaurant_name"].str.contains(search, case=False, na=False)]
            df = df[(df["total_orders"] >= min_orders) & (df["total_revenue"] >= min_revenue)]
            return jsonify(paginate(df, page, page_size))

        elif view == "reviews":
            try:
                df = get_db().execute("""
                    SELECT review_id, original_text AS review, sentiment, topics, summary
                    FROM gold_review_sentiments
                    ORDER BY review_id
                """).df()
                source = "enriched"
            except Exception:
                df = get_db().execute("""
                    SELECT review_id, comment AS review, rating
                    FROM raw.reviews
                    ORDER BY review_id
                    LIMIT 5000
                """).df()
                source = "raw"
            if search:
                df = df[df["review"].str.contains(search, case=False, na=False)]
            result = paginate(df, page, page_size)
            result["source"] = source
            return jsonify(result)

        else:
            return error_response(f"Unknown explorer type: {view}", 400)
    except Exception as e:
        return error_response(e)


@app.route("/api/explorer/cities")
def explorer_cities():
    """Real city list for the Data Explorer's city filter dropdown."""
    real_city = REAL_CITY_SQL.format(col="city")
    try:
        df = get_db().execute(f"SELECT DISTINCT {real_city} AS city FROM staging.mart_orders ORDER BY city").df()
        return jsonify(df["city"].dropna().tolist())
    except Exception as e:
        return error_response(e)


if __name__ == "__main__":
    app.run(debug=True, port=5000)