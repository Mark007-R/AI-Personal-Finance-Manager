-- FinTrack schema bootstrap for the web app's MySQL database.
-- Run this ONCE against the database you set as DB_NAME:
--   mysql -h <host> -P <port> -u <user> -p <db> < db/schema.sql
--
-- Only users1 + transactions are required for the app to run. The investment
-- catalog tables (RecurringDeposits / Bonds / Banks / BankStockData /
-- BankLifeInsurance) are OPTIONAL: invest.py falls back to a built-in catalog
-- when they are absent, so they are not created here.

CREATE TABLE IF NOT EXISTS users1 (
    id        INT AUTO_INCREMENT PRIMARY KEY,
    firstname VARCHAR(120)        NOT NULL,
    lastname  VARCHAR(120)        NOT NULL DEFAULT '',
    email     VARCHAR(255)        NOT NULL UNIQUE,
    password  VARCHAR(255)        NOT NULL          -- werkzeug pbkdf2/scrypt hash
);

CREATE TABLE IF NOT EXISTS transactions (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    user_id     INT            NOT NULL,
    description VARCHAR(255)   NOT NULL,
    amount      DECIMAL(12,2)  NOT NULL,
    date        DATE           NOT NULL,
    INDEX idx_transactions_user_id (user_id),
    CONSTRAINT fk_transactions_user
        FOREIGN KEY (user_id) REFERENCES users1(id) ON DELETE CASCADE
);
