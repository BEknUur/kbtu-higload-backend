CREATE INDEX products_category ON products (category_id, id) WHERE status = 'active';
CREATE INDEX products_category_price ON products (category_id, price) WHERE status = 'active';
CREATE INDEX outbox_pending ON outbox (id) WHERE processed_at IS NULL;