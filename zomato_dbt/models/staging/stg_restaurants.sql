-- Step 1: Grab the raw data from our Bronze layer
WITH raw_data AS (
    SELECT * FROM raw.restaurants
)

-- Step 2: Clean the data using the EXACT column names from your CSV
SELECT
    CAST(id AS INTEGER) AS restaurant_id,
    name AS restaurant_name,
    city,
    
    -- 1. Clean the rating
    CASE 
        WHEN rating = '--' THEN NULL 
        ELSE CAST(rating AS FLOAT) 
    END AS rating,
    
    -- 2. Clean the cost (Raw column is 'cost', not 'cost_for_two')
    CAST(REPLACE(cost, '₹ ', '') AS INTEGER) AS cost_for_two,
    
    -- 3. Select the cuisine (Raw column is 'cuisine', not 'cuisines')
    cuisine

FROM raw_data