-- WealthLite demo schema. Every row is synthetic: no real people, funds or balances.
CREATE TABLE users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  password_hash TEXT NOT NULL
);

CREATE TABLE holdings (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users (id),
  fund_name TEXT NOT NULL,
  ticker TEXT NOT NULL,
  asset_class TEXT NOT NULL,
  units REAL NOT NULL,
  unit_price_pence INTEGER NOT NULL
);

-- Demo login: demo@wealthlite.test / demo-pass-2026 (shown on the login page).
INSERT INTO users (id, email, display_name, password_hash) VALUES
  ('u_demo', 'demo@wealthlite.test', 'Demo Investor', 'pbkdf2$100000$1634ebd6a579650c5baec7939beac070$fab7f0b2bf28517617db42a0bd6a8635effd7f68ddcf3ac8393dbc820e3ae174');

INSERT INTO holdings (id, user_id, fund_name, ticker, asset_class, units, unit_price_pence) VALUES
  ('h_1', 'u_demo', 'Northwind Global Equity', 'NWGE', 'Equity', 1250, 1842),
  ('h_2', 'u_demo', 'Harbour Sterling Bond', 'HSBD', 'Fixed income', 3400, 512),
  ('h_3', 'u_demo', 'Meridian Emerging Markets', 'MEMK', 'Equity', 820, 1376),
  ('h_4', 'u_demo', 'Lantern Property Income', 'LTPI', 'Property', 600, 945),
  ('h_5', 'u_demo', 'Cobalt Cash Reserve', 'CBCR', 'Cash', 5000, 100);
