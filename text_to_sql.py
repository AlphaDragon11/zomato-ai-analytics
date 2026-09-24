import os
import re
import time
import logging
import duckdb
from groq import Groq
from dotenv import load_dotenv

# ==========================================
# PART 1: PROFESSIONAL LOGGING SETUP
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("Zomato_TextToSQL")

# ==========================================
# PART 2: INITIALIZATION & CONNECTIONS
# ==========================================
load_dotenv()
API_KEY = os.getenv("GROQ_API_KEY")
if not API_KEY:
    logger.error("CRITICAL: GROQ_API_KEY not found in .env file. Exiting.")
    exit(1)

client = Groq(api_key=API_KEY)
conn = duckdb.connect('zomato.duckdb')

# ==========================================
# PART 3: DYNAMIC SCHEMA EXTRACTION (Resume Booster!)
# ==========================================
def get_database_schema() -> str:
    """Dynamically fetches the schema of our Gold/Staging tables to give the AI perfect context."""
    logger.info("🔍 Dynamically fetching database schema...")
    
    # We only document the tables relevant to business questions
    tables_to_document = ['staging.mart_orders', 'gold_review_sentiments', 'staging.stg_restaurants']
    schema_info = "Database: Zomato DuckDB\nAvailable Tables and Columns:\n"
    
    for table in tables_to_document:
        try:
            # DESCRIBE gets the exact column names and data types directly from DuckDB
            desc_result = conn.execute(f"DESCRIBE {table}").fetchall()
            schema_info += f"\nTable: {table}\n"
            for col in desc_result:
                schema_info += f"  - {col[0]} ({col[1]})\n"
        except Exception as e:
            logger.warning(f"Could not fetch schema for {table}: {e}")
            
    return schema_info.strip()

# ==========================================
# PART 4: AI SQL GENERATION WITH SAFETY & RETRY
# ==========================================
def generate_sql_query(user_question: str, schema: str, max_retries: int = 3) -> str:
    """Sends the prompt to the LLM to generate a safe, valid SELECT query."""
    
    system_prompt = f"""
    You are an expert Data Engineer and SQL Analyst. 
    Your task is to write a precise, valid DuckDB SQL query to answer the user's question based *only* on the provided schema.
    
    Schema:
    {schema}
    
    STRICT RULES:
    1. Return ONLY the raw SQL query. Do NOT include markdown formatting (like ```sql), explanations, or any other text.
    2. The query MUST be a SELECT statement. NEVER generate DROP, DELETE, UPDATE, or INSERT statements.
    3. Use proper DuckDB syntax (e.g., use ILIKE for case-insensitive string matching).
    4. If the question cannot be answered with the provided schema, return exactly: "SCHEMA_LIMITATION"
    """
    
    prompt = f"User Question: {user_question}"
    
    for attempt in range(max_retries):
        try:
            completion = client.chat.completions.create(
                model="qwen/qwen3.8-27b",
                messages=[{"role": "user", "content": system_prompt + "\n\n" + prompt}],
                temperature=0.0 # Zero temperature for deterministic, factual code generation
            )
            
            raw_sql = completion.choices[0].message.content.strip()
            # Clean up any accidental markdown backticks the AI might add
            clean_sql = raw_sql.replace("```sql", "").replace("```", "").strip()
            
            # 🛡️ SAFETY CHECK: Ensure it's strictly a SELECT statement
            if not clean_sql.upper().startswith("SELECT"):
                logger.warning(f"AI generated non-SELECT query on attempt {attempt + 1}. Retrying...")
                continue
                
            return clean_sql
            
        except Exception as e:
            logger.warning(f"API Attempt {attempt + 1} failed: {e}")
            time.sleep(2) # Exponential backoff
            
    logger.error("Failed to generate valid SQL query after multiple attempts.")
    return "SCHEMA_LIMITATION"

# ==========================================
# PART 5: SAFE QUERY EXECUTION
# ==========================================
def execute_query_safely(sql_query: str):
    """Executes the generated SQL query and returns the structured results."""
    if sql_query == "SCHEMA_LIMITATION":
        logger.info("🤖 AI Response: I cannot answer this question with the current database schema.")
        return None
        
    logger.info(f"⏳ Executing AI-generated query:\n{sql_query}")
    
    try:
        cursor = conn.execute(sql_query)
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        
        logger.info("✅ Query executed successfully!")
        return {"columns": columns, "rows": rows}
        
    except duckdb.Error as e:
        logger.error(f"❌ Database Execution Error: {e}")
        return None

# ==========================================
# PART 6: MAIN EXECUTION & TEST CASES
# ==========================================
def main():
    logger.info("="*60)
    logger.info("🚀 ZOMATO ENTERPRISE TEXT-TO-SQL ENGINE")
    logger.info("="*60)
    
    # 1. Get live schema
    schema_context = get_database_schema()
    
    # 2. Define Test Questions (Simulating real user inputs)
    test_questions = [
        "What is the total revenue and total number of orders for restaurants in Bangalore?",
        "How many reviews have a Negative sentiment and mention Delivery in the topics?",
        "Show me the top 3 cities by average restaurant rating."
    ]
    
    # 3. Process each question
    for i, question in enumerate(test_questions, 1):
        logger.info(f"\n--- Test Case {i} ---")
        logger.info(f"👤 User Question: '{question}'")
        
        # Generate SQL
        sql = generate_sql_query(question, schema_context)
        
        # Execute and display
        result = execute_query_safely(sql)
        
        if result:
            logger.info("📊 Results:")
            logger.info(f"Columns: {result['columns']}")
            for row in result['rows']:
                logger.info(f"  {row}")
                
    logger.info("\n" + "="*60)
    logger.info("🎉 TEXT-TO-SQL ENGINE TEST COMPLETE")
    logger.info("="*60)
    conn.close()

if __name__ == "__main__":
    main()