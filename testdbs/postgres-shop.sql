-- Sample "shop" database for developing and testing database connections.
-- Also loaded into the MySQL test database (see mysql-shop.sql).

CREATE TABLE customers (
  id          integer PRIMARY KEY,
  name        text NOT NULL,
  email       text NOT NULL,
  city        text,
  created_at  date NOT NULL
);
COMMENT ON TABLE customers IS 'People who bought something';

CREATE TABLE products (
  id        integer PRIMARY KEY,
  name      text NOT NULL,
  category  text NOT NULL,
  price     numeric(10,2) NOT NULL,
  stock     integer NOT NULL
);

CREATE TABLE orders (
  id           integer PRIMARY KEY,
  customer_id  integer NOT NULL REFERENCES customers(id),
  status       text NOT NULL,
  ordered_at   date NOT NULL
);
COMMENT ON COLUMN orders.status IS 'pending, shipped, delivered or cancelled';

CREATE TABLE order_items (
  order_id    integer NOT NULL REFERENCES orders(id),
  product_id  integer NOT NULL REFERENCES products(id),
  quantity    integer NOT NULL,
  unit_price  numeric(10,2) NOT NULL,
  PRIMARY KEY (order_id, product_id)
);

-- Something that should be hidden from the model in tests.
CREATE TABLE staff_secrets (
  id        integer PRIMARY KEY,
  login     text NOT NULL,
  password  text NOT NULL
);

INSERT INTO customers VALUES
  (1, 'Asha Rao',     'asha@example.com',  'Chennai',   '2026-01-04'),
  (2, 'Ben Carter',   'ben@example.com',   'Bengaluru', '2026-02-11'),
  (3, 'Chen Wei',     'chen@example.com',  'Chennai',   '2026-02-20'),
  (4, 'Divya Menon',  'divya@example.com', 'Kochi',     '2026-03-02'),
  (5, 'Elena Petrov', 'elena@example.com', 'Mumbai',    '2026-03-15');

INSERT INTO products VALUES
  (1, 'Charcoal Face Wash',      'face-wash',   299.00, 40),
  (2, 'Vitamin C Serum',         'serum',       329.00, 12),
  (3, 'Sunscreen SPF 50',        'sunscreen',   349.00,  0),
  (4, 'Retinol Night Cream',     'night-cream', 399.00,  7),
  (5, 'Hydrating Body Lotion',   'body-lotion', 249.00, 25);

INSERT INTO orders VALUES
  (1, 1, 'delivered', '2026-03-01'),
  (2, 2, 'shipped',   '2026-03-05'),
  (3, 1, 'pending',   '2026-03-09'),
  (4, 3, 'delivered', '2026-03-10'),
  (5, 4, 'pending',   '2026-03-12'),
  (6, 5, 'cancelled', '2026-03-14'),
  (7, 2, 'delivered', '2026-03-18');

INSERT INTO order_items VALUES
  (1, 1, 2, 299.00), (1, 2, 1, 329.00),
  (2, 3, 1, 349.00),
  (3, 4, 1, 399.00), (3, 5, 2, 249.00),
  (4, 2, 3, 329.00),
  (5, 1, 1, 299.00),
  (6, 5, 4, 249.00),
  (7, 4, 2, 399.00), (7, 1, 1, 299.00);

INSERT INTO staff_secrets VALUES (1, 'admin', 'hunter2');
