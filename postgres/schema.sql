-- PostgreSQL warehouse schema for the Olist dataset.
-- The loader runs this file inside one transaction when --replace is supplied.

DROP TABLE IF EXISTS customer_order_summary CASCADE;
DROP TABLE IF EXISTS payment_type_summary CASCADE;
DROP TABLE IF EXISTS delivery_kpis CASCADE;
DROP TABLE IF EXISTS category_sales_summary CASCADE;
DROP TABLE IF EXISTS product_sales CASCADE;
DROP TABLE IF EXISTS order_delivery_metrics CASCADE;
DROP TABLE IF EXISTS order_financials CASCADE;
DROP TABLE IF EXISTS reviews CASCADE;
DROP TABLE IF EXISTS payments CASCADE;
DROP TABLE IF EXISTS order_items CASCADE;
DROP TABLE IF EXISTS orders CASCADE;
DROP TABLE IF EXISTS products CASCADE;
DROP TABLE IF EXISTS sellers CASCADE;
DROP TABLE IF EXISTS customers CASCADE;
DROP TABLE IF EXISTS category_translations CASCADE;
DROP TABLE IF EXISTS geolocation CASCADE;

CREATE TABLE geolocation (
    zip_code_prefix INTEGER PRIMARY KEY,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    city TEXT,
    state TEXT,
    source_row_count INTEGER NOT NULL CHECK (source_row_count > 0)
);

CREATE TABLE category_translations (
    category_name TEXT PRIMARY KEY,
    category_name_english TEXT NOT NULL
);

CREATE TABLE customers (
    customer_id TEXT PRIMARY KEY,
    customer_unique_id TEXT NOT NULL,
    zip_code_prefix INTEGER,
    city TEXT,
    state TEXT
);

CREATE TABLE sellers (
    seller_id TEXT PRIMARY KEY,
    zip_code_prefix INTEGER,
    city TEXT,
    state TEXT
);

CREATE TABLE products (
    product_id TEXT PRIMARY KEY,
    category_name TEXT,
    name_length INTEGER,
    description_length INTEGER,
    photos_qty INTEGER,
    weight_g NUMERIC(12, 3),
    length_cm NUMERIC(10, 3),
    height_cm NUMERIC(10, 3),
    width_cm NUMERIC(10, 3)
);

CREATE TABLE orders (
    order_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(customer_id),
    status TEXT NOT NULL,
    purchase_timestamp TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    approved_at TIMESTAMP WITHOUT TIME ZONE,
    delivered_carrier_timestamp TIMESTAMP WITHOUT TIME ZONE,
    delivered_customer_timestamp TIMESTAMP WITHOUT TIME ZONE,
    estimated_delivery_timestamp TIMESTAMP WITHOUT TIME ZONE
);

CREATE TABLE order_items (
    order_id TEXT NOT NULL REFERENCES orders(order_id),
    order_item_id INTEGER NOT NULL,
    product_id TEXT NOT NULL REFERENCES products(product_id),
    seller_id TEXT NOT NULL REFERENCES sellers(seller_id),
    shipping_limit_timestamp TIMESTAMP WITHOUT TIME ZONE,
    price NUMERIC(14, 2) NOT NULL CHECK (price >= 0),
    freight_value NUMERIC(14, 2) NOT NULL CHECK (freight_value >= 0),
    PRIMARY KEY (order_id, order_item_id)
);

CREATE TABLE payments (
    order_id TEXT NOT NULL REFERENCES orders(order_id),
    payment_sequential INTEGER NOT NULL,
    payment_type TEXT,
    payment_installments INTEGER,
    payment_value NUMERIC(14, 2) CHECK (payment_value >= 0),
    PRIMARY KEY (order_id, payment_sequential)
);

CREATE TABLE reviews (
    review_id TEXT NOT NULL,
    order_id TEXT NOT NULL REFERENCES orders(order_id),
    review_score INTEGER NOT NULL CHECK (review_score BETWEEN 1 AND 5),
    comment_title TEXT,
    comment_message TEXT,
    creation_timestamp TIMESTAMP WITHOUT TIME ZONE,
    answer_timestamp TIMESTAMP WITHOUT TIME ZONE,
    PRIMARY KEY (review_id, order_id)
);

CREATE INDEX idx_customers_unique_id ON customers(customer_unique_id);
CREATE INDEX idx_customers_state ON customers(state);
CREATE INDEX idx_orders_customer_purchase ON orders(customer_id, purchase_timestamp);
CREATE INDEX idx_orders_purchase_status ON orders(purchase_timestamp, status);
CREATE INDEX idx_order_items_product ON order_items(product_id);
CREATE INDEX idx_order_items_seller ON order_items(seller_id);
CREATE INDEX idx_payments_type ON payments(payment_type);
CREATE INDEX idx_reviews_order ON reviews(order_id);
CREATE INDEX idx_reviews_score ON reviews(review_score);
CREATE INDEX idx_products_category ON products(category_name);
CREATE INDEX idx_sellers_state ON sellers(state);
