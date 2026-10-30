WITH params AS (
    SELECT
        DATE('2025-10-31') AS as_of_date,
        DATE('2025-11-01') AS visible_cutoff
),
visible_holder AS (
    SELECT
        hi.*
    FROM mf_holderinfo hi
    JOIN params p ON 1 = 1
    WHERE hi.ENDDATE IN ('2024-12-31', '2025-06-30')
      AND hi.INFOPUBLDATE < p.visible_cutoff
),
holder_latest AS (
    SELECT *
    FROM (
        SELECT
            vh.*,
            ROW_NUMBER() OVER (
                PARTITION BY vh.INNERCODE, vh.ENDDATE
                ORDER BY vh.INFOPUBLDATE DESC, vh.JSID DESC
            ) AS rn
        FROM visible_holder vh
    ) x
    WHERE rn = 1
),
scale_latest AS (
    SELECT INNERCODE, ENDDATE, TOTALSHARES
    FROM (
        SELECT
            sa.INNERCODE,
            sa.ENDDATE,
            sa.TOTALSHARES,
            ROW_NUMBER() OVER (
                PARTITION BY sa.INNERCODE, sa.ENDDATE
                ORDER BY sa.INSERTTIME DESC, sa.ID DESC
            ) AS rn
        FROM mf_scaleanalysis sa
        JOIN params p ON 1 = 1
        WHERE sa.ENDDATE IN ('2024-12-31', '2025-06-30')
          AND sa.INSERTTIME < p.visible_cutoff
    ) x
    WHERE rn = 1
),
share_holder AS (
    SELECT
        COALESCE(fa.MAINCODE, fa.SECURITYCODE) AS main_code,
        fa.SECURITYCODE AS share_code,
        hl.ENDDATE,
        hl.INSTITUTIONHOLDSHARES,
        hl.INSTITUTIONHOLDRATIO,
        hl.INDIVIDUALHOLDSHARES,
        hl.INDIVIDUALHOLDRATIO,
        hl.UNDEFINEDHOLDSHARES,
        hl.UNDEFINEDHOLDRATIO,
        COALESCE(
            CASE
                WHEN sl.TOTALSHARES > 0
                 AND ABS(
                        sl.TOTALSHARES
                        - (
                            COALESCE(hl.INSTITUTIONHOLDSHARES, 0)
                          + COALESCE(hl.INDIVIDUALHOLDSHARES, 0)
                          + COALESCE(hl.UNDEFINEDHOLDSHARES, 0)
                        )
                     ) <= GREATEST(1, sl.TOTALSHARES * 0.0001)
                THEN sl.TOTALSHARES
            END,
            CASE
                WHEN hl.INSTITUTIONHOLDRATIO > 0 AND hl.INSTITUTIONHOLDSHARES IS NOT NULL
                THEN hl.INSTITUTIONHOLDSHARES * 100 / hl.INSTITUTIONHOLDRATIO
            END,
            CASE
                WHEN hl.INDIVIDUALHOLDRATIO > 0 AND hl.INDIVIDUALHOLDSHARES IS NOT NULL
                THEN hl.INDIVIDUALHOLDSHARES * 100 / hl.INDIVIDUALHOLDRATIO
            END,
            CASE
                WHEN hl.UNDEFINEDHOLDRATIO > 0 AND hl.UNDEFINEDHOLDSHARES IS NOT NULL
                THEN hl.UNDEFINEDHOLDSHARES * 100 / hl.UNDEFINEDHOLDRATIO
            END,
            COALESCE(hl.INSTITUTIONHOLDSHARES, 0)
              + COALESCE(hl.INDIVIDUALHOLDSHARES, 0)
              + COALESCE(hl.UNDEFINEDHOLDSHARES, 0)
        ) AS total_shares_est
    FROM holder_latest hl
    JOIN mf_fundarchives fa
        ON fa.INNERCODE = hl.INNERCODE
    LEFT JOIN scale_latest sl
        ON sl.INNERCODE = hl.INNERCODE
       AND sl.ENDDATE = hl.ENDDATE
),
share_holder_filled AS (
    SELECT
        main_code,
        share_code,
        ENDDATE,
        COALESCE(
            INSTITUTIONHOLDSHARES,
            CASE
                WHEN INSTITUTIONHOLDRATIO IS NOT NULL
                THEN total_shares_est * INSTITUTIONHOLDRATIO / 100
            END
        ) AS institution_shares,
        total_shares_est
    FROM share_holder
    WHERE main_code IS NOT NULL
),
main_period_ratio AS (
    SELECT
        main_code,
        ENDDATE,
        SUM(institution_shares) AS institution_shares,
        SUM(total_shares_est) AS total_shares,
        CASE
            WHEN COUNT(institution_shares) = COUNT(*)
             AND COUNT(total_shares_est) = COUNT(*)
             AND MIN(total_shares_est) >= 0
             AND SUM(total_shares_est) > 0
            THEN SUM(institution_shares) / SUM(total_shares_est) * 100
        END AS institution_ratio
    FROM share_holder_filled
    GROUP BY main_code, ENDDATE
),
pivoted AS (
    SELECT
        main_code,
        MAX(CASE WHEN ENDDATE = '2024-12-31' THEN institution_ratio END) AS ratio_2024a,
        MAX(CASE WHEN ENDDATE = '2025-06-30' THEN institution_ratio END) AS ratio_2025h1
    FROM main_period_ratio
    GROUP BY main_code
),
screened AS (
    SELECT
        p.main_code,
        p.ratio_2024a,
        p.ratio_2025h1,
        p.ratio_2025h1 - p.ratio_2024a AS ratio_change
    FROM pivoted p
    WHERE p.ratio_2024a IS NOT NULL
      AND p.ratio_2025h1 IS NOT NULL
      AND p.ratio_2025h1 >= 50
      AND p.ratio_2025h1 - p.ratio_2024a <= -30
)
SELECT
    s.main_code AS `基金代码`,
    sm.SECUABBR AS `基金简称`,
    ROUND(s.ratio_2024a, 2) AS `2024A机构持有比例(%)`,
    ROUND(s.ratio_2025h1, 2) AS `2025H1机构持有比例(%)`,
    ROUND(s.ratio_change, 2) AS `机构持有比例变化(pct)`
FROM screened s
JOIN mf_fundarchives main_fa
    ON main_fa.SECURITYCODE = s.main_code
   AND main_fa.MAINCODE = main_fa.SECURITYCODE
JOIN secumain sm
    ON sm.INNERCODE = main_fa.INNERCODE
   AND sm.SECUCATEGORY = 8
JOIN params p ON 1 = 1
WHERE main_fa.ESTABLISHMENTDATE <= p.as_of_date
  AND (main_fa.EXPIREDATE IS NULL OR main_fa.EXPIREDATE >= p.as_of_date)
  AND (main_fa.INSERTTIME IS NULL OR main_fa.INSERTTIME < p.visible_cutoff)
ORDER BY s.ratio_change ASC, s.main_code ASC;
