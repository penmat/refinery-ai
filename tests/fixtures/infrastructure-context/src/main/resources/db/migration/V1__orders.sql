CREATE TABLE customers (
  id BIGINT PRIMARY KEY
);

CREATE TABLE orders (
  id BIGINT PRIMARY KEY,
  customer_id BIGINT REFERENCES customers(id)
);
