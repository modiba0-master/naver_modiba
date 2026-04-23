-- Option cost upsert by effective date (daily change friendly)
-- Engine: MySQL / MariaDB
-- Usage:
-- 1) Replace variables below
-- 2) Run in a transaction

START TRANSACTION;

-- Example input values
SET @p_product_name = '친절한 닭가슴살';
SET @p_option_name = '기본 1kg';
SET @p_option_code = 'CHK-01';
SET @p_unit_cost = 5800;
SET @p_pack_cost = 260;
SET @p_fulfillment_cost = 190;
SET @p_default_shipping_cost = 3200;
SET @p_supplier_name = '기본공급처';
SET @p_effective_from = DATE('2026-04-24');
SET @p_note = '원가 변동 반영';

-- 1) Close previous active period if exists
UPDATE product_option_cost_master
SET
    effective_to = DATE_SUB(@p_effective_from, INTERVAL 1 DAY),
    is_active = 0
WHERE product_name = @p_product_name
  AND option_name = @p_option_name
  AND is_active = 1
  AND effective_from < @p_effective_from;

-- 2) Insert new active period row
INSERT INTO product_option_cost_master (
    product_name,
    option_name,
    option_code,
    unit_cost,
    pack_cost,
    fulfillment_cost,
    default_shipping_cost,
    supplier_name,
    effective_from,
    effective_to,
    is_active,
    note
)
VALUES (
    @p_product_name,
    @p_option_name,
    @p_option_code,
    @p_unit_cost,
    @p_pack_cost,
    @p_fulfillment_cost,
    @p_default_shipping_cost,
    @p_supplier_name,
    @p_effective_from,
    NULL,
    1,
    @p_note
)
ON DUPLICATE KEY UPDATE
    option_code = VALUES(option_code),
    unit_cost = VALUES(unit_cost),
    pack_cost = VALUES(pack_cost),
    fulfillment_cost = VALUES(fulfillment_cost),
    default_shipping_cost = VALUES(default_shipping_cost),
    supplier_name = VALUES(supplier_name),
    effective_to = VALUES(effective_to),
    is_active = VALUES(is_active),
    note = VALUES(note);

COMMIT;

