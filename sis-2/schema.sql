CREATE TABLE IF NOT EXISTS categories (
    id        BIGSERIAL PRIMARY KEY,
    parent_id BIGINT REFERENCES categories(id),
    name      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
    id          BIGSERIAL PRIMARY KEY,
    sku         TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    price       NUMERIC(12, 2) NOT NULL CHECK (price >= 0),
    category_id BIGINT NOT NULL REFERENCES categories(id),
    attributes  JSONB NOT NULL DEFAULT '{}',
    image_keys  TEXT[] NOT NULL DEFAULT '{}',
    status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'deleted')),
    version     INT NOT NULL DEFAULT 1,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    key          TEXT PRIMARY KEY,
    request_hash TEXT NOT NULL,
    status_code  INT,
    response     JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- indexes 
CREATE INDEX IF NOT EXISTS products_category_id
    ON products (category_id, id) WHERE status = 'active';
-- attr.color=red  ->  attributes @> '{"color": "red"}'
CREATE INDEX IF NOT EXISTS products_attributes
    ON products USING GIN (attributes jsonb_path_ops);