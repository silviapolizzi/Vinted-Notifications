BEGIN TRANSACTION;

-- Per query settings
ALTER TABLE queries ADD COLUMN enabled INTEGER DEFAULT 1;
ALTER TABLE queries ADD COLUMN per_page INTEGER;
ALTER TABLE queries ADD COLUMN banwords TEXT;

-- Notifications waiting to be sent (survive restarts)
CREATE TABLE IF NOT EXISTS outbox
(
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    content     TEXT NOT NULL,
    caption     TEXT,
    url         TEXT,
    button_text TEXT,
    buy_url     TEXT,
    buy_text    TEXT,
    photo_url   TEXT,
    query_id    INTEGER,
    created_at  NUMERIC,
    attempts    INTEGER DEFAULT 0,
    sent_at     NUMERIC,
    last_error  TEXT
);

-- Cache of the seller country, avoids one API call per item
CREATE TABLE IF NOT EXISTS users
(
    profile_id TEXT PRIMARY KEY,
    country    TEXT,
    updated_at NUMERIC
);

CREATE INDEX IF NOT EXISTS idx_items_item ON items (item);
CREATE INDEX IF NOT EXISTS idx_outbox_sent_at ON outbox (sent_at, created_at);

-- Browser session (Patchright) settings
INSERT OR IGNORE INTO parameters (key, value)
VALUES ('browser_enabled', 'True'),
       ('browser_mode', 'essential'),
       ('browser_domain', 'www.vinted.fr'),
       ('browser_headless', 'True'),
       ('browser_channel', ''),
       ('browser_no_sandbox', 'True'),
       ('browser_user_data_dir', './data/browser_profile'),
       ('browser_ttl', '1800'),
       ('browser_state', '{}'),
       ('browser_state_ts', '0'),
       ('browser_status', 'unknown'),
       ('browser_last_error', ''),
       ('browser_refresh_requested', 'False'),
       ('browser_retry_after', '0');

-- Notification settings
INSERT OR IGNORE INTO parameters (key, value)
VALUES ('web_ui_port', '8000'),
       ('send_photo', 'True'),
       ('show_buy_button', 'False'),
       ('notifications_paused', 'False'),
       ('quiet_hours', ''),
       ('caption_template', '🆕 {title}
💶 {price}
🛍️ {brand}');

UPDATE parameters
SET value = '1.0.6'
WHERE key = 'version';

COMMIT;
