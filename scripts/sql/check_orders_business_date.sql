-- 매출 귀속일(business_date)별 orders 존재 여부·합계 확인
-- 사용: 클라이언트에서 :bd 에 날짜 바인딩 (예: 2026-04-21)
-- MySQL / MariaDB / SQLite 공통 COUNT·SUM

SELECT
    business_date,
    COUNT(*) AS row_count,
    COUNT(DISTINCT order_id) AS distinct_product_orders,
    COALESCE(SUM(amount), 0) AS sum_amount
FROM orders
WHERE business_date = :bd;
