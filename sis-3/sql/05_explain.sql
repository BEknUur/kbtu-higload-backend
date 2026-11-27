\echo '=== get: product card by id'
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM products WHERE id = 500000;

\echo '=== list_category: popular category 11, page after id 500000'
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, name, price FROM products
WHERE category_id = 11 AND status = 'active' AND id > 500000
ORDER BY id LIMIT 20;

\echo '=== list_category: rare category 100, page after id 500000'
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, name, price FROM products
WHERE category_id = 100 AND status = 'active' AND id > 500000
ORDER BY id LIMIT 20;

\echo '=== list_filter: rare category 100, price 100..110'
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, name, price FROM products
WHERE category_id = 100 AND status = 'active' AND price BETWEEN 100 AND 110
ORDER BY id LIMIT 20;

\echo '=== write: price change + event in outbox'
BEGIN;
EXPLAIN (ANALYZE, BUFFERS)
WITH changed AS (
    UPDATE products SET price = price + 1, version = version + 1, updated_at = now()
    WHERE id = 500000
    RETURNING id
)
INSERT INTO outbox (product_id, event_type, payload)
SELECT id, 'product.updated', '{}' FROM changed;
ROLLBACK;

\echo '=== outbox worker: pending events of the last 7 days'
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, product_id FROM outbox
WHERE processed_at IS NULL AND created_at > now() - interval '7 days'
ORDER BY id LIMIT 100;