import duckdb
import pandas as pd
import os

print("🔧 Setting up Zomato database from CSV files...")

# Connect to database
conn = duckdb.connect('zomato.duckdb')

# Create schemas
conn.execute("CREATE SCHEMA IF NOT EXISTS staging;")
conn.execute("CREATE SCHEMA IF NOT EXISTS gold;")

# Load CSV files
print("📂 Loading CSV files...")
orders_df = pd.read_csv('data/raw_csvs/orders.csv')
restaurants_df = pd.read_csv('data/raw_csvs/restaurant.csv')
reviews_df = pd.read_csv('data/raw_csvs/reviews.csv')

# Create staging tables
print("🏗️ Creating staging tables...")

# Staging restaurants
conn.execute("""
    CREATE OR REPLACE TABLE staging.stg_restaurants AS
    SELECT 
        restaurant_id,
        restaurant_name,
        city,
        cuisine,
        CAST(rating AS FLOAT) as rating,
        CAST(cost_for_two AS INTEGER) as cost_for_two
    FROM restaurants_df
""")

# Staging orders
conn.execute("""
    CREATE OR REPLACE TABLE staging.stg_orders AS
    SELECT 
        order_id,
        r_id,
        order_date,
        CAST(sales_amount AS FLOAT) as sales_amount
    FROM orders_df
""")

# Create mart tables (Gold layer)
print("📊 Creating mart tables...")

# Mart orders
conn.execute("""
    CREATE OR REPLACE TABLE staging.mart_orders AS
    SELECT 
        r.restaurant_name,
        r.city,
        COUNT(o.order_id) AS total_orders,
        SUM(o.sales_amount) AS total_revenue
    FROM staging.stg_orders o
    JOIN staging.stg_restaurants r ON o.r_id = r.restaurant_id
    GROUP BY r.restaurant_name, r.city
    ORDER BY total_revenue DESC
""")

# Mart city restaurant stats
conn.execute("""
    CREATE OR REPLACE TABLE staging.mart_city_restaurant_stats AS
    SELECT 
        city,
        COUNT(DISTINCT restaurant_id) as restaurant_count,
        SUM(total_orders) as total_orders,
        SUM(total_revenue) as total_revenue
    FROM staging.mart_orders
    GROUP BY city
    ORDER BY total_revenue DESC
""")

# Create gold review sentiments table (empty for now, will be populated by enrich_reviews.py)
print(" Creating gold review sentiments table...")
conn.execute("""
    CREATE OR REPLACE TABLE gold.gold_review_sentiments (
        review_id VARCHAR,
        original_text VARCHAR,
        sentiment VARCHAR,
        topics VARCHAR,
        summary VARCHAR,
        processed_at TIMESTAMP
    )
""")

print("✅ Database setup complete!")
print(f"📈 Tables created:")
print(f"   - staging.stg_restaurants: {conn.execute('SELECT COUNT(*) FROM staging.stg_restaurants').fetchone()[0]} rows")
print(f"   - staging.stg_orders: {conn.execute('SELECT COUNT(*) FROM staging.stg_orders').fetchone()[0]} rows")
print(f"   - staging.mart_orders: {conn.execute('SELECT COUNT(*) FROM staging.mart_orders').fetchone()[0]} rows")
print(f"   - gold.gold_review_sentiments: 0 rows (run enrich_reviews.py to populate)")

conn.close()