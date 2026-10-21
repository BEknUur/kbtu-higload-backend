BEGIN;
TRUNCATE products, categories, idempotency_keys RESTART IDENTITY CASCADE;

INSERT INTO categories (name)
SELECT 'Category ' || r FROM generate_series(1, 10) r;

INSERT INTO categories (parent_id, name)
SELECT r, 'Category ' || r || '.' || c
FROM generate_series(1, 10) r, generate_series(1, 9) c;

SELECT setseed(0.42); 

INSERT INTO products (sku, name, description, price, category_id, attributes, image_keys)
SELECT
    'SKU-' || lpad(i::text, 8, '0'),
    'Product ' || i,
    'Description of product ' || i,
    round((1 + random() * 999)::numeric, 2),
    11 + i % 90,
    jsonb_build_object('color', (ARRAY['red', 'green', 'blue', 'black', 'white'])[1 + (i / 90) % 5]),
    ARRAY['products/' || i || '/0.jpg', 'products/' || i || '/1.jpg', 'products/' || i || '/2.jpg']
FROM generate_series(1, :count) i;

COMMIT;
ANALYZE;