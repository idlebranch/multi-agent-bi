-- Physical semantic tables preserve the definitions used by the frozen SQLite build.

CREATE TABLE order_financials AS
WITH item_totals AS (
    SELECT order_id,
           COUNT(*) AS item_count,
           ROUND(SUM(price), 2) AS item_value,
           ROUND(SUM(freight_value), 2) AS freight_value
    FROM order_items
    GROUP BY order_id
),
payment_totals AS (
    SELECT order_id,
           COUNT(*) AS payment_count,
           ROUND(SUM(payment_value), 2) AS payment_value
    FROM payments
    GROUP BY order_id
)
SELECT o.order_id,
       o.customer_id,
       c.customer_unique_id,
       c.state AS customer_state,
       o.status,
       o.purchase_timestamp,
       COALESCE(i.item_count, 0) AS item_count,
       COALESCE(i.item_value, 0::NUMERIC) AS item_value,
       COALESCE(i.freight_value, 0::NUMERIC) AS freight_value,
       COALESCE(p.payment_count, 0) AS payment_count,
       COALESCE(p.payment_value, 0::NUMERIC) AS payment_value
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id
LEFT JOIN item_totals i ON i.order_id = o.order_id
LEFT JOIN payment_totals p ON p.order_id = o.order_id;

CREATE TABLE order_delivery_metrics AS
SELECT o.order_id,
       o.customer_id,
       c.customer_unique_id,
       c.state AS customer_state,
       o.purchase_timestamp,
       o.delivered_customer_timestamp,
       o.estimated_delivery_timestamp,
       ROUND(
           EXTRACT(EPOCH FROM (o.delivered_customer_timestamp - o.purchase_timestamp))
           / 86400.0,
           2
       ) AS delivery_days,
       ROUND(
           EXTRACT(EPOCH FROM (
               o.delivered_customer_timestamp - o.estimated_delivery_timestamp
           )) / 86400.0,
           2
       ) AS days_vs_estimate,
       CASE
           WHEN o.delivered_customer_timestamp IS NULL
                OR o.estimated_delivery_timestamp IS NULL THEN NULL
           WHEN o.delivered_customer_timestamp <= o.estimated_delivery_timestamp THEN 1
           ELSE 0
       END::SMALLINT AS delivered_on_time
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id
WHERE o.status = 'delivered';

CREATE TABLE product_sales AS
SELECT oi.order_id,
       oi.order_item_id,
       o.purchase_timestamp,
       o.status AS order_status,
       c.state AS customer_state,
       oi.product_id,
       COALESCE(t.category_name_english, p.category_name, 'unknown') AS category_name,
       oi.seller_id,
       s.state AS seller_state,
       oi.price,
       oi.freight_value
FROM order_items oi
JOIN orders o ON o.order_id = oi.order_id
JOIN customers c ON c.customer_id = o.customer_id
JOIN products p ON p.product_id = oi.product_id
JOIN sellers s ON s.seller_id = oi.seller_id
LEFT JOIN category_translations t ON t.category_name = p.category_name;

CREATE TABLE category_sales_summary AS
SELECT category_name,
       COUNT(*) AS item_count,
       COUNT(DISTINCT order_id) AS order_count,
       ROUND(SUM(price), 2) AS delivered_gmv,
       ROUND(SUM(freight_value), 2) AS freight_value
FROM product_sales
WHERE order_status = 'delivered'
GROUP BY category_name;

CREATE TABLE delivery_kpis AS
SELECT COUNT(*) AS delivered_orders,
       COUNT(delivered_on_time) AS measured_delivery_orders,
       SUM(delivered_on_time) AS on_time_orders,
       ROUND(AVG(delivered_on_time::NUMERIC) * 100, 4) AS on_time_delivery_pct,
       ROUND(AVG(delivery_days), 2) AS average_delivery_days
FROM order_delivery_metrics;

CREATE TABLE payment_type_summary AS
SELECT COALESCE(payment_type, 'unknown') AS payment_type,
       COUNT(*) AS payment_count,
       COUNT(DISTINCT order_id) AS order_count,
       ROUND(SUM(payment_value), 2) AS payment_value
FROM payments
GROUP BY COALESCE(payment_type, 'unknown');

CREATE TABLE customer_order_summary AS
SELECT customer_unique_id,
       COUNT(*) AS order_count,
       SUM(CASE WHEN status = 'delivered' THEN 1 ELSE 0 END) AS delivered_order_count,
       ROUND(SUM(item_value), 2) AS all_order_item_value,
       ROUND(SUM(CASE WHEN status = 'delivered' THEN item_value ELSE 0 END), 2)
           AS delivered_gmv
FROM order_financials
GROUP BY customer_unique_id;

CREATE UNIQUE INDEX idx_order_financials_order ON order_financials(order_id);
CREATE INDEX idx_order_financials_status ON order_financials(status);
CREATE INDEX idx_order_financials_purchase ON order_financials(purchase_timestamp);
CREATE INDEX idx_order_financials_customer ON order_financials(customer_unique_id);

CREATE UNIQUE INDEX idx_delivery_metrics_order ON order_delivery_metrics(order_id);
CREATE INDEX idx_delivery_metrics_state_time
    ON order_delivery_metrics(customer_state, delivered_on_time);
CREATE INDEX idx_delivery_metrics_purchase
    ON order_delivery_metrics(purchase_timestamp);

CREATE UNIQUE INDEX idx_product_sales_item ON product_sales(order_id, order_item_id);
CREATE INDEX idx_product_sales_status_purchase
    ON product_sales(order_status, purchase_timestamp);
CREATE INDEX idx_product_sales_status_category
    ON product_sales(order_status, category_name);
CREATE INDEX idx_product_sales_status_seller_state
    ON product_sales(order_status, seller_state);
CREATE INDEX idx_product_sales_status_product
    ON product_sales(order_status, product_id);
CREATE INDEX idx_product_sales_state_pair
    ON product_sales(order_status, customer_state, seller_state);

CREATE UNIQUE INDEX idx_category_summary_name ON category_sales_summary(category_name);
CREATE INDEX idx_category_summary_gmv ON category_sales_summary(delivered_gmv DESC);
CREATE UNIQUE INDEX idx_payment_summary_type ON payment_type_summary(payment_type);
CREATE UNIQUE INDEX idx_customer_summary_customer
    ON customer_order_summary(customer_unique_id);
CREATE INDEX idx_customer_summary_orders ON customer_order_summary(order_count);
CREATE INDEX idx_customer_summary_delivered
    ON customer_order_summary(delivered_order_count, delivered_gmv);

ANALYZE;
