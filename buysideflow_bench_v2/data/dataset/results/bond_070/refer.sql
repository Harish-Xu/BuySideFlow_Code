WITH term_rows AS (
    SELECT
        c.INNERCODE,
        c.CALLLEVEL,
        c.CALLCONDITIONDAY,
        c.CALLREACHDAY,
        c.CALLSTARTDATE,
        c.CALLENDDATE,
        c.PAYMENTYEARSEQUENCE,
        c.PAYMENTYEARSTARTDATE,
        c.PAYMENTYEARENDDATE,
        c.INSERTTIME,
        c.UPDATETIME,
        c.ID
    FROM bond_conbdcallinfo c FORCE INDEX (IX_BOND_CONBDCALLINFO)
    -- Retain historical versions, including incomplete revisions. Python chooses
    -- the visible version for each signal day before checking the trigger fields.
    WHERE c.CALLSTARTDATE IS NOT NULL
      AND DATE(c.CALLSTARTDATE) <= DATE('2025-06-30')
      AND COALESCE(c.MULTILEVEL, 1) = 1
      -- INSERTTIME is this table's publication time; maintenance time is not a substitute.
      AND c.INSERTTIME IS NOT NULL
      AND c.INSERTTIME < TIMESTAMP('2025-07-01 00:00:00')
),
code_ranked AS (
    SELECT
        bc.INNERCODE,
        bc.SECUCODE,
        bc.SECUABBR,
        bc.LISTEDDATE,
        bc.DELISTDATE,
        ROW_NUMBER() OVER (
            PARTITION BY bc.INNERCODE
            ORDER BY bc.UPDATETIME DESC, bc.ID DESC
        ) AS rn
    FROM bond_code bc
    WHERE bc.BONDTYPELEVEL1DESC = '可转债'
      AND bc.BONDTYPELEVEL2DESC = '可转债'
),
basic_ranked AS (
    SELECT
        b.INNERCODE,
        b.BONDFORM,
        b.CONVTERMSTARTDATE,
        b.LISTEDDATE,
        b.ENDDATE,
        b.LISTEDENDDATE,
        b.TRADEENDDATE,
        b.LASTTRADINGDATE,
        b.DELISTDATE,
        ROW_NUMBER() OVER (
            PARTITION BY b.INNERCODE
            ORDER BY b.UPDATETIME DESC, b.ID DESC
        ) AS rn
    FROM bond_conbdbasicinfo b
    WHERE b.CONVTERMSTARTDATE IS NOT NULL
      AND DATE(b.CONVTERMSTARTDATE) <= DATE('2025-06-30')
)
SELECT
    bc.INNERCODE,
    bc.SECUCODE,
    bc.SECUABBR,
    CASE WHEN t.CALLLEVEL > 10 THEN t.CALLLEVEL / 100 ELSE t.CALLLEVEL END AS call_trigger_ratio,
    t.CALLCONDITIONDAY AS window_days,
    t.CALLREACHDAY AS required_hit_days,
    DATE(t.CALLSTARTDATE) AS call_start_date,
    DATE(t.CALLENDDATE) AS call_end_date,
    DATE(b.CONVTERMSTARTDATE) AS convert_start_date,
    t.PAYMENTYEARSEQUENCE AS payment_year_sequence,
    DATE(t.PAYMENTYEARSTARTDATE) AS payment_year_start_date,
    DATE(t.PAYMENTYEARENDDATE) AS payment_year_end_date,
    t.INSERTTIME AS term_publish_time,
    t.UPDATETIME AS term_update_time,
    t.ID AS term_id
FROM term_rows t
JOIN code_ranked bc
  ON t.INNERCODE = bc.INNERCODE
 AND bc.rn = 1
JOIN basic_ranked b
  ON t.INNERCODE = b.INNERCODE
 AND b.rn = 1
WHERE b.BONDFORM = 1
  AND (bc.LISTEDDATE IS NULL OR DATE(bc.LISTEDDATE) <= DATE('2025-06-30'))
  AND (bc.DELISTDATE IS NULL OR DATE(bc.DELISTDATE) >= DATE('2025-01-01'))
  AND (b.LISTEDDATE IS NULL OR DATE(b.LISTEDDATE) <= DATE('2025-06-30'))
  AND (b.ENDDATE IS NULL OR DATE(b.ENDDATE) >= DATE('2025-01-01'))
  AND (b.LISTEDENDDATE IS NULL OR DATE(b.LISTEDENDDATE) >= DATE('2025-01-01'))
  AND (b.TRADEENDDATE IS NULL OR DATE(b.TRADEENDDATE) >= DATE('2025-01-01'))
  AND (b.LASTTRADINGDATE IS NULL OR DATE(b.LASTTRADINGDATE) >= DATE('2025-01-01'))
  AND (b.DELISTDATE IS NULL OR DATE(b.DELISTDATE) >= DATE('2025-01-01'))
ORDER BY bc.SECUCODE, t.CALLSTARTDATE, t.PAYMENTYEARSEQUENCE, t.ID;
