WITH report_params AS (
    SELECT
        DATE('2025-12-31') AS report_date,
        DATE('2026-02-27') AS publish_cutoff
),
fund_pool AS (
    SELECT
        fa.INNERCODE,
        sm.SECUCODE,
        sm.SECUABBR
    FROM mf_fundarchives fa
    JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
    JOIN report_params rp
      ON 1 = 1
    WHERE sm.SECUCATEGORY = 8
      AND fa.ESTABLISHMENTDATE <= rp.report_date
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE > rp.report_date)
),
natural_latest AS (
    SELECT
        s.INNERCODE,
        s.SERIALNUMBER,
        COALESCE(s.APPLYINGSHARES, 0) AS applying_shares,
        COALESCE(s.REDEEMSHARES, 0) AS redeem_shares,
        ROW_NUMBER() OVER (
            PARTITION BY s.INNERCODE, s.SERIALNUMBER
            ORDER BY s.INSERTTIME DESC, s.ID DESC
        ) AS rn_dup
    FROM mf_shinfluence s
    JOIN fund_pool fp
      ON fp.INNERCODE = s.INNERCODE
    JOIN report_params rp
      ON 1 = 1
    WHERE s.ENDDATE >= rp.report_date
      AND s.ENDDATE < DATE_ADD(rp.report_date, INTERVAL 1 DAY)
      AND s.HOLDERNATURE = 1
      AND (s.INFOPUBLDATE IS NULL OR s.INFOPUBLDATE < DATE_ADD(rp.publish_cutoff, INTERVAL 1 DAY))
      AND s.INSERTTIME < DATE_ADD(rp.publish_cutoff, INTERVAL 1 DAY)
),
natural_net AS (
    SELECT
        INNERCODE,
        SUM(applying_shares - redeem_shares) AS net_shares
    FROM natural_latest
    WHERE rn_dup = 1
    GROUP BY INNERCODE
),
top_subscribe AS (
    SELECT
        '自然人净申购份额最高' AS metric,
        fp.SECUCODE AS fund_code,
        fp.SECUABBR AS fund_name,
        ROUND(nn.net_shares, 2) AS net_shares
    FROM natural_net nn
    JOIN fund_pool fp
      ON fp.INNERCODE = nn.INNERCODE
    WHERE nn.net_shares > 0
    ORDER BY nn.net_shares DESC, fp.SECUCODE ASC
    LIMIT 1
),
top_redeem AS (
    SELECT
        '自然人净赎回份额最高' AS metric,
        fp.SECUCODE AS fund_code,
        fp.SECUABBR AS fund_name,
        ROUND(nn.net_shares, 2) AS net_shares
    FROM natural_net nn
    JOIN fund_pool fp
      ON fp.INNERCODE = nn.INNERCODE
    WHERE nn.net_shares < 0
    ORDER BY nn.net_shares ASC, fp.SECUCODE ASC
    LIMIT 1
)
SELECT
    metric,
    fund_code,
    fund_name,
    net_shares
FROM top_subscribe
UNION ALL
SELECT
    metric,
    fund_code,
    fund_name,
    net_shares
FROM top_redeem
ORDER BY metric, fund_code;
