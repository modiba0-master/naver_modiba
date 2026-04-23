-- Margin management schema (option-level cost + shipping rules)
-- Date: 2026-04-23
-- Notes:
-- 1) Keep existing `orders` table as source-of-truth.
-- 2) Add master/rule/snapshot tables only.
-- 3) SQL is written for MySQL/MariaDB syntax.

CREATE TABLE IF NOT EXISTS product_option_cost_master (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    product_name VARCHAR(255) NOT NULL,
    option_name VARCHAR(255) NOT NULL,
    option_code VARCHAR(100) NULL,
    unit_cost INT NOT NULL DEFAULT 0,
    pack_cost INT NOT NULL DEFAULT 0,
    fulfillment_cost INT NOT NULL DEFAULT 0,
    default_shipping_cost INT NOT NULL DEFAULT 0,
    supplier_name VARCHAR(120) NULL,
    effective_from DATE NOT NULL,
    effective_to DATE NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    note VARCHAR(255) NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_option_cost_period (product_name, option_name, effective_from)
);

CREATE INDEX idx_option_cost_active
    ON product_option_cost_master (is_active, effective_from, effective_to);

CREATE TABLE IF NOT EXISTS shipping_margin_rule (
    rule_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    delivery_fee_type VARCHAR(80) NOT NULL,
    customer_paid_shipping INT NOT NULL DEFAULT 0,
    seller_shipping_burden INT NOT NULL DEFAULT 0,
    margin_treatment VARCHAR(40) NOT NULL DEFAULT 'include_shipping',
    effective_from DATE NOT NULL,
    effective_to DATE NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    note VARCHAR(255) NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_shipping_rule_period (delivery_fee_type, effective_from)
);

CREATE INDEX idx_shipping_rule_active
    ON shipping_margin_rule (is_active, effective_from, effective_to);

CREATE TABLE IF NOT EXISTS agg_option_margin_daily (
    stat_date DATE NOT NULL,
    product_name VARCHAR(255) NOT NULL,
    option_name VARCHAR(255) NOT NULL,
    delivery_fee_type VARCHAR(80) NULL,
    order_count INT NOT NULL DEFAULT 0,
    order_quantity INT NOT NULL DEFAULT 0,
    net_revenue BIGINT NOT NULL DEFAULT 0,
    expected_settlement_amount BIGINT NOT NULL DEFAULT 0,
    customer_paid_shipping BIGINT NOT NULL DEFAULT 0,
    seller_shipping_burden BIGINT NOT NULL DEFAULT 0,
    estimated_cost BIGINT NOT NULL DEFAULT 0,
    margin_amount BIGINT NOT NULL DEFAULT 0,
    margin_rate_pct DECIMAL(8, 3) NOT NULL DEFAULT 0.000,
    loaded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    job_id VARCHAR(64) NULL,
    PRIMARY KEY (stat_date, product_name, option_name)
);

CREATE INDEX idx_agg_option_margin_revenue
    ON agg_option_margin_daily (stat_date, net_revenue);

CREATE INDEX idx_agg_option_margin_rate
    ON agg_option_margin_daily (stat_date, margin_rate_pct);

-- -------------------------------------------------------------------
-- Seed examples (edit as needed)
-- -------------------------------------------------------------------
INSERT INTO shipping_margin_rule (
    delivery_fee_type,
    customer_paid_shipping,
    seller_shipping_burden,
    margin_treatment,
    effective_from,
    note
)
VALUES
    ('FREE', 0, 3200, 'exclude_shipping_revenue', '2026-01-01', '무료배송 기본 원가'),
    ('PAID', 3000, 3200, 'include_shipping', '2026-01-01', '유료배송 기본 원가')
ON DUPLICATE KEY UPDATE
    customer_paid_shipping = VALUES(customer_paid_shipping),
    seller_shipping_burden = VALUES(seller_shipping_burden),
    margin_treatment = VALUES(margin_treatment),
    note = VALUES(note);

