-- Daily UPSERT for agg_option_margin_daily
-- Engine: MySQL / MariaDB
-- Required parameter: :stat_date (DATE), :job_id (VARCHAR)

INSERT INTO agg_option_margin_daily (
    stat_date,
    product_name,
    option_name,
    delivery_fee_type,
    order_count,
    order_quantity,
    net_revenue,
    expected_settlement_amount,
    customer_paid_shipping,
    seller_shipping_burden,
    estimated_cost,
    margin_amount,
    margin_rate_pct,
    loaded_at,
    job_id
)
SELECT
    o.business_date AS stat_date,
    o.product_name,
    COALESCE(o.option_name, '') AS option_name,
    COALESCE(o.delivery_fee_type, 'UNKNOWN') AS delivery_fee_type,
    COUNT(DISTINCT o.order_id) AS order_count,
    COALESCE(SUM(o.quantity), 0) AS order_quantity,
    COALESCE(SUM(o.net_revenue), 0) AS net_revenue,
    COALESCE(SUM(o.expected_settlement_amount), 0) AS expected_settlement_amount,
    COALESCE(SUM(sr.customer_paid_shipping), 0) AS customer_paid_shipping,
    COALESCE(SUM(sr.seller_shipping_burden), 0) AS seller_shipping_burden,
    COALESCE(
        SUM((COALESCE(cm.unit_cost, 0) * COALESCE(o.quantity, 0)) + COALESCE(cm.pack_cost, 0) + COALESCE(cm.fulfillment_cost, 0)),
        0
    ) + COALESCE(SUM(sr.seller_shipping_burden), 0) AS estimated_cost,
    COALESCE(SUM(o.net_revenue), 0)
      - (
          COALESCE(
              SUM((COALESCE(cm.unit_cost, 0) * COALESCE(o.quantity, 0)) + COALESCE(cm.pack_cost, 0) + COALESCE(cm.fulfillment_cost, 0)),
              0
          ) + COALESCE(SUM(sr.seller_shipping_burden), 0)
        ) AS margin_amount,
    CASE
        WHEN COALESCE(SUM(o.net_revenue), 0) = 0 THEN 0
        ELSE (
            (
                COALESCE(SUM(o.net_revenue), 0)
                - (
                    COALESCE(
                        SUM((COALESCE(cm.unit_cost, 0) * COALESCE(o.quantity, 0)) + COALESCE(cm.pack_cost, 0) + COALESCE(cm.fulfillment_cost, 0)),
                        0
                    ) + COALESCE(SUM(sr.seller_shipping_burden), 0)
                  )
            ) / COALESCE(SUM(o.net_revenue), 0)
        ) * 100
    END AS margin_rate_pct,
    CURRENT_TIMESTAMP AS loaded_at,
    :job_id AS job_id
FROM orders o
LEFT JOIN product_option_cost_master cm
    ON cm.product_name = o.product_name
    AND cm.option_name = COALESCE(o.option_name, '')
    AND cm.is_active = 1
    AND cm.effective_from <= o.business_date
    AND (cm.effective_to IS NULL OR cm.effective_to >= o.business_date)
LEFT JOIN shipping_margin_rule sr
    ON sr.delivery_fee_type = COALESCE(o.delivery_fee_type, 'UNKNOWN')
    AND sr.is_active = 1
    AND sr.effective_from <= o.business_date
    AND (sr.effective_to IS NULL OR sr.effective_to >= o.business_date)
WHERE o.business_date = :stat_date
GROUP BY
    o.business_date,
    o.product_name,
    COALESCE(o.option_name, ''),
    COALESCE(o.delivery_fee_type, 'UNKNOWN')
ON DUPLICATE KEY UPDATE
    delivery_fee_type = VALUES(delivery_fee_type),
    order_count = VALUES(order_count),
    order_quantity = VALUES(order_quantity),
    net_revenue = VALUES(net_revenue),
    expected_settlement_amount = VALUES(expected_settlement_amount),
    customer_paid_shipping = VALUES(customer_paid_shipping),
    seller_shipping_burden = VALUES(seller_shipping_burden),
    estimated_cost = VALUES(estimated_cost),
    margin_amount = VALUES(margin_amount),
    margin_rate_pct = VALUES(margin_rate_pct),
    loaded_at = VALUES(loaded_at),
    job_id = VALUES(job_id);

