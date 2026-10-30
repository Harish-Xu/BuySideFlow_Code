WITH target_product AS (
    SELECT
        '金泰产融(北京)创业投资基金管理有限公司2025年度第一期本钢北营绿色科技创新定向资产支持证券(类REITs)' AS product_name
),
target_tranches AS (
    SELECT
        b.ACTUALISSUESIZE,
        b.COUPONRATE,
        b.ABSLEVEL
    FROM bond_basicinfo b
    CROSS JOIN target_product t
    WHERE b.ISSUEDATE = '2025-09-24'
      AND b.BONDFULLNAME IN (
          CONCAT(t.product_name, '优先级'),
          CONCAT(t.product_name, '次级')
      )
)
SELECT
    ROUND(SUM(ACTUALISSUESIZE), 4) AS `发行规模（亿元）`,
    ROUND(MAX(CASE WHEN ABSLEVEL = 12 THEN COUPONRATE END), 4) AS `票面利率（%）`
FROM target_tranches;
