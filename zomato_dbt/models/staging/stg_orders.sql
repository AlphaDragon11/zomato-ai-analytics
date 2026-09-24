-- Step 1: Grab the raw orders data
WITH raw_orders AS (
    SELECT * FROM raw.orders
),

-- Step 2: Cast the text columns into proper numbers and dates using temporary names
-- FIX: We use 'r_id' and 'sales_amount' because that's what the raw CSV actually has!
cleaned_orders AS (
    SELECT
        CAST(order_id AS INTEGER) AS order_id_temp,
        CAST(r_id AS INTEGER) AS restaurant_id_temp,
        CAST(user_id AS INTEGER) AS user_id_temp,
        CAST(order_date AS DATE) AS order_date,
        order_status,
        CAST(sales_amount AS FLOAT) AS order_amount_temp
    FROM raw_orders
)

-- Step 3: Select from our cleaned data and give them their final business names!
SELECT
    order_id_temp AS order_id,
    restaurant_id_temp AS restaurant_id,
    user_id_temp AS user_id,
    order_date,
    order_status,
    order_amount_temp AS order_amount
FROM cleaned_orders