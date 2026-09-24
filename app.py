import os
import time
import duckdb
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
from groq import Groq
from dotenv import load_dotenv
from streamlit_option_menu import option_menu
import json
from datetime import datetime

# ==============================================================================
# BLOCK 1: PROFESSIONAL UI SETUP
# ==============================================================================
st.set_page_config(page_title="Zomato AI Analytics", page_icon="🍔", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
<style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    .stDeployButton {display:none;}
    
    .stApp {
        background-color: #f8f9fa;
        color: #2c3e50;
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    }
    
    /* Professional Metric Cards */
    div[data-testid="stMetric"] {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        border-radius: 12px;
        padding: 25px;
        box-shadow: 0 4px 15px rgba(0,0,0,0.1);
        color: white !important;
    }
    div[data-testid="stMetricValue"] { 
        font-size: 2.2rem !important; 
        color: white !important; 
        font-weight: bold; 
    }
    div[data-testid="stMetricLabel"] { 
        font-size: 0.95rem !important; 
        color: rgba(255,255,255,0.95) !important; 
    }

    /* Section Cards */
    .section-card {
        background-color: white;
        border-radius: 12px;
        padding: 25px;
        box-shadow: 0 2px 10px rgba(0,0,0,0.08);
        margin-bottom: 20px;
    }
    
    h1 { color: #2c3e50 !important; font-weight: 600; font-size: 2.5rem; }
    h2 { color: #34495e !important; font-weight: 600; font-size: 1.8rem; margin-top: 30px; }
    h3 { color: #7f8c8d !important; font-weight: 500; }
    
    .stButton>button {
        background-color: #3498db;
        color: white;
        border-radius: 6px;
        border: none;
        padding: 10px 24px;
        font-weight: 500;
    }
    
    .stChatMessage {
        background-color: white;
        border-radius: 10px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.08);
    }
    
    /* Better spacing */
    .block-container {
        padding-top: 2rem;
        padding-bottom: 2rem;
    }
</style>
""", unsafe_allow_html=True)

selected = option_menu(
    menu_title=None,
    options=["📊 Executive Overview", "💰 Advanced Analytics", "🧠 Sentiment Hub", "💬 AI Chatbot", "📊 All Data Views"],
    icons=["house", "bar-chart", "emoji-smile", "chat-dots", "database"],
    menu_icon="cast",
    default_index=0,
    orientation="horizontal",
    styles={
        "container": {"padding": "10px", "background-color": "white", "border-radius": "10px", "box-shadow": "0 2px 10px rgba(0,0,0,0.1)"},
        "icon": {"color": "#3498db", "font-size": "20px"},
        "nav-link": {"font-size": "15px", "text-align": "center", "margin":"0px", "padding": "10px 20px", "--hover-color": "#ecf0f1", "color": "#2c3e50", "font-weight": "500"},
        "nav-link-selected": {"background-color": "#3498db", "color": "white", "border-radius": "8px"},
    }
)

st.markdown("<h1 style='text-align: center; color: #2c3e50; margin: 30px 0;'>🍔 Zomato AI Analytics Platform</h1>", unsafe_allow_html=True)
st.markdown("---")

# ==============================================================================
# BLOCK 2: BACKEND CONNECTIONS
# ==============================================================================
load_dotenv()
API_KEY = os.getenv("GROQ_API_KEY")

@st.cache_resource
def init_db():
    try:
        return duckdb.connect('zomato.duckdb')
    except Exception as e:
        st.error(f"Database connection error: {e}")
        return None

@st.cache_resource
def init_ai():
    if API_KEY:
        return Groq(api_key=API_KEY)
    return None

conn = init_db()
client = init_ai()

if conn is None:
    st.error("❌ Cannot connect to database. Please make sure zomato.duckdb exists.")
    st.stop()

# ==============================================================================
# BLOCK 3: HELPER FUNCTIONS
# ==============================================================================
def get_schema():
    tables = ['staging.mart_orders', 'gold_review_sentiments', 'staging.stg_restaurants', 'raw.orders']
    schema = "Database: Zomato DuckDB\nAvailable Tables:\n"
    for table in tables:
        try:
            desc = conn.execute(f"DESCRIBE {table}").fetchall()
            schema += f"\n{table}:\n" + "\n".join([f"  - {col[0]} ({col[1]})" for col in desc])
        except: pass
    return schema

def generate_sql(question, schema):
    prompt = f"""You are an expert SQL analyst. Write a valid DuckDB SELECT query based on this schema:
{schema}
STRICT RULES:
1. Return ONLY raw SQL. No markdown.
2. ALWAYS use ORDER BY [metric] DESC, [name_column] ASC.
3. ALWAYS use ROUND(..., 2) for financial metrics.
4. If impossible, return 'SCHEMA_LIMITATION'."""
    
    for _ in range(3):
        try:
            response = client.chat.completions.create(
                model="qwen/qwen3.8-27b",
                messages=[{"role": "user", "content": prompt + f"\n\nQuestion: {question}"}],
                temperature=0.0
            )
            sql = response.choices[0].message.content.strip().replace("```sql", "").replace("```", "").strip()
            if sql.upper().startswith("SELECT"): return sql
        except: time.sleep(1)
    return "SCHEMA_LIMITATION"

def analyze_review(text):
    prompt = f"""Analyze this review and return JSON:
{{"sentiment": "Positive/Negative/Neutral", "topics": ["topic1", "topic2"], "summary": "5-word summary"}}
Review: "{text}"
"""
    try:
        response = client.chat.completions.create(model="qwen/qwen3.8-27b", messages=[{"role": "user", "content": prompt}], temperature=0.1)
        return json.loads(response.choices[0].message.content)
    except: return None

# ==============================================================================
# BLOCK 4: EXECUTIVE OVERVIEW (FIXED)
# ==============================================================================
if selected == "📊 Executive Overview":
    st.header("📊 Executive Overview Dashboard")
    st.markdown("High-level KPIs and business metrics at a glance")
    st.markdown("---")
    
    # KPI Cards with proper error handling
    col1, col2, col3, col4 = st.columns(4)
    
    try:
        total_rev = conn.execute('SELECT COALESCE(SUM(total_revenue), 0) FROM staging.mart_orders').fetchone()[0]
        col1.metric("💰 Total Revenue", f"{total_rev:,.0f}")
    except Exception as e:
        col1.metric(" Total Revenue", "N/A")
        st.warning(f"Revenue data unavailable: {e}")
        
    try:
        total_ord = conn.execute('SELECT COALESCE(SUM(total_orders), 0) FROM staging.mart_orders').fetchone()[0]
        col2.metric(" Total Orders", f"{int(total_ord):,}")
    except Exception as e:
        col2.metric("📦 Total Orders", "N/A")
        
    try:
        total_rest = conn.execute('SELECT COUNT(*) FROM staging.stg_restaurants').fetchone()[0]
        col3.metric(" Restaurants", f"{total_rest:,}")
    except Exception as e:
        col3.metric(" Restaurants", "N/A")
        
    try:
        avg_rat = conn.execute('SELECT COALESCE(ROUND(AVG(rating), 2), 0) FROM staging.stg_restaurants').fetchone()[0]
        col4.metric("⭐ Avg Rating", f"{avg_rat}/5.0")
    except Exception as e:
        col4.metric("⭐ Avg Rating", "N/A")
    
    st.markdown("---")
    
    # Charts Row 1
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("🏙️ Top 10 Cities by Revenue")
        try:
            df = conn.execute("SELECT city, total_revenue FROM staging.mart_orders ORDER BY total_revenue DESC LIMIT 10").df()
            if not df.empty:
                fig = px.bar(df, x='city', y='total_revenue', color='total_revenue', 
                            color_continuous_scale='Blues', title="Top 10 Cities by Revenue")
                fig.update_layout(plot_bgcolor='white', paper_bgcolor='white', height=400)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("No revenue data available")
        except Exception as e:
            st.error(f"Chart error: {e}")
            
    with col2:
        st.subheader("🍽️ Top 10 Restaurants by Revenue")
        try:
            df2 = conn.execute("SELECT restaurant_name, total_revenue FROM staging.mart_orders ORDER BY total_revenue DESC LIMIT 10").df()
            if not df2.empty:
                fig2 = px.bar(df2, x='total_revenue', y='restaurant_name', orientation='h', 
                             color='total_revenue', color_continuous_scale='Greens', title="Top 10 Restaurants")
                fig2.update_layout(plot_bgcolor='white', paper_bgcolor='white', height=400, yaxis={'categoryorder': 'total ascending'})
                st.plotly_chart(fig2, use_container_width=True)
            else:
                st.info("No restaurant data available")
        except Exception as e:
            st.error(f"Chart error: {e}")
    
    # Charts Row 2
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("📊 Revenue Distribution by City")
        try:
            df3 = conn.execute("SELECT city, SUM(total_revenue) as rev FROM staging.mart_orders GROUP BY city ORDER BY rev DESC LIMIT 10").df()
            if not df3.empty:
                fig3 = px.pie(df3, values='rev', names='city', title="Revenue Share by Top 10 Cities", hole=0.4)
                fig3.update_layout(plot_bgcolor='white', paper_bgcolor='white', height=400)
                st.plotly_chart(fig3, use_container_width=True)
        except Exception as e:
            st.error(f"Chart error: {e}")
            
    with col2:
        st.subheader("📈 Orders vs Revenue Correlation")
        try:
            df4 = conn.execute("SELECT city, total_orders, total_revenue FROM staging.mart_orders WHERE total_revenue > 0 ORDER BY total_revenue DESC LIMIT 20").df()
            if not df4.empty:
                fig4 = px.scatter(df4, x='total_orders', y='total_revenue', size='total_revenue',
                                 hover_name='city', title="Orders vs Revenue by City",
                                 color='total_revenue', color_continuous_scale='Viridis')
                fig4.update_layout(plot_bgcolor='white', paper_bgcolor='white', height=400)
                st.plotly_chart(fig4, use_container_width=True)
        except Exception as e:
            st.error(f"Chart error: {e}")
    
    # Data Table
    st.markdown("---")
    st.subheader(" Detailed Restaurant Performance")
    try:
        df_table = conn.execute("""
            SELECT restaurant_name, city, total_orders, 
                   ROUND(total_revenue, 2) as revenue,
                   ROUND(total_revenue/NULLIF(total_orders, 0), 2) as avg_order_value
            FROM staging.mart_orders 
            ORDER BY total_revenue DESC 
            LIMIT 20
        """).df()
        if not df_table.empty:
            st.dataframe(df_table, use_container_width=True, height=350)
        else:
            st.info("No detailed data available")
    except Exception as e:
        st.error(f"Table error: {e}")

# ==============================================================================
# BLOCK 5: ADVANCED ANALYTICS WITH MONTHLY/YEARLY (FIXED)
# ==============================================================================
elif selected == "💰 Advanced Analytics":
    st.header("💰 Advanced Revenue Analytics")
    st.markdown("Deep dive analytics with time-based analysis and filters")
    st.markdown("---")
    
    # Time period selector
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        time_period = st.selectbox("Time Period", ["All Time", "Monthly", "Yearly"])
    with col2:
        min_revenue = st.number_input("Min Revenue (₹)", value=0, step=50000)
    with col3:
        sort_option = st.selectbox("Sort By", ["Revenue", "Orders", "City Name"])
    with col4:
        limit_val = st.slider("Records", 10, 100, 20)
    
    # Build query based on time period
    sort_col = "total_revenue" if sort_option == "Revenue" else ("total_orders" if sort_option == "Orders" else "city")
    
    try:
        if time_period == "Monthly":
            # Monthly breakdown from raw orders
            query = f"""
                SELECT 
                    strftime('%Y-%m', CAST(order_date AS DATE)) as month,
                    COUNT(*) as total_orders,
                    ROUND(SUM(CAST(sales_amount AS FLOAT)), 2) as total_revenue,
                    ROUND(AVG(CAST(sales_amount AS FLOAT)), 2) as avg_order_value
                FROM raw.orders
                WHERE order_date IS NOT NULL AND sales_amount IS NOT NULL
                GROUP BY strftime('%Y-%m', CAST(order_date AS DATE))
                ORDER BY month DESC
                LIMIT {limit_val}
            """
            df = conn.execute(query).df()
            
            if not df.empty:
                # Metrics
                col1, col2, col3 = st.columns(3)
                col1.metric("Total Revenue (Selected)", f"₹{df['total_revenue'].sum():,.0f}")
                col2.metric("Total Orders (Selected)", f"{df['total_orders'].sum():,}")
                col3.metric("Avg Order Value", f"₹{df['avg_order_value'].mean():.2f}")
                
                st.markdown("---")
                
                # Monthly trend chart
                st.subheader("📈 Monthly Revenue Trend")
                fig_trend = px.line(df, x='month', y='total_revenue', 
                                   title="Monthly Revenue Trend",
                                   markers=True,
                                   color_discrete_sequence=['#3498db'])
                fig_trend.update_layout(plot_bgcolor='white', paper_bgcolor='white', height=400)
                st.plotly_chart(fig_trend, use_container_width=True)
                
                # Monthly orders chart
                st.subheader(" Monthly Orders Trend")
                fig_orders = px.bar(df, x='month', y='total_orders',
                                   title="Monthly Orders Volume",
                                   color='total_orders',
                                   color_continuous_scale='Blues')
                fig_orders.update_layout(plot_bgcolor='white', paper_bgcolor='white', height=400)
                st.plotly_chart(fig_orders, use_container_width=True)
                
                # Data table
                st.subheader("📊 Monthly Breakdown")
                st.dataframe(df, use_container_width=True)
            else:
                st.warning("No monthly data available")
                
        elif time_period == "Yearly":
            # Yearly breakdown from raw orders
            query = f"""
                SELECT 
                    strftime('%Y', CAST(order_date AS DATE)) as year,
                    COUNT(*) as total_orders,
                    ROUND(SUM(CAST(sales_amount AS FLOAT)), 2) as total_revenue,
                    ROUND(AVG(CAST(sales_amount AS FLOAT)), 2) as avg_order_value
                FROM raw.orders
                WHERE order_date IS NOT NULL AND sales_amount IS NOT NULL
                GROUP BY strftime('%Y', CAST(order_date AS DATE))
                ORDER BY year DESC
                LIMIT {limit_val}
            """
            df = conn.execute(query).df()
            
            if not df.empty:
                # Metrics
                col1, col2, col3 = st.columns(3)
                col1.metric("Total Revenue (Selected)", f"₹{df['total_revenue'].sum():,.0f}")
                col2.metric("Total Orders (Selected)", f"{df['total_orders'].sum():,}")
                col3.metric("Avg Order Value", f"₹{df['avg_order_value'].mean():.2f}")
                
                st.markdown("---")
                
                # Yearly trend chart
                st.subheader("📈 Yearly Revenue Trend")
                fig_trend = px.line(df, x='year', y='total_revenue', 
                                   title="Yearly Revenue Trend",
                                   markers=True,
                                   color_discrete_sequence=['#e74c3c'])
                fig_trend.update_layout(plot_bgcolor='white', paper_bgcolor='white', height=400)
                st.plotly_chart(fig_trend, use_container_width=True)
                
                # Yearly orders chart
                st.subheader(" Yearly Orders Trend")
                fig_orders = px.bar(df, x='year', y='total_orders',
                                   title="Yearly Orders Volume",
                                   color='total_orders',
                                   color_continuous_scale='Greens')
                fig_orders.update_layout(plot_bgcolor='white', paper_bgcolor='white', height=400)
                st.plotly_chart(fig_orders, use_container_width=True)
                
                # Data table
                st.subheader("📊 Yearly Breakdown")
                st.dataframe(df, use_container_width=True)
            else:
                st.warning("No yearly data available")
                
        else:
            # All Time - Restaurant level data
            query = f"""
                SELECT 
                    restaurant_name,
                    city,
                    total_orders,
                    total_revenue,
                    ROUND(total_revenue/NULLIF(total_orders, 0), 2) as avg_order_value
                FROM staging.mart_orders 
                WHERE total_revenue >= {min_revenue}
                ORDER BY {sort_col} DESC
                LIMIT {limit_val}
            """
            df = conn.execute(query).df()
            
            if not df.empty:
                # Metrics
                col1, col2, col3 = st.columns(3)
                col1.metric("Total Revenue", f"₹{df['total_revenue'].sum():,.0f}")
                col2.metric("Total Orders", f"{df['total_orders'].sum():,}")
                col3.metric("Avg Order Value", f"₹{df['avg_order_value'].mean():.2f}")
                
                st.markdown("---")
                
                # Data table
                st.subheader("📊 Detailed Data")
                st.dataframe(df, use_container_width=True)
                
                # Visualizations
                col1, col2 = st.columns(2)
                with col1:
                    fig = px.bar(df, x='city', y='total_revenue', color='total_revenue', 
                                title="Revenue by City", color_continuous_scale='Reds')
                    fig.update_layout(plot_bgcolor='white', paper_bgcolor='white', height=400)
                    st.plotly_chart(fig, use_container_width=True)
                with col2:
                    fig2 = px.scatter(df, x='total_orders', y='total_revenue', size='total_revenue',
                                     hover_name='restaurant_name', title="Orders vs Revenue",
                                     color='city')
                    fig2.update_layout(plot_bgcolor='white', paper_bgcolor='white', height=400)
                    st.plotly_chart(fig2, use_container_width=True)
            else:
                st.warning("No data found with current filters")
                
    except Exception as e:
        st.error(f"❌ Analytics Error: {e}")
        st.info("Make sure the required tables exist and have data")

# ==============================================================================
# BLOCK 6: SENTIMENT HUB
# ==============================================================================
elif selected == "🧠 Sentiment Hub":
    st.header("🧠 AI Sentiment Analysis Hub")
    st.markdown("Analyze customer reviews with AI and extract insights")
    st.markdown("---")
    
    try:
        df_sent = conn.execute("SELECT sentiment, COUNT(*) as count FROM gold_review_sentiments GROUP BY sentiment").df()
        
        if df_sent.empty:
            st.warning("⚠️ No sentiment data found. Run: `python enrich_reviews.py`")
            raw_count = conn.execute("SELECT COUNT(*) FROM raw.reviews").fetchone()[0]
            st.metric("Available Raw Reviews", f"{raw_count:,}")
        else:
            tab1, tab2 = st.tabs(["📊 Dashboard", "🔍 Topic Analysis"])
            
            with tab1:
                col1, col2, col3 = st.columns(3)
                pos = df_sent[df_sent['sentiment']=='Positive']['count'].sum() if 'Positive' in df_sent['sentiment'].values else 0
                neg = df_sent[df_sent['sentiment']=='Negative']['count'].sum() if 'Negative' in df_sent['sentiment'].values else 0
                neu = df_sent[df_sent['sentiment']=='Neutral']['count'].sum() if 'Neutral' in df_sent['sentiment'].values else 0
                
                col1.metric("😊 Positive", f"{pos:,}")
                col2.metric(" Negative", f"{neg:,}")
                col3.metric("😐 Neutral", f"{neu:,}")
                
                col1, col2 = st.columns(2)
                with col1:
                    fig = px.pie(df_sent, values='count', names='sentiment', title="Sentiment Distribution",
                                color_discrete_map={'Positive':'#2ecc71', 'Negative':'#e74c3c', 'Neutral':'#f39c12'})
                    fig.update_layout(plot_bgcolor='white', paper_bgcolor='white', height=400)
                    st.plotly_chart(fig, use_container_width=True)
                with col2:
                    st.subheader("📋 Recent Reviews")
                    df_reviews = conn.execute("SELECT original_text, sentiment, summary FROM gold_review_sentiments LIMIT 10").df()
                    st.dataframe(df_reviews, use_container_width=True, height=400)
            
            with tab2:
                df_topics = conn.execute("SELECT topics FROM gold_review_sentiments").df()
                all_topics = []
                for topics_str in df_topics['topics']:
                    if topics_str and pd.notna(topics_str):
                        all_topics.extend([t.strip() for t in str(topics_str).split(',') if t.strip()])
                
                if all_topics:
                    topic_counts = pd.Series(all_topics).value_counts().head(15).reset_index()
                    topic_counts.columns = ['Topic', 'Count']
                    fig = px.bar(topic_counts, x='Count', y='Topic', orientation='h', color='Count',
                                title="Top Topics", color_continuous_scale='Viridis')
                    fig.update_layout(plot_bgcolor='white', paper_bgcolor='white', height=500)
                    st.plotly_chart(fig, use_container_width=True)
    except Exception as e:
        st.error(f"Error: {e}")

# ==============================================================================
# BLOCK 7: AI CHATBOT
# ==============================================================================
elif selected == "💬 AI Chatbot":
    st.header("💬 AI Text-to-SQL Chatbot")
    st.markdown("Ask questions in plain English. The AI will write and execute SQL queries.")
    st.markdown("---")
    
    if "messages" not in st.session_state:
        st.session_state.messages = [{"role": "assistant", "content": "Hello! I'm your AI Data Analyst. Ask me anything! Try:\n• 'Show top 5 cities by revenue'\n• 'How many total restaurants are there?'\n• 'What is the average order value?'"}]
    
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]): st.markdown(msg["content"])
    
    if prompt := st.chat_input("Ask a question about the data..."):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"): st.markdown(prompt)
        
        with st.chat_message("assistant"):
            with st.spinner(" AI is writing SQL..."):
                sql = generate_sql(prompt, get_schema())
                if sql == "SCHEMA_LIMITATION":
                    st.warning("❌ I cannot answer this with the current schema.")
                else:
                    st.markdown("** Generated SQL Query:**")
                    st.code(sql, language="sql")
                    try:
                        res = conn.execute(sql).df()
                        st.markdown("**📊 Results:**")
                        st.dataframe(res, use_container_width=True)
                        st.session_state.messages.append({"role": "assistant", "content": f"Here are the results. Found {len(res)} rows."})
                    except Exception as e:
                        st.error(f"❌ Database Error: {e}")

# ==============================================================================
# BLOCK 8: ALL DATA VIEWS (FIXED FILTERING)
# ==============================================================================
elif selected == "📊 All Data Views":
    st.header("📊 Complete Data Explorer")
    st.markdown("View all orders, restaurants, and reviews with advanced filtering")
    st.markdown("---")
    
    tab1, tab2, tab3 = st.tabs(["🏪 All Restaurants", "📦 All Orders", "💬 All Reviews"])
    
    with tab1:
        st.subheader("Complete Restaurant Database")
        try:
            df_rest = conn.execute("""
                SELECT 
                    restaurant_name,
                    city,
                    rating,
                    cuisine,
                    cost_for_two
                FROM staging.stg_restaurants
                ORDER BY rating DESC
            """).df()
            
            col1, col2 = st.columns(2)
            with col1:
                search = st.text_input("🔍 Search Restaurant")
            with col2:
                min_rating = st.slider("Min Rating", 0.0, 5.0, 0.0, step=0.1)
            
            # Apply filters
            filtered_df = df_rest.copy()
            if search:
                filtered_df = filtered_df[filtered_df['restaurant_name'].str.contains(search, case=False, na=False)]
            if min_rating > 0:
                filtered_df = filtered_df[filtered_df['rating'] >= min_rating]
            
            # Show filtered count
            st.metric("Total Restaurants (Filtered)", f"{len(filtered_df):,}")
            st.dataframe(filtered_df, use_container_width=True, height=500)
            
        except Exception as e:
            st.error(f"Error loading restaurants: {e}")
    
    with tab2:
        st.subheader("Complete Orders Database")
        try:
            df_orders = conn.execute("""
                SELECT 
                    restaurant_name,
                    city,
                    total_orders,
                    total_revenue,
                    ROUND(total_revenue/NULLIF(total_orders, 0), 2) as avg_order_value
                FROM staging.mart_orders
                ORDER BY total_revenue DESC
            """).df()
            
            col1, col2, col3 = st.columns(3)
            with col1:
                city_filter = st.multiselect("Filter by City", df_orders['city'].unique())
            with col2:
                min_ord = st.number_input("Min Orders", value=0)
            with col3:
                min_rev = st.number_input("Min Revenue", value=0)
            
            # Apply filters
            filtered_df = df_orders.copy()
            if city_filter:
                filtered_df = filtered_df[filtered_df['city'].isin(city_filter)]
            filtered_df = filtered_df[(filtered_df['total_orders'] >= min_ord) & (filtered_df['total_revenue'] >= min_rev)]
            
            st.metric("Total Records (Filtered)", f"{len(filtered_df):,}")
            st.dataframe(filtered_df, use_container_width=True, height=500)
            
        except Exception as e:
            st.error(f"Error loading orders: {e}")
    
    with tab3:
        st.subheader("All Customer Reviews")
        try:
            try:
                df_reviews = conn.execute("""
                    SELECT 
                        review_id,
                        original_text as review,
                        sentiment,
                        topics,
                        summary
                    FROM gold_review_sentiments
                    ORDER BY review_id
                """).df()
                st.markdown("✅ Showing AI-enriched reviews")
            except:
                df_reviews = conn.execute("""
                    SELECT 
                        review_id,
                        comment as review,
                        rating
                    FROM raw.reviews
                    ORDER BY review_id
                    LIMIT 1000
                """).df()
                st.markdown("📄 Showing raw reviews (AI enrichment not available)")
            
            st.metric("Total Reviews", f"{len(df_reviews):,}")
            st.dataframe(df_reviews, use_container_width=True, height=500)
            
        except Exception as e:
            st.error(f"Error loading reviews: {e}")