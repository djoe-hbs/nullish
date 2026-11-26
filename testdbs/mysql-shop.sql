-- Same sample "shop" database as postgres-shop.sql, in MySQL syntax.

CREATE TABLE customers (
  id          int PRIMARY KEY,
  name        varchar(100) NOT NULL,
  email       varchar(200) NOT NULL,
  city        varchar(100),
  created_at  date NOT NULL
) ENGINE=InnoDB COMMENT='People who bought something';

CREATE TABLE products (
  id        int PRIMARY KEY,
  name      varchar(200) NOT NULL,
  category  varchar(50) NOT NULL,
  price     decimal(10,2) NOT NULL,
  stock     int NOT NULL
) ENGINE=InnoDB;

CREATE TABLE orders (
  id           int PRIMARY KEY,
  customer_id  int NOT NULL,
  status       varchar(20) NOT NULL COMMENT 'pending, shipped, delivered or cancelled',
  ordered_at   date NOT NULL,
  FOREIGN KEY (customer_id) REFERENCES customers(id)
) ENGINE=InnoDB;

CREATE TABLE order_items (
  order_id    int NOT NULL,
  product_id  int NOT NULL,
  quantity    int NOT NULL,
  unit_price  decimal(10,2) NOT NULL,
  PRIMARY KEY (order_id, product_id),
  FOREIGN KEY (order_id) REFERENCES orders(id),
  FOREIGN KEY (product_id) REFERENCES products(id)
) ENGINE=InnoDB;

CREATE TABLE staff_secrets (
  id        int PRIMARY KEY,
  login     varchar(50) NOT NULL,
  password  varchar(100) NOT NULL
) ENGINE=InnoDB;

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
