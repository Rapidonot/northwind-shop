-- BE-04 (row #25): the inventory store. Applied by `northwind_backend.inventory.postgres.apply_schema`
-- into the connection's first search_path schema (a throwaway schema in tests).
-- The CHECK on inventory_stock is the database's last guard against oversell: `held` never exceeds
-- `on_hand`, whatever the code above it does.

CREATE TABLE IF NOT EXISTS inventory_stock (
    variant_id text NOT NULL,
    node       text NOT NULL,
    region     text NOT NULL,
    on_hand    integer NOT NULL,
    held       integer NOT NULL DEFAULT 0,
    PRIMARY KEY (variant_id, node),
    CONSTRAINT inventory_stock_on_hand CHECK (on_hand >= 0),
    CONSTRAINT inventory_stock_held CHECK (held >= 0 AND held <= on_hand)
);

CREATE TABLE IF NOT EXISTS inventory_reservations (
    idempotency_key text PRIMARY KEY,
    fingerprint     text NOT NULL,
    order_id        text NOT NULL,
    region          text NOT NULL,
    status          text NOT NULL CHECK (status IN ('held', 'released', 'committed')),
    expires_at      timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory_holds (
    idempotency_key text NOT NULL REFERENCES inventory_reservations ON DELETE CASCADE,
    variant_id      text NOT NULL,
    node            text NOT NULL,
    quantity        integer NOT NULL CHECK (quantity > 0),
    swept           boolean NOT NULL DEFAULT false,
    PRIMARY KEY (idempotency_key, variant_id),
    FOREIGN KEY (variant_id, node) REFERENCES inventory_stock
);

CREATE INDEX IF NOT EXISTS inventory_holds_by_row ON inventory_holds (variant_id, node)
    WHERE NOT swept;
