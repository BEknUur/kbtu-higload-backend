SELECT 'events_' || :'unit' AS tbl, '1 ' || :'unit' AS step, :'unit' = 'none' AS plain \gset

DROP TABLE IF EXISTS :"tbl";
\if :plain
CREATE TABLE :"tbl" (LIKE outbox INCLUDING DEFAULTS, PRIMARY KEY (id, created_at));
\else
CREATE TABLE :"tbl" (LIKE outbox INCLUDING DEFAULTS, PRIMARY KEY (id, created_at))
PARTITION BY RANGE (created_at);

SELECT format('CREATE TABLE %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L)',
              :'tbl' || '_' || to_char(p, 'YYYYMMDD'), :'tbl', p, p + :'step'::interval)
FROM generate_series(date_trunc(:'unit', (SELECT min(created_at) FROM outbox)),
                     date_trunc(:'unit', now()) + :'step'::interval,
                     :'step'::interval) AS p
\gexec
\endif

SELECT count(*) AS partitions FROM pg_inherits WHERE inhparent = :'tbl'::regclass \gset
\echo '=== ' :tbl ', partitions:' :partitions

\timing on
\echo '--- insert 3,000,000 events (in time order, the way an append-only log fills up)'
INSERT INTO :"tbl" SELECT * FROM outbox ORDER BY created_at;
\timing off
CREATE INDEX ON :"tbl" (id) WHERE processed_at IS NULL;
VACUUM ANALYZE :"tbl";

\echo '--- worker query, 7-day window (partitions can be pruned)'
EXPLAIN (ANALYZE, COSTS OFF)
SELECT id, product_id FROM :"tbl"
WHERE processed_at IS NULL AND created_at > now() - interval '7 days' ORDER BY id LIMIT 100;
EXPLAIN (ANALYZE, COSTS OFF)
SELECT id, product_id FROM :"tbl"
WHERE processed_at IS NULL AND created_at > now() - interval '7 days' ORDER BY id LIMIT 100;

\echo '--- the same query without the window (every partition)'
EXPLAIN (ANALYZE, COSTS OFF)
SELECT id, product_id FROM :"tbl" WHERE processed_at IS NULL ORDER BY id LIMIT 100;
EXPLAIN (ANALYZE, COSTS OFF)
SELECT id, product_id FROM :"tbl" WHERE processed_at IS NULL ORDER BY id LIMIT 100;

\echo '--- one event by id, without created_at (every partition)'
EXPLAIN (ANALYZE, COSTS OFF) SELECT * FROM :"tbl" WHERE id = 1500000;
EXPLAIN (ANALYZE, COSTS OFF) SELECT * FROM :"tbl" WHERE id = 1500000;
