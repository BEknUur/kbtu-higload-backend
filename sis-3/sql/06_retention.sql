SELECT 'outbox_' || to_char(min(created_at), 'YYYY_MM') AS oldest,
       date_trunc('month', min(created_at)) + interval '1 month' AS boundary,
       (SELECT relkind = 'p' FROM pg_class WHERE relname = 'outbox') AS partitioned
FROM outbox
\gset

\timing on
BEGIN;
\if :partitioned
    \echo '=== retention: DROP the oldest month' :oldest
    DROP TABLE :"oldest";
\else
    \echo '=== retention: DELETE events older than' :boundary
    DELETE FROM outbox WHERE created_at < :'boundary';
\endif
ROLLBACK;