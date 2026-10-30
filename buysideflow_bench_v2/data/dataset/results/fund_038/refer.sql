-- 截至 as_of_date 已披露的最近 6 期定期报告买卖股票数据
WITH params AS (
    SELECT DATE('2025-07-18') AS as_of_date
),
tf AS (
    SELECT INNERCODE, SECUCODE
    FROM mf_fundarchives
    WHERE SECUCODE = '005739'
),
trade_raw AS (
    SELECT
        ti.REPORTDATE AS report_date,
        ti.INFOPUBLDATE AS pub_date,
        ti.BUYINGCOST / 100000000 AS buy_cost_100m,
        ti.SELLINGINCOME / 100000000 AS sell_income_100m,
        (ti.BUYINGCOST - ti.SELLINGINCOME) / 100000000 AS net_buy_100m,
        ROW_NUMBER() OVER (
            PARTITION BY ti.REPORTDATE
            ORDER BY ti.INFOPUBLDATE DESC, ti.INSERTTIME DESC, ti.ID DESC
        ) AS rn
    FROM mf_fundtradeinfo ti
    INNER JOIN tf ON tf.INNERCODE = ti.INNERCODE
    CROSS JOIN params p
    WHERE ti.REPORTDATE <= p.as_of_date
      AND ti.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND ti.BUYINGCOST IS NOT NULL
      AND ti.SELLINGINCOME IS NOT NULL
),
latest_trade AS (
    SELECT
        report_date,
        pub_date,
        buy_cost_100m,
        sell_income_100m,
        net_buy_100m
    FROM trade_raw
    WHERE rn = 1
),
metrics AS (
    SELECT
        rr.*,
        LAG(rr.net_buy_100m) OVER (ORDER BY rr.report_date) AS prev_net_buy_100m
    FROM latest_trade rr
),
recent_reports AS (
    SELECT *
    FROM metrics
    ORDER BY report_date DESC
    LIMIT 6
)
SELECT
    '005739' AS `基金代码`,
    DATE_FORMAT(report_date, '%Y-%m-%d') AS `报告期`,
    DATE_FORMAT(pub_date, '%Y-%m-%d') AS `公告日`,
    ROUND(buy_cost_100m, 2) AS `买入成本（亿元）`,
    ROUND(sell_income_100m, 2) AS `卖出收入（亿元）`,
    ROUND(net_buy_100m, 2) AS `净买入额（亿元）`,
    ROUND(net_buy_100m - prev_net_buy_100m, 2) AS `净买入额环比变动（亿元）`
FROM recent_reports
ORDER BY report_date;
