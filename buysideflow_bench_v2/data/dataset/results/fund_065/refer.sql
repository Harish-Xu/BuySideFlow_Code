WITH anchor_date AS (
    SELECT CAST('2025-12-31' AS DATETIME) AS anchor_day,
           TIMESTAMP('2026-01-01 00:00:00') AS visible_cutoff
),
type_asof AS (
    SELECT
        t.INNERCODE,
        t.SECASSETCATCODE,
        ROW_NUMBER() OVER (
            PARTITION BY t.INNERCODE
            ORDER BY t.EFFECTIVEDATE DESC, t.INSERTTIME DESC, t.ID DESC
        ) AS rn
    FROM mf_jyfundtype t
    JOIN anchor_date ad
        ON 1 = 1
    WHERE t.STANDARD = 75
      AND t.EFFECTIVEDATE <= ad.anchor_day
      AND (t.CANCELDATE IS NULL OR t.CANCELDATE > ad.anchor_day)
      AND t.INSERTTIME < ad.visible_cutoff
),
issue_version AS (
    SELECT
        il.INNERCODE,
        il.ESTABLISHMENTDATE,
        il.ACTRAISINGAMOUNT,
        il.UNITISSUEPRICE AS issue_price,
        ROW_NUMBER() OVER (
            PARTITION BY il.INNERCODE
            ORDER BY COALESCE(il.INITIALINFOPUBLDATE, il.INSERTTIME) DESC,
                     il.INSERTTIME DESC,
                     il.ID DESC
        ) AS rn
    FROM mf_issueandlisting il
    JOIN anchor_date ad
      ON 1 = 1
    WHERE (il.FUNDRAISINGMETHOD = 1 OR il.FUNDRAISINGMETHOD IS NULL)
      AND (
            (il.INITIALINFOPUBLDATE IS NOT NULL
             AND il.INITIALINFOPUBLDATE < ad.visible_cutoff)
         OR (il.INITIALINFOPUBLDATE IS NULL
             AND il.INSERTTIME < ad.visible_cutoff)
      )
),
latest_issue AS (
    SELECT
        INNERCODE,
        ESTABLISHMENTDATE,
        ACTRAISINGAMOUNT,
        issue_price
    FROM issue_version
    WHERE rn = 1
),
non_money_share AS (
    SELECT
        f.INNERCODE,
        TRIM(CAST(f.MAINCODE AS CHAR)) AS main_fund_code,
        f.SECURITYCODE,
        f.INVESTADVISORCODE,
        f.ESTABLISHMENTDATE,
        f.FOUNDEDSIZE
    FROM mf_fundarchives f
    JOIN type_asof ta
        ON ta.INNERCODE = f.INNERCODE
       AND ta.rn = 1
    JOIN anchor_date ad
        ON 1 = 1
    WHERE ta.SECASSETCATCODE <> 1402
      AND f.MAINCODE IS NOT NULL
      AND TRIM(CAST(f.MAINCODE AS CHAR)) <> ''
      AND f.ESTABLISHMENTDATE <= ad.anchor_day
      AND (f.EXPIREDATE IS NULL OR f.EXPIREDATE > ad.anchor_day)
),
main_fund_base AS (
    SELECT
        n.INNERCODE,
        n.INVESTADVISORCODE,
        n.main_fund_code,
        n.ESTABLISHMENTDATE,
        n.FOUNDEDSIZE
    FROM non_money_share n
    WHERE n.SECURITYCODE = n.main_fund_code
),
founding_share_amount AS (
    SELECT
        m.main_fund_code,
        m.INVESTADVISORCODE,
        f.INNERCODE AS share_innercode,
        CASE
            -- Amounts and prices belong to this exact share and founding event.
            -- A reported invalid amount must not recover an older/inferred value.
            WHEN li.ACTRAISINGAMOUNT IS NOT NULL
            THEN CASE WHEN li.ACTRAISINGAMOUNT >= 0 THEN li.ACTRAISINGAMOUNT END
            WHEN f.FOUNDEDSIZE = 0 THEN 0
            WHEN f.FOUNDEDSIZE > 0 AND li.issue_price > 0
            THEN f.FOUNDEDSIZE * li.issue_price
            ELSE NULL
        END AS share_founded_amount
    FROM main_fund_base m
    JOIN mf_fundarchives f
      -- Do not truncate suffixes: 485118J is not the product 485118.
      ON TRIM(CAST(f.MAINCODE AS CHAR)) = m.main_fund_code
     AND f.ESTABLISHMENTDATE = m.ESTABLISHMENTDATE
    LEFT JOIN latest_issue li
      ON li.INNERCODE = f.INNERCODE
     AND li.ESTABLISHMENTDATE = f.ESTABLISHMENTDATE
),
product_founded_amount AS (
    SELECT
        main_fund_code,
        INVESTADVISORCODE,
        CASE
            -- Partial coverage is not a product total; retain the full denominator.
            WHEN COUNT(*) = COUNT(share_founded_amount)
            THEN SUM(share_founded_amount)
        END AS founded_amount
    FROM founding_share_amount
    GROUP BY main_fund_code, INVESTADVISORCODE
),
scale_period_version AS (
    SELECT
        n.main_fund_code,
        n.SECURITYCODE,
        s.ENDDATE,
        s.TOTALASSET,
        s.INSERTTIME,
        s.ID,
        ROW_NUMBER() OVER (
            PARTITION BY n.INNERCODE, s.ENDDATE
            ORDER BY s.INSERTTIME DESC, s.ID DESC
        ) AS version_rn
    FROM non_money_share n
    JOIN mf_scaleanalysis s
        ON s.INNERCODE = n.INNERCODE
    JOIN anchor_date ad
        ON 1 = 1
    WHERE s.ENDDATE <= ad.anchor_day
      AND s.INSERTTIME < ad.visible_cutoff
),
scale_metric_row AS (
    SELECT
        main_fund_code,
        SECURITYCODE,
        ENDDATE,
        TOTALASSET,
        INSERTTIME,
        ID
    FROM scale_period_version
    -- Judge the field only after the visible version for that report period is fixed.
    WHERE version_rn = 1
      AND TOTALASSET IS NOT NULL
),
main_fund_asset AS (
    SELECT
        t.main_fund_code,
        t.TOTALASSET
    FROM (
        SELECT
            s.main_fund_code,
            s.TOTALASSET,
            ROW_NUMBER() OVER (
                PARTITION BY s.main_fund_code
                ORDER BY s.ENDDATE DESC,
                         CASE WHEN s.SECURITYCODE = s.main_fund_code THEN 0 ELSE 1 END,
                         s.INSERTTIME DESC,
                         s.ID DESC
            ) AS rn
        FROM scale_metric_row s
    ) t
    WHERE t.rn = 1
),
advisor_info AS (
    SELECT
        t.INVESTADVISORCODE,
        t.INVESTADVISORNAME
    FROM (
        SELECT
            ia.INVESTADVISORCODE,
            ia.INVESTADVISORNAME,
            ROW_NUMBER() OVER (
                PARTITION BY ia.INVESTADVISORCODE
                ORDER BY
                    CASE
                        WHEN ia.XGRQ IS NULL OR ia.XGRQ <= ad.anchor_day THEN 0
                        ELSE 1
                    END,
                    CASE
                        WHEN ia.XGRQ IS NULL OR ia.XGRQ <= ad.anchor_day THEN ia.XGRQ
                        ELSE NULL
                    END DESC,
                    ia.ID DESC
            ) AS rn
        FROM mf_investadvisoroutline ia
        JOIN anchor_date ad
            ON 1 = 1
    ) t
    WHERE t.rn = 1
),
company_stats AS (
    SELECT
        ai.INVESTADVISORNAME,
        pfa.INVESTADVISORCODE,
        COUNT(*) AS non_money_main_fund_count,
        CASE
            WHEN COUNT(*) = COUNT(pfa.founded_amount)
            THEN AVG(pfa.founded_amount)
        END AS avg_founded_amount,
        CASE
            WHEN COUNT(*) = COUNT(mfa.TOTALASSET)
            THEN SUM(mfa.TOTALASSET)
        END AS total_latest_asset
    FROM product_founded_amount pfa
    LEFT JOIN main_fund_asset mfa
        ON mfa.main_fund_code = pfa.main_fund_code
    LEFT JOIN advisor_info ai
        ON ai.INVESTADVISORCODE = pfa.INVESTADVISORCODE
    GROUP BY ai.INVESTADVISORNAME, pfa.INVESTADVISORCODE
    HAVING COUNT(*) >= 10
)
SELECT
    INVESTADVISORNAME,
    non_money_main_fund_count,
    ROUND(avg_founded_amount / 100000000, 2) AS avg_founded_size_yi,
    ROUND(total_latest_asset / 100000000, 2) AS total_latest_asset_yi
FROM company_stats
-- Company code only breaks ties after the two ordering keys requested by the task.
ORDER BY total_latest_asset DESC, non_money_main_fund_count DESC, INVESTADVISORCODE;
