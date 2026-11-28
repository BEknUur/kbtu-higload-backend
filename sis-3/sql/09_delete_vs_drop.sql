SELECT date_trunc('month', min(created_at)) + interval '1 month' AS boundary FROM events_none \gset
SELECT min(c.relname) AS oldest
FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
WHERE i.inhparent = 'events_month'::regclass \gset
SELECT count(*) AS rows_to_remove FROM events_none WHERE created_at < :'boundary' \gset
\echo '=== remove the oldest month:' :rows_to_remove 'events older than' :boundary

\echo '--- DELETE on the plain table'
SELECT pg_current_wal_lsn() AS lsn, pg_total_relation_size('events_none') AS size \gset
\timing on
DELETE FROM events_none WHERE created_at < :'boundary';
\timing off
SELECT pg_size_pretty(pg_current_wal_lsn() - :'lsn'::pg_lsn) AS wal_written,
       pg_size_pretty(:size::bigint) AS size_before,
       pg_size_pretty(pg_total_relation_size('events_none')) AS size_after;
\echo 'VACUUM afterwards (the dead rows must be cleaned up):'
\timing on
VACUUM events_none;
\timing off
SELECT pg_size_pretty(pg_total_relation_size('events_none')) AS size_after_vacuum;

\echo '--- DROP the oldest monthly partition'
SELECT pg_current_wal_lsn() AS lsn,
       (SELECT sum(pg_total_relation_size(inhrelid)) FROM pg_inherits
        WHERE inhparent = 'events_month'::regclass) AS size \gset
\timing on
DROP TABLE :"oldest";
\timing off
SELECT pg_size_pretty(pg_current_wal_lsn() - :'lsn'::pg_lsn) AS wal_written,
       pg_size_pretty(:size::bigint) AS size_before,
       pg_size_pretty((SELECT sum(pg_total_relation_size(inhrelid)) FROM pg_inherits
                       WHERE inhparent = 'events_month'::regclass)) AS size_after;

\echo '=== where DELETE wins: remove the events of one product (not a time range)'
\echo '--- plain table'
\timing on
BEGIN;
DELETE FROM events_none WHERE product_id = 42;
ROLLBACK;
\echo '--- monthly partitions: DROP is impossible, DELETE has to visit every partition'
BEGIN;
DELETE FROM events_month WHERE product_id = 42;
ROLLBACK;
\timing off
