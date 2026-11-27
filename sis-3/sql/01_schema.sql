DROP TABLE IF EXISTS outbox, products, categories;

CREATE TABLE categories (
    id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    parent_id bigint REFERENCES categories (id),
    name      text NOT NULL
);

CREATE TABLE products (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sku         text NOT NULL UNIQUE,
    name        text NOT NULL,
    description text NOT NULL DEFAULT '',
    price       numeric(12, 2) NOT NULL CHECK (price >= 0),
    category_id bigint NOT NULL REFERENCES categories (id),
    attributes  jsonb NOT NULL DEFAULT '{}',
    status      text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'deleted')),
    version     integer NOT NULL DEFAULT 1,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE outbox (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id   bigint NOT NULL,
    event_type   text NOT NULL,
    payload      jsonb NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz
);