-- MariaDB/MySQL: business_date가 payment_date(한국시간) 16시 규칙과 일치하는지 검증.
-- 기대 건수: 0
-- 컬럼명: payment_date (paid_at 아님)

SELECT COUNT(*) AS mismatch_count
FROM orders
WHERE payment_date IS NOT NULL
  AND business_date <>
      CASE
        WHEN HOUR(payment_date) >= 16 THEN DATE_ADD(DATE(payment_date), INTERVAL 1 DAY)
        ELSE DATE(payment_date)
      END;
