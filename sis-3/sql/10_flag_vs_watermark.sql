
SELECT max(id) AS watermark FROM outbox \gset
INSERT INTO outbox (product_id, event_type, payload)
SELECT 1 + i % 1000000, 'product.updated', '{}' FROM generate_series(1, 100000) i;
VACUUM ANALYZE outbox;
\echo '=== 3,000,000 sent events + a backlog of 100,000 new ones'

\echo '--- flag without an index: the worker query'
BEGIN;
DROP INDEX outbox_pending;
EXPLAIN (ANALYZE, COSTS OFF)
SELECT id, product_id FROM outbox WHERE processed_at IS NULL ORDER BY id LIMIT 100;
ROLLBACK;

\echo '--- flag + partial index outbox_pending: the worker query'
EXPLAIN (ANALYZE, COSTS OFF)
SELECT id, product_id FROM outbox WHERE processed_at IS NULL ORDER BY id LIMIT 100;

\echo '--- watermark (primary key range, no extra index): the worker query'
EXPLAIN (ANALYZE, COSTS OFF)
SELECT id, product_id FROM outbox WHERE id > :watermark ORDER BY id LIMIT 100;

\echo '=== send the whole backlog in batches of 100, one transaction per batch'

\echo '--- flag: UPDATE every sent event'
SELECT pg_current_wal_lsn() AS lsn, pg_total_relation_size('outbox') AS size \gset
\timing on
DO $$
DECLARE sent int;
BEGIN
  LOOP
    UPDATE outbox SET processed_at = now()
    WHERE id IN (SELECT id FROM outbox WHERE processed_at IS NULL
                 ORDER BY id LIMIT 100 FOR UPDATE SKIP LOCKED);
    GET DIAGNOSTICS sent = ROW_COUNT;
    EXIT WHEN sent = 0;
    COMMIT;
  END LOOP;
END $$;
\timing off
SELECT pg_size_pretty(pg_current_wal_lsn() - :'lsn'::pg_lsn) AS wal_written,
       pg_size_pretty(pg_total_relation_size('outbox') - :size::bigint) AS table_growth;

\echo '--- watermark: UPDATE one row per batch'
DROP TABLE IF EXISTS outbox_watermark;
CREATE TABLE outbox_watermark (position bigint NOT NULL);
INSERT INTO outbox_watermark VALUES (:watermark);
SELECT pg_current_wal_lsn() AS lsn, pg_total_relation_size('outbox') AS size \gset
\timing on
DO $$
DECLARE last_id bigint;
BEGIN
  LOOP
    SELECT max(id), count(product_id) INTO last_id
    FROM (SELECT id, product_id FROM outbox
          WHERE id > (SELECT position FROM outbox_watermark)
          ORDER BY id LIMIT 100) batch;
    EXIT WHEN last_id IS NULL;
    UPDATE outbox_watermark SET position = last_id;
    COMMIT;
  END LOOP;
END $$;
\timing off
SELECT pg_size_pretty(pg_current_wal_lsn() - :'lsn'::pg_lsn) AS wal_written,
       pg_size_pretty(pg_total_relation_size('outbox') - :size::bigint) AS table_growth;
