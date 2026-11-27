BEGIN;

ALTER TABLE outbox RENAME TO outbox_old;
ALTER INDEX outbox_pkey RENAME TO outbox_old_pkey;

CREATE TABLE outbox (
    id           bigint GENERATED ALWAYS AS IDENTITY,
    product_id   bigint NOT NULL,
    event_type   text NOT NULL,
    payload      jsonb NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz,
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

SELECT format('CREATE TABLE outbox_%s PARTITION OF outbox FOR VALUES FROM (%L) TO (%L)',
              to_char(month, 'YYYY_MM'), month, month + interval '1 month')
FROM generate_series(date_trunc('month', (SELECT min(created_at) FROM outbox_old)),
                     date_trunc('month', now()) + interval '2 months',
                     interval '1 month') AS month
\gexec

INSERT INTO outbox OVERRIDING SYSTEM VALUE SELECT * FROM outbox_old;
SELECT setval(pg_get_serial_sequence('outbox', 'id'), (SELECT max(id) FROM outbox));
DROP TABLE outbox_old;

CREATE INDEX outbox_pending ON outbox (id) WHERE processed_at IS NULL;

COMMIT;
VACUUM ANALYZE outbox;