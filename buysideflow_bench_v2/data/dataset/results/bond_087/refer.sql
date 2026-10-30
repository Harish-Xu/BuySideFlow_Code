WITH params AS (
    SELECT
        DATE('2025-01-01') AS issue_start,
        DATE('2026-01-01') AS issue_end,
        TIMESTAMP('2026-01-31 00:00:00') AS data_cutoff_ts
),
first_issue_ranked AS (
    SELECT
        CAST(i.MAINCODE AS UNSIGNED) AS MAINCODE,
        DATE(i.BIDSTARTDATE) AS issue_event_date,
        i.ISSUEPRICE AS issue_price_from_issue,
        i.PARVALUE AS parvalue_from_issue,
        ROW_NUMBER() OVER (
            PARTITION BY i.MAINCODE
            ORDER BY
                i.BIDSTARTDATE ASC,
                i.INITIALINFOPUBLDATE DESC,
                i.UPDATETIME DESC,
                i.ID ASC
        ) AS rn
    FROM bond_issuenew i
    JOIN params p
      ON 1 = 1
    WHERE i.ISSUETYPE = 1
      AND i.BIDSTARTDATE IS NOT NULL
      AND (
            (i.INITIALINFOPUBLDATE IS NOT NULL
             AND i.INITIALINFOPUBLDATE < p.data_cutoff_ts)
         OR (i.INITIALINFOPUBLDATE IS NULL
             AND i.UPDATETIME < p.data_cutoff_ts)
      )
),
first_issue AS (
    SELECT
        MAINCODE,
        issue_event_date,
        issue_price_from_issue,
        parvalue_from_issue
    FROM first_issue_ranked
    WHERE rn = 1
),
basic_ranked AS (
    SELECT
        CAST(b.MAINCODE AS UNSIGNED) AS MAINCODE,
        CAST(b.INNERCODE AS UNSIGNED) AS basic_innercode,
        b.ISSUER,
        b.BONDFULLNAME,
        b.MATURITY,
        b.ISSUEREFYTM,
        b.COUPONRATE,
        b.PARVALUE,
        b.ISSUEPRICE,
        b.RATEIFFLOAT,
        b.COMPOUNDMETHOD,
        b.FRNREFRATEPER,
        b.FRNMARGIN,
        DATE(b.ISSUEDATE) AS basic_issue_date,
        DATE(b.LISTEDDATE) AS basic_listed_date,
        b.UPDATETIME,
        ROW_NUMBER() OVER (
            PARTITION BY b.MAINCODE
            ORDER BY COALESCE(b.INITIALINFOPUBLDATE, b.UPDATETIME) DESC,
                     b.UPDATETIME DESC,
                     b.ID DESC
        ) AS rn
    FROM bond_basicinfo b
    JOIN params p
      ON (
            (b.INITIALINFOPUBLDATE IS NOT NULL
             AND b.INITIALINFOPUBLDATE < p.data_cutoff_ts)
         OR (b.INITIALINFOPUBLDATE IS NULL
             AND b.UPDATETIME < p.data_cutoff_ts)
      )
),
code_ranked AS (
    SELECT
        CAST(c.MAINCODE AS UNSIGNED) AS MAINCODE,
        CAST(c.INNERCODE AS UNSIGNED) AS INNERCODE,
        c.SECUCODE,
        c.SECUABBR,
        c.SECUMARKET,
        DATE(c.LISTEDDATE) AS code_listed_date,
        ROW_NUMBER() OVER (
            PARTITION BY c.MAINCODE
            ORDER BY
                CASE WHEN c.SECUMARKET = 89 THEN 0 ELSE 1 END,
                c.LISTEDDATE ASC,
                c.INNERCODE ASC
        ) AS rn
    FROM bond_code c
    WHERE c.INNERCODE IS NOT NULL
      AND c.LISTEDDATE IS NOT NULL
      AND c.SECUMARKET = 89
      AND c.BONDTYPELEVEL2 = 1201
)
SELECT
    b.MAINCODE,
    COALESCE(c.INNERCODE, b.basic_innercode) AS INNERCODE,
    COALESCE(c.SECUCODE, CAST(COALESCE(c.INNERCODE, b.basic_innercode) AS CHAR)) AS bond_code,
    COALESCE(c.SECUABBR, b.BONDFULLNAME) AS bond_name,
    c.SECUMARKET,
    b.ISSUER,
    '国开债' AS bond_type,
    195 AS curve_code,
    i.issue_event_date AS issue_start_date,
    COALESCE(c.code_listed_date, b.basic_listed_date) AS listed_date,
    b.MATURITY AS issue_maturity_years,
    b.COUPONRATE AS issue_yield_pct,
    b.COUPONRATE AS issue_ref_ytm_pct,
    b.COUPONRATE AS coupon_rate_pct,
    COALESCE(i.issue_price_from_issue, b.ISSUEPRICE) AS issue_price,
    COALESCE(i.parvalue_from_issue, b.PARVALUE) AS par_value,
    b.RATEIFFLOAT,
    b.COMPOUNDMETHOD
FROM basic_ranked b
JOIN first_issue i
  ON i.MAINCODE = b.MAINCODE
JOIN code_ranked c
  ON b.MAINCODE = c.MAINCODE
 AND c.rn = 1
WHERE b.rn = 1
  AND i.issue_event_date >= (SELECT issue_start FROM params)
  AND i.issue_event_date < (SELECT issue_end FROM params)
  AND b.ISSUER LIKE '%国家开发银行%'
  AND COALESCE(c.code_listed_date, b.basic_listed_date) IS NOT NULL
  AND COALESCE(c.INNERCODE, b.basic_innercode) IS NOT NULL
  AND b.MATURITY >= 3
  AND b.MATURITY <= 10
  AND COALESCE(i.issue_price_from_issue, b.ISSUEPRICE) = 100
  AND COALESCE(i.parvalue_from_issue, b.PARVALUE) = 100
  AND b.COUPONRATE IS NOT NULL
  AND b.COUPONRATE > 0
  AND b.RATEIFFLOAT = 2
  AND b.COMPOUNDMETHOD = 1
  AND b.FRNREFRATEPER IS NULL
  AND b.FRNMARGIN IS NULL
ORDER BY bond_type, issue_start_date, b.MAINCODE;
