import os
import json
import time
import logging
import duckdb
from groq import Groq
from dotenv import load_dotenv
from tqdm import tqdm

# ==========================================
# PART 1: PROFESSIONAL LOGGING SETUP
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("ZomatoAI_Pipeline")

# ==========================================
# PART 2: INITIALIZATION & CONNECTIONS
# ==========================================
logger.info("🚀 Starting Production AI Enrichment Pipeline...")
load_dotenv()

API_KEY = os.getenv("GROQ_API_KEY")
if not API_KEY:
    logger.error("CRITICAL: GROQ_API_KEY not found in .env file. Exiting.")
    exit(1)

client = Groq(api_key=API_KEY)
conn = duckdb.connect('zomato.duckdb')

# ==========================================
# PART 3: SCHEMA SETUP & IDEMPOTENCY
# ==========================================
conn.execute("""
    CREATE TABLE IF NOT EXISTS gold_review_sentiments (
        review_id INTEGER PRIMARY KEY,
        original_text VARCHAR,
        sentiment VARCHAR,
        topics VARCHAR,
        summary VARCHAR,
        processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
""")

processed_ids = [row[0] for row in conn.execute("SELECT review_id FROM gold_review_sentiments").fetchall()]
logger.info(f"Found {len(processed_ids)} previously processed reviews. Skipping them.")

# ==========================================
# PART 4: DYNAMIC DATA FETCHING (FIXED!)
# ==========================================
logger.info("🔍 Fetching raw reviews from Bronze layer...")
description = conn.execute("SELECT * FROM raw.reviews LIMIT 1").description
columns = [desc[0] for desc in description]

# FIX: We now specifically look for 'comment' or 'review_text'
if 'comment' in columns:
    text_col = 'comment'
elif 'review_text' in columns:
    text_col = 'review_text'
else:
    text_col = columns[5] # Fallback to 6th column if names change

text_idx = columns.index(text_col)
id_idx = columns.index('review_id') if 'review_id' in columns else 0

all_reviews = conn.execute(f"SELECT * FROM raw.reviews LIMIT 100").fetchall()
reviews_to_process = [row for row in all_reviews if row[id_idx] not in processed_ids]
logger.info(f"Ready to process {len(reviews_to_process)} new reviews using column: '{text_col}'.")

# ==========================================
# PART 5: ADVANCED PROMPT ENGINEERING
# ==========================================
SYSTEM_PROMPT = """
You are an expert Data Analyst for a food delivery app. 
Your task is to analyze customer reviews and extract structured JSON data.

Rules:
1. Sentiment must be strictly "Positive", "Negative", or "Neutral".
2. Topics must be a comma-separated string (e.g., "Food Quality, Delivery, Packaging").
3. Summary must be under 8 words.
4. Return ONLY valid JSON. No markdown, no explanations.

Example Input: "The pizza was cold and the driver was rude."
Example Output: {"sentiment": "Negative", "topics": "Food Quality, Service", "summary": "Cold pizza, rude driver."}
"""

def get_ai_analysis(text):
    prompt = f"{SYSTEM_PROMPT}\n\nReview: \"{text}\""
    
    for attempt in range(3):
        try:
            completion = client.chat.completions.create(
                model="qwen/qwen3.8-27b",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1
            )
            raw_response = completion.choices[0].message.content
            clean_response = raw_response.replace("```json", "").replace("```", "").strip()
            return json.loads(clean_response)
            
        except Exception as e:
            logger.warning(f"API Attempt {attempt + 1} failed: {e}")
            time.sleep(2)
            
    logger.error("Failed to get AI response after 3 attempts.")
    return None

# ==========================================
# PART 6: THE EXECUTION LOOP
# ==========================================
success_count = 0
error_count = 0

for row in tqdm(reviews_to_process, desc="Processing Reviews"):
    review_id = row[id_idx]
    review_text = row[text_idx]
    
    if not review_text or len(str(review_text)) < 15:
        continue

    ai_data = get_ai_analysis(str(review_text))

    if ai_data:
        try:
            sentiment = ai_data.get('sentiment', 'Neutral')
            topics = ai_data.get('topics', 'General')
            summary = ai_data.get('summary', 'No summary')

            conn.execute("""
                INSERT INTO gold_review_sentiments (review_id, original_text, sentiment, topics, summary)
                VALUES (?, ?, ?, ?, ?)
            """, [review_id, str(review_text), sentiment, topics, summary])
            
            success_count += 1
        except Exception as e:
            logger.error(f"Database error for review {review_id}: {e}")
            error_count += 1
    else:
        error_count += 1

# ==========================================
# PART 7: FINAL REPORT
# ==========================================
conn.close()
logger.info("="*50)
logger.info(f"🎉 PIPELINE COMPLETE!")
logger.info(f"Successfully enriched: {success_count}")
logger.info(f"Errors/Skipped: {error_count}")
logger.info("="*50)