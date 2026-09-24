{{ config(materialized='table') }}

SELECT
    r.restaurant_name,
    r.city,
    COUNT(o.order_id) AS total_orders,
    SUM(o.order_amount) AS total_revenue

-- MAGIC: We join the clean orders with the clean restaurants using the restaurant_id!
FROM {{ ref('stg_orders') }} o
JOIN {{ ref('stg_restaurants') }} r 
    ON o.restaurant_id = r.restaurant_id

GROUP BY 
    r.restaurant_name, 
    r.city
ORDER BY 
    total_revenue DESC