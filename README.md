# 🍔 Zomato Food Delivery Data Pipeline & AI Analytics Platform

An end-to-end, AI-powered Data Engineering and Analytics platform that transforms raw food delivery data into actionable business insights. This project demonstrates a Modern Data Stack architecture, Medallion data modeling, and LLM integration for advanced analytics.

## 🚀 Live Demo
[Link to your Render/Streamlit Cloud deployment here]

## 🏗️ Architecture & Tech Stack
* **Data Ingestion & Storage:** AWS S3 (Concept), Snowflake (Concept), DuckDB (Local Execution)
* **Data Transformation:** dbt (Medallion Architecture: Bronze ➡️ Silver ➡️ Gold)
* **Orchestration:** Apache Airflow (Conceptual DAGs)
* **AI & LLM Integration:** Groq API (Qwen 3.8B), Python
* **Frontend & BI:** Streamlit, Plotly
* **Deployment:** Docker, Render

## 🧠 Key Features
1. **Medallion Data Architecture:** Raw data is ingested into Bronze, cleaned in Silver (`stg_`), and aggregated into Gold business marts (`mart_`) using dbt.
2. **LLM Review Enrichment:** An automated pipeline that uses an LLM to read unstructured customer reviews and extract structured Sentiment, Topics, and Summaries.
3. **Text-to-SQL AI Chatbot:** A conversational interface where stakeholders can ask plain-English questions (e.g., "Show top 3 cities by revenue"), and the AI dynamically writes and executes safe SQL queries.
4. **Interactive BI Dashboard:** A professional, Power BI-style Streamlit dashboard featuring KPI tracking, time-series analysis (Monthly/Yearly), and deep-dive data exploration.

##  Project Structure
```text
zomato-ai-project/
├── app.py                  # Main Streamlit Dashboard
├── enrich_reviews.py       # AI Enrichment Pipeline
├── text_to_sql.py          # Text-to-SQL Engine
── zomato_dbt/             # dbt Data Transformation Models
│   ├── models/staging/     # Silver Layer
│   └── models/marts/       # Gold Layer
├── data/                   # Raw CSV Datasets
├── Dockerfile              # Containerization config
── requirements.txt        # Python dependencies