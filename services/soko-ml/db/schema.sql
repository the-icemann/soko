-- Soko ML Feature Store — PostgreSQL 16
-- Owned exclusively by the ML layer. No backend service has direct access.

-- ── Price observations — feeds Prophet model training ─────────────────────────
CREATE TABLE IF NOT EXISTS price_observations (
    id              SERIAL PRIMARY KEY,
    observed_at     DATE NOT NULL,
    market          VARCHAR(50) NOT NULL,
    crop            VARCHAR(50) NOT NULL,
    price_per_kg    NUMERIC(10, 2) NOT NULL,
    currency        CHAR(3) DEFAULT 'UGX',
    source          VARCHAR(20) NOT NULL,        -- 'soko_order' | 'farmgain_seed'
    order_id        VARCHAR(100),                -- NULL for seed data
    quantity_kg     NUMERIC(10, 2),
    created_at      TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_price_market_crop
    ON price_observations(market, crop, observed_at);

-- ── Farmer features — feeds recommendation-service ───────────────────────────
CREATE TABLE IF NOT EXISTS farmer_features (
    farmer_id               VARCHAR(100) PRIMARY KEY,
    name                    VARCHAR(200),
    lat                     NUMERIC(10, 7),
    lng                     NUMERIC(10, 7),
    district                VARCHAR(100),
    crops_offered           TEXT[],
    markets_served          TEXT[],
    avg_rating              NUMERIC(3, 2) DEFAULT 0.0,
    fulfillment_rate        NUMERIC(5, 4) DEFAULT 1.0,
    avg_response_time_hrs   NUMERIC(6, 2) DEFAULT 24.0,
    price_competitiveness   NUMERIC(5, 4) DEFAULT 0.5,
    repeat_buyer_rate       NUMERIC(5, 4) DEFAULT 0.0,
    total_orders_completed  INTEGER DEFAULT 0,
    total_orders_cancelled  INTEGER DEFAULT 0,
    total_listings          INTEGER DEFAULT 0,
    last_active_at          TIMESTAMP,
    synced_at               TIMESTAMP DEFAULT NOW()
);

-- ── Buyer features — feeds recommendation-service ────────────────────────────
CREATE TABLE IF NOT EXISTS buyer_features (
    buyer_id                VARCHAR(100) PRIMARY KEY,
    name                    VARCHAR(200),
    lat                     NUMERIC(10, 7),
    lng                     NUMERIC(10, 7),
    district                VARCHAR(100),
    preferred_crops         TEXT[],
    preferred_markets       TEXT[],
    avg_order_volume_kg     NUMERIC(10, 2) DEFAULT 0.0,
    payment_reliability     NUMERIC(5, 4) DEFAULT 1.0,
    purchase_frequency_days NUMERIC(6, 2) DEFAULT 30.0,
    avg_spend_per_order     NUMERIC(12, 2) DEFAULT 0.0,
    total_purchases         INTEGER DEFAULT 0,
    last_active_at          TIMESTAMP,
    synced_at               TIMESTAMP DEFAULT NOW()
);

-- ── Market registry — static seed populated at init time ─────────────────────
-- No market-service exists; this is the authoritative source.
CREATE TABLE IF NOT EXISTS market_registry (
    market_id   VARCHAR(50) PRIMARY KEY,
    name        VARCHAR(200) NOT NULL,
    lat         NUMERIC(10, 7) NOT NULL,
    lng         NUMERIC(10, 7) NOT NULL,
    district    VARCHAR(100),
    active      BOOLEAN DEFAULT TRUE
);

INSERT INTO market_registry (market_id, name, lat, lng, district) VALUES
    ('Kisenyi_Kampala', 'Kisenyi Market, Kampala',  0.3136,  32.5811, 'Kampala'),
    ('Gulu',            'Gulu Main Market',          2.7747,  32.2990, 'Gulu'),
    ('Mbarara',         'Mbarara Market',           -0.6072,  30.6545, 'Mbarara'),
    ('Mbale',           'Mbale Central Market',      1.0824,  34.1754, 'Mbale'),
    ('Lira',            'Lira Market',               2.2499,  32.8998, 'Lira'),
    ('Masaka',          'Masaka Market',            -0.3390,  31.7369, 'Masaka')
ON CONFLICT (market_id) DO NOTHING;

-- ── Coverage map — tracks which crop-market pairs have enough data ─────────────
CREATE TABLE IF NOT EXISTS coverage_map (
    crop                    VARCHAR(50) NOT NULL,
    market                  VARCHAR(50) NOT NULL,
    observation_count       INTEGER DEFAULT 0,
    min_observations_needed INTEGER DEFAULT 52,
    is_model_ready          BOOLEAN DEFAULT FALSE,
    last_retrain_at         TIMESTAMP,
    PRIMARY KEY (crop, market)
);

-- Seed coverage_map with all known pairs at zero observations
INSERT INTO coverage_map (crop, market) VALUES
    ('maize_grain',   'Kisenyi_Kampala'), ('maize_grain',   'Gulu'),
    ('maize_grain',   'Mbarara'),         ('maize_grain',   'Mbale'),
    ('maize_grain',   'Lira'),            ('maize_grain',   'Masaka'),
    ('yellow_beans',  'Kisenyi_Kampala'), ('yellow_beans',  'Gulu'),
    ('yellow_beans',  'Mbarara'),         ('yellow_beans',  'Mbale'),
    ('yellow_beans',  'Lira'),            ('yellow_beans',  'Masaka'),
    ('irish_potatoes','Kisenyi_Kampala'), ('irish_potatoes','Gulu'),
    ('irish_potatoes','Mbarara'),         ('irish_potatoes','Mbale'),
    ('irish_potatoes','Lira'),            ('irish_potatoes','Masaka'),
    ('tomatoes',      'Kisenyi_Kampala'), ('tomatoes',      'Gulu'),
    ('tomatoes',      'Mbarara'),         ('tomatoes',      'Mbale'),
    ('tomatoes',      'Lira'),            ('tomatoes',      'Masaka'),
    ('matoke',        'Kisenyi_Kampala'), ('matoke',        'Gulu'),
    ('matoke',        'Mbarara'),         ('matoke',        'Mbale'),
    ('matoke',        'Lira'),            ('matoke',        'Masaka'),
    ('cassava_chips', 'Kisenyi_Kampala'), ('cassava_chips', 'Gulu'),
    ('cassava_chips', 'Mbarara'),         ('cassava_chips', 'Mbale'),
    ('cassava_chips', 'Lira'),            ('cassava_chips', 'Masaka'),
    ('sorghum',       'Kisenyi_Kampala'), ('sorghum',       'Gulu'),
    ('sorghum',       'Mbarara'),         ('sorghum',       'Mbale'),
    ('sorghum',       'Lira'),            ('sorghum',       'Masaka'),
    ('millet',        'Kisenyi_Kampala'), ('millet',        'Gulu'),
    ('millet',        'Mbarara'),         ('millet',        'Mbale'),
    ('millet',        'Lira'),            ('millet',        'Masaka')
ON CONFLICT (crop, market) DO NOTHING;

-- ── Coverage gaps — tracks unrecognised crops submitted by farmers ────────────
CREATE TABLE IF NOT EXISTS coverage_gaps (
    crop_submitted      VARCHAR(100) PRIMARY KEY,
    category_guess      VARCHAR(50),
    frequency           INTEGER DEFAULT 1,
    first_reported_by   VARCHAR(100),
    first_reported_at   TIMESTAMP DEFAULT NOW(),
    last_reported_at    TIMESTAMP DEFAULT NOW(),
    status              VARCHAR(20) DEFAULT 'pending_review',
    priority            VARCHAR(10) DEFAULT 'low'
);

-- ── Function: auto-update coverage_map after price observation insert ─────────
CREATE OR REPLACE FUNCTION update_coverage_map()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO coverage_map (crop, market, observation_count)
    VALUES (NEW.crop, NEW.market, 1)
    ON CONFLICT (crop, market) DO UPDATE
        SET observation_count = coverage_map.observation_count + 1,
            is_model_ready    = (coverage_map.observation_count + 1 >= coverage_map.min_observations_needed);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER trg_update_coverage
AFTER INSERT ON price_observations
FOR EACH ROW EXECUTE FUNCTION update_coverage_map();
