-- Sample seed for product_option_cost_master (edit names/costs before running)
-- Date: 2026-04-23

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
    is_active,
    note
)
VALUES
-- 1) 주력옵션 A (유료배송)
('친절한 닭가슴살', '기본 1kg', 'CHK-01', 5600, 250, 180, 3200, '기본공급처', '2026-04-01', 1, '샘플-주력A'),
-- 2) 주력옵션 B (무료배송)
('친절한 닭가슴살', '2kg 묶음', 'CHK-02', 10500, 320, 220, 3200, '기본공급처', '2026-04-01', 1, '샘플-주력B'),
-- 3) 묶음배송 옵션
('친절한 닭안심', '1kg 2팩', 'ANS-02', 9300, 330, 230, 3200, '안심공급처', '2026-04-01', 1, '샘플-묶음배송'),
-- 4) 저마진 의심 옵션
('친절한 닭안심', '소용량 500g', 'ANS-01', 4800, 260, 180, 3200, '안심공급처', '2026-04-01', 1, '샘플-저마진의심'),
-- 5) 신규 테스트 옵션
('친절한 아이스팩', '16X23', 'ICE-1623', 400, 80, 40, 0, '부자재공급처', '2026-04-01', 1, '샘플-신규테스트')
ON DUPLICATE KEY UPDATE
    option_code = VALUES(option_code),
    unit_cost = VALUES(unit_cost),
    pack_cost = VALUES(pack_cost),
    fulfillment_cost = VALUES(fulfillment_cost),
    default_shipping_cost = VALUES(default_shipping_cost),
    supplier_name = VALUES(supplier_name),
    is_active = VALUES(is_active),
    note = VALUES(note);

