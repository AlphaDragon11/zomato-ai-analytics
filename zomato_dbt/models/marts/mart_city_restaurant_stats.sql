-- This config tells dbt to build this as a real TABLE (not just a view)
-- because we will query it a lot for dashboards and AI.
{{ config(materialized='table') }}

SELECT
    city,
    COUNT(restaurant_id) AS total_restaurants,
    ROUND(AVG(rating), 2) AS avg_rating,
    ROUND(AVG(cost_for_two), 2) AS avg_cost_for_two

-- MAGIC HAPPENS HERE: 
-- This tells dbt: "Wait, build stg_restaurants FIRST, then use its output here!"
FROM {{ ref('stg_restaurants') }}

WHERE city IS NOT NULL
GROUP BY city
ORDER BY total_restaurants DESC