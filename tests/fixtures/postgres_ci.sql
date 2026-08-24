-- Small synthetic Olist-shaped dataset for deterministic PostgreSQL CI tests.
-- The schema comes from postgres/schema.sql; this file contains data only.

INSERT INTO geolocation
    (zip_code_prefix, latitude, longitude, city, state, source_row_count)
VALUES
    (10001, -23.5505, -46.6333, 'sao paulo', 'SP', 1),
    (20001, -22.9068, -43.1729, 'rio de janeiro', 'RJ', 1),
    (30001, -19.9167, -43.9345, 'belo horizonte', 'MG', 1);

INSERT INTO category_translations (category_name, category_name_english)
VALUES
    ('beleza_saude', 'health_beauty'),
    ('livros', 'books'),
    ('eletronicos', 'electronics');

INSERT INTO customers
    (customer_id, customer_unique_id, zip_code_prefix, city, state)
VALUES
    ('c001', 'u001', 10001, 'sao paulo', 'SP'),
    ('c002', 'u002', 20001, 'rio de janeiro', 'RJ'),
    ('c003', 'u001', 10001, 'sao paulo', 'SP'),
    ('c004', 'u003', 30001, 'belo horizonte', 'MG');

INSERT INTO sellers (seller_id, zip_code_prefix, city, state)
VALUES
    ('s001', 10001, 'sao paulo', 'SP'),
    ('s002', 20001, 'rio de janeiro', 'RJ'),
    ('s003', 30001, 'belo horizonte', 'MG');

INSERT INTO products
    (product_id, category_name, name_length, description_length, photos_qty,
     weight_g, length_cm, height_cm, width_cm)
VALUES
    ('p001', 'beleza_saude', 20, 100, 2, 500, 20, 10, 15),
    ('p002', 'livros', 18, 80, 1, 300, 25, 3, 18),
    ('p003', 'eletronicos', 30, 150, 3, 900, 30, 12, 22),
    ('p004', 'livros', 22, 90, 1, 350, 24, 4, 17);

INSERT INTO orders
    (order_id, customer_id, status, purchase_timestamp, approved_at,
     delivered_carrier_timestamp, delivered_customer_timestamp,
     estimated_delivery_timestamp)
VALUES
    ('o001', 'c001', 'delivered', '2018-01-01 10:00:00', '2018-01-01 11:00:00',
     '2018-01-02 09:00:00', '2018-01-05 10:00:00', '2018-01-07 00:00:00'),
    ('o002', 'c002', 'delivered', '2018-02-01 10:00:00', '2018-02-01 11:00:00',
     '2018-02-02 09:00:00', '2018-02-10 10:00:00', '2018-02-08 00:00:00'),
    ('o003', 'c003', 'delivered', '2018-03-01 10:00:00', '2018-03-01 11:00:00',
     '2018-03-02 09:00:00', '2018-03-04 10:00:00', '2018-03-06 00:00:00'),
    ('o004', 'c004', 'delivered', '2018-04-01 10:00:00', '2018-04-01 11:00:00',
     '2018-04-02 09:00:00', '2018-04-04 10:00:00', '2018-04-05 00:00:00'),
    ('o005', 'c001', 'canceled', '2018-05-01 10:00:00', NULL, NULL, NULL,
     '2018-05-10 00:00:00'),
    ('o006', 'c002', 'shipped', '2018-06-01 10:00:00', '2018-06-01 11:00:00',
     '2018-06-02 09:00:00', NULL, '2018-06-10 00:00:00');

INSERT INTO order_items
    (order_id, order_item_id, product_id, seller_id, shipping_limit_timestamp,
     price, freight_value)
VALUES
    ('o001', 1, 'p001', 's001', '2018-01-03 00:00:00', 100.00, 10.00),
    ('o001', 2, 'p002', 's002', '2018-01-03 00:00:00', 20.00, 2.00),
    ('o002', 1, 'p003', 's003', '2018-02-03 00:00:00', 80.00, 10.00),
    ('o003', 1, 'p001', 's002', '2018-03-03 00:00:00', 200.00, 10.00),
    ('o003', 2, 'p004', 's001', '2018-03-03 00:00:00', 28.16, 1.84),
    ('o004', 1, 'p002', 's003', '2018-04-03 00:00:00', 120.00, 10.00),
    ('o005', 1, 'p003', 's001', '2018-05-03 00:00:00', 50.00, 5.00),
    ('o006', 1, 'p004', 's002', '2018-06-03 00:00:00', 70.00, 5.00);

INSERT INTO payments
    (order_id, payment_sequential, payment_type, payment_installments, payment_value)
VALUES
    ('o001', 1, 'credit_card', 1, 132.00),
    ('o002', 1, 'boleto', 1, 90.00),
    ('o003', 1, 'credit_card', 2, 200.00),
    ('o003', 2, 'voucher', 1, 40.00),
    ('o004', 1, 'credit_card', 2, 130.00),
    ('o005', 1, 'credit_card', 1, 55.00),
    ('o006', 1, 'boleto', 1, 75.00);

INSERT INTO reviews
    (review_id, order_id, review_score, comment_title, comment_message,
     creation_timestamp, answer_timestamp)
VALUES
    ('r001', 'o001', 5, 'great', 'arrived early', '2018-01-06', '2018-01-07'),
    ('r002', 'o002', 2, 'late', 'arrived late', '2018-02-11', '2018-02-12'),
    ('r003', 'o003', 4, 'good', 'as expected', '2018-03-05', '2018-03-06'),
    ('r004', 'o004', 5, 'great', 'fast delivery', '2018-04-05', '2018-04-06'),
    ('r006', 'o006', 3, 'pending', 'still in transit', '2018-06-05', '2018-06-06');
