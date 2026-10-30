WITH params AS (
    SELECT DATE('2025-05-08') AS anchor_date
),
target_fund AS (
    SELECT
        sm.INNERCODE,
        sm.SECUCODE AS fund_code,
        sm.SECUABBR AS fund_name
    FROM secumain sm
    WHERE sm.SECUCODE = '420002'
      AND sm.SECUCATEGORY = 8
    ORDER BY sm.INNERCODE
    LIMIT 1
),
visible_rules AS (
    SELECT
        p.ENDDATE,
        p.INSERTTIME AS info_pub_date,
        p.APPLYINGTYPE,
        p.REDEEMTYPE,
        p.LARGEAPPLYINGMAX,
        ROW_NUMBER() OVER (
            ORDER BY p.ENDDATE DESC,
                     p.INSERTTIME DESC,
                     p.INSERTTIME DESC,
                     p.ID DESC
        ) AS rn
    FROM mf_purchaseandredeem p
    JOIN target_fund tf
      ON tf.INNERCODE = p.INNERCODE
    JOIN params prm
      ON 1 = 1
    WHERE p.ENDDATE <= prm.anchor_date
      AND p.INSERTTIME < DATE_ADD(prm.anchor_date, INTERVAL 1 DAY)
)
SELECT
    tf.fund_code,
    tf.fund_name,
    DATE(vr.ENDDATE) AS rule_date,
    vr.APPLYINGTYPE AS applying_type,
    vr.REDEEMTYPE AS redeem_type,
    vr.LARGEAPPLYINGMAX AS large_applying_max
FROM visible_rules vr
JOIN target_fund tf
  ON 1 = 1
WHERE vr.rn = 1;
