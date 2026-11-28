SELECT count(*) AS indexes FROM pg_indexes WHERE tablename = 'products' \gset
\echo '=== indexes on products:' :indexes

\timing on
\echo '--- INSERT 100,000 products'
BEGIN;
INSERT INTO products (sku, name, price, category_id)
SELECT 'NEW-' || i, 'New ' || i, 100, 11 + i % 90 FROM generate_series(1, 100000) i;
ROLLBACK;

\echo '--- UPDATE price of 100,000 products'
BEGIN;
UPDATE products SET price = price + 1 WHERE id <= 100000;
ROLLBACK;
\timing off

\echo '--- sizes'
SELECT relname AS relation, pg_size_pretty(pg_relation_size(oid)) AS size
FROM pg_class
WHERE relname LIKE 'products%' AND relkind IN ('r', 'i')
ORDER BY relkind DESC, relname;
