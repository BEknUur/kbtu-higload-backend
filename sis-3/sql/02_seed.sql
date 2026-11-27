INSERT INTO categories (name)
SELECT 'Category ' || r FROM generate_series(1, 10) r;

INSERT INTO categories (parent_id, name)
SELECT r, 'Category ' || r || '.' || c
FROM generate_series(1, 10) r, generate_series(1, 9) c;

SELECT setseed(0.42);

INSERT INTO products (sku, name, description, price, category_id, status)
SELECT
    'SKU-' || lpad(i::text, 8, '0'),
    'Product ' || i,
    'Description of product ' || i,
    round((1 + random() * 999)::numeric, 2),
    11 + floor(90 * random() ^ 2)::int,
    CASE WHEN random() < 0.1 THEN 'deleted' ELSE 'active' END
FROM generate_series(1, 1000000) i;

INSERT INTO outbox (product_id, event_type, payload, created_at, processed_at)
SELECT 1 + floor(random() * 1000000)::int, 'product.updated', '{}', ts, ts + interval '1 second'
FROM (SELECT now() - random() * interval '90 days' AS ts FROM generate_series(1, 3000000)) s;

VACUUM ANALYZE;