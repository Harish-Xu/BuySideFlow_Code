WITH issuer_codes AS (
    SELECT DISTINCT
        bc.MAINCODE,
        bc.INNERCODE,
        bc.SECUCODE,
        bc.SECUABBR,
        bc.SECUMARKET,
        bc.COMPANYCODE,
        bc.ISSUER,
        bc.LISTEDDATE,
        bc.INTERESTENDDATE,
        bc.DELISTDATE
    FROM bond_code bc
    WHERE bc.COMPANYCODE = 74749
      AND bc.MAINCODE IS NOT NULL
      AND bc.INNERCODE IS NOT NULL
      AND (
            bc.BONDTYPELEVEL1 IN (1600,1700,1800,2500,2600,2800,2900,3100,3200,3300,3700)
         OR bc.BONDTYPELEVEL2 IN (1901,1903)
      )
      AND (bc.LISTEDDATE IS NULL OR bc.LISTEDDATE <= '2024-05-27')
      AND (bc.INTERESTENDDATE IS NULL OR bc.INTERESTENDDATE > '2024-05-27')
      AND (bc.DELISTDATE IS NULL OR bc.DELISTDATE > '2024-05-27')
),
val_daily_ranked AS (
    SELECT
        c.MAINCODE,
        c.INNERCODE,
        c.SECUCODE,
        c.SECUMARKET,
        DATE(v.ENDDATE) AS val_date,
        v.VPYIELD,
        v.TRUEYIELD,
        ROW_NUMBER() OVER (
            PARTITION BY c.MAINCODE, DATE(v.ENDDATE)
            ORDER BY
                CASE WHEN c.INNERCODE = c.MAINCODE THEN 0 ELSE 1 END,
                CASE c.SECUMARKET WHEN 89 THEN 1 WHEN 83 THEN 2 WHEN 90 THEN 3 ELSE 9 END,
                c.SECUCODE,
                v.UPDATETIME DESC,
                v.ID DESC
        ) AS rn
    FROM issuer_codes c
    JOIN bond_cbvaluationall v FORCE INDEX (IX_BOND_CBVALUATIONALL)
     ON v.INNERCODE = c.INNERCODE
     AND v.ENDDATE < '2024-05-28'
     AND v.ENDDATE >= DATE_SUB('2024-05-27', INTERVAL 120 DAY)
     AND v.CREDIBILITYCODE = 1
     AND v.YIELDCODE = 2
    WHERE v.VPYIELD IS NOT NULL
      AND (v.INSERTTIME IS NULL OR v.INSERTTIME < DATE_ADD(DATE('2024-05-27'), INTERVAL 1 DAY))
      AND v.UPDATETIME < DATE_ADD(DATE('2024-05-27'), INTERVAL 1 DAY)
),
val_daily AS (
    SELECT *
    FROM val_daily_ranked
    WHERE rn = 1
),
val_last5 AS (
    SELECT
        d.*,
        ROW_NUMBER() OVER (PARTITION BY d.MAINCODE ORDER BY d.val_date DESC) AS val_rn
    FROM val_daily d
),
val_summary AS (
    SELECT
        MAINCODE,
        MAX(CASE WHEN val_rn = 1 THEN SECUCODE END) AS bond_code,
        (MAX(CASE WHEN val_rn = 1 THEN VPYIELD END) - MAX(CASE WHEN val_rn = 5 THEN VPYIELD END)) * 100 AS yield_change_bp,
        COUNT(*) AS last5_points
    FROM val_last5
    WHERE val_rn <= 5
    GROUP BY MAINCODE
    HAVING last5_points = 5
),
trade_calendar AS (
    SELECT DISTINCT DATE(TRADINGDATE) AS trade_date
    FROM qt_tradingdaynew
    WHERE SECUMARKET IN (83, 90)
      AND IFTRADINGDAY = 1
      AND TRADINGDATE >= '2024-01-28'
      AND TRADINGDATE < '2024-05-28'
    ORDER BY trade_date DESC
    LIMIT 20
),
trade_window AS (
    SELECT
        d.MAINCODE,
        d.val_date,
        d.VPYIELD,
        d.TRUEYIELD
    FROM val_daily d
    JOIN trade_calendar c ON c.trade_date = d.val_date
),
trade_deviation AS (
    SELECT
        MAINCODE,
        AVG((TRUEYIELD - VPYIELD) * 100) AS avg_trade_deviation_bp
    FROM trade_window
    WHERE TRUEYIELD IS NOT NULL
    GROUP BY MAINCODE
),
bond_rating AS (
    SELECT MAINCODE, CRDESC, INFOPUBLDATE
    FROM (
        SELECT
            r.MAINCODE,
            r.CRDESC,
            r.INFOPUBLDATE,
            ROW_NUMBER() OVER (
                PARTITION BY r.MAINCODE
                ORDER BY r.INFOPUBLDATE DESC, r.CRDATE DESC, r.ID DESC
            ) AS rn
        FROM bond_bdcreditgrading r
        JOIN val_summary s
          ON s.MAINCODE = r.MAINCODE
        WHERE r.INFOPUBLDATE < DATE_ADD(DATE('2024-05-27'), INTERVAL 1 DAY)
          AND r.CRTYPECODE = 2
          AND r.CRSYSTEM = 1
          AND r.CRSTATUS = 1
          AND r.CRDESC IS NOT NULL
          AND (r.RATINGEFFECTIVEENDDATE IS NULL OR r.RATINGEFFECTIVEENDDATE >= '2024-05-27')
    ) x
    WHERE rn = 1
),
subject_rating AS (
    SELECT COMPANYCODE, CRDESC, INFOPUBLDATE
    FROM (
        SELECT
            r.COMPANYCODE,
            r.CRDESC,
            r.INFOPUBLDATE,
            ROW_NUMBER() OVER (
                PARTITION BY r.COMPANYCODE
                ORDER BY r.INFOPUBLDATE DESC, r.CRDATE DESC, r.ID DESC
            ) AS rn
        FROM bond_comcreditgrading r
        WHERE r.COMPANYCODE = 74749
          AND r.INFOPUBLDATE < DATE_ADD(DATE('2024-05-27'), INTERVAL 1 DAY)
          AND r.CRTYPE = 3
          AND r.CRSYSTEM = 1
          AND r.CRSTATUS = 1
          AND r.CRDESC IS NOT NULL
          AND (r.RATINGEFFECTIVEENDDATE IS NULL OR r.RATINGEFFECTIVEENDDATE >= '2024-05-27')
    ) x
    WHERE rn = 1
),
scope AS (
    SELECT
        s.MAINCODE,
        s.bond_code,
        COALESCE(br.CRDESC, sr.CRDESC) AS rating,
        CASE WHEN br.CRDESC IS NOT NULL THEN br.INFOPUBLDATE ELSE sr.INFOPUBLDATE END AS rating_pubdate,
        s.yield_change_bp
    FROM val_summary s
    LEFT JOIN bond_rating br
      ON br.MAINCODE = s.MAINCODE
    LEFT JOIN subject_rating sr
      ON sr.COMPANYCODE = 74749
),
subject_events AS (
    SELECT
        s.MAINCODE,
        CASE
            WHEN COALESCE(r.CRCHANGE, '') REGEXP '调低|下调|降低|降级'
              OR (r.LASTCRCODE IS NOT NULL AND r.CRCODE IS NOT NULL AND r.CRCODE < r.LASTCRCODE)
            THEN '主体评级下调'
            WHEN r.CROUTLOOK = 1208 THEN '主体下调观察'
            WHEN r.CROUTLOOK = 1203 THEN '主体负面展望'
            WHEN COALESCE(r.CRANTICIPATE, '') REGEXP '下调观察|列入.*下调|下调.*观察|负面观察'
            THEN '主体下调观察'
            WHEN COALESCE(r.CRANTICIPATE, '') REGEXP '负面'
            THEN '主体负面展望'
            ELSE '主体评级负面'
        END AS event_type,
        DATE(r.INFOPUBLDATE) AS announcement_date,
        r.ID AS event_id,
        2 AS event_priority
    FROM scope s
    JOIN bond_comcreditgrading r
      ON r.COMPANYCODE = 74749
    WHERE DATE(r.INFOPUBLDATE) BETWEEN DATE_SUB('2024-05-27', INTERVAL 90 DAY) AND '2024-05-27'
      AND r.CRTYPE = 3
      AND r.CRSYSTEM = 1
      AND r.CRSTATUS = 1
      AND (
            COALESCE(r.CRCHANGE, '') REGEXP '调低|下调|降低|降级'
         OR (r.LASTCRCODE IS NOT NULL AND r.CRCODE IS NOT NULL AND r.CRCODE < r.LASTCRCODE)
         OR r.CROUTLOOK IN (1203,1208)
         OR COALESCE(r.CRANTICIPATE, '') REGEXP '负面|下调观察|列入.*下调|下调.*观察'
         OR COALESCE(r.CHANGERESON, '') REGEXP '负面|下调|违约|展期|兑付风险'
      )
),
bond_events AS (
    SELECT
        s.MAINCODE,
        CASE
            WHEN COALESCE(r.CRCHANGE, '') REGEXP '调低|下调|降低|降级'
              OR (r.LASTCRCODE IS NOT NULL AND r.CRCODE IS NOT NULL AND r.CRCODE < r.LASTCRCODE)
            THEN '债项评级下调'
            WHEN r.CROUTLOOK = 1208 THEN '债项下调观察'
            WHEN r.CROUTLOOK = 1203 THEN '债项负面展望'
            WHEN COALESCE(r.CRANTICIPATE, '') REGEXP '下调观察|列入.*下调|下调.*观察|负面观察'
            THEN '债项下调观察'
            WHEN COALESCE(r.CRANTICIPATE, '') REGEXP '负面'
            THEN '债项负面展望'
            ELSE '债项评级负面'
        END AS event_type,
        DATE(r.INFOPUBLDATE) AS announcement_date,
        r.ID AS event_id,
        2 AS event_priority
    FROM scope s
    JOIN bond_bdcreditgrading r
      ON r.MAINCODE = s.MAINCODE
    WHERE DATE(r.INFOPUBLDATE) BETWEEN DATE_SUB('2024-05-27', INTERVAL 90 DAY) AND '2024-05-27'
      AND r.CRTYPECODE = 2
      AND r.CRSYSTEM = 1
      AND r.CRSTATUS = 1
      AND (
            COALESCE(r.CRCHANGE, '') REGEXP '调低|下调|降低|降级'
         OR (r.LASTCRCODE IS NOT NULL AND r.CRCODE IS NOT NULL AND r.CRCODE < r.LASTCRCODE)
         OR r.CROUTLOOK IN (1203,1208)
         OR COALESCE(r.CRANTICIPATE, '') REGEXP '负面|下调观察|列入.*下调|下调.*观察'
         OR COALESCE(r.CHANGERESON, '') REGEXP '负面|下调|违约|展期|兑付风险'
      )
),
default_events AS (
    SELECT DISTINCT
        s.MAINCODE,
        CASE d.DEFAULTTYPE
            WHEN 1001 THEN '公告预计违约'
            WHEN 1002 THEN '信用评级违约'
            WHEN 1003 THEN '触发投资者保护条款'
            WHEN 2001 THEN '实质违约'
            WHEN 2002 THEN '技术性违约'
            WHEN 2003 THEN '本息展期'
            WHEN 100301 THEN '触发交叉违约条款'
            ELSE '违约相关公告'
        END AS event_type,
        DATE(d.INFOPUBLDATE) AS announcement_date,
        d.ID AS event_id,
        1 AS event_priority
    FROM scope s
    JOIN bond_code dc
      ON dc.COMPANYCODE = 74749
    JOIN bond_default d
      ON d.INNERCODE = dc.INNERCODE
    WHERE DATE(d.INFOPUBLDATE) BETWEEN DATE_SUB('2024-05-27', INTERVAL 90 DAY) AND '2024-05-27'
      AND (d.DEFAULTPROG IS NULL OR d.DEFAULTPROG <> 901)
),
event_candidates AS (
    SELECT * FROM subject_events
    UNION ALL
    SELECT * FROM bond_events
    UNION ALL
    SELECT * FROM default_events
),
latest_event AS (
    SELECT MAINCODE, event_type, announcement_date
    FROM (
        SELECT
            e.*,
            ROW_NUMBER() OVER (
                PARTITION BY e.MAINCODE
                ORDER BY e.announcement_date DESC, e.event_priority ASC, e.event_id DESC
            ) AS rn
        FROM event_candidates e
    ) x
    WHERE rn = 1
)
SELECT
    s.bond_code AS `债券代码`,
    s.rating AS `评级`,
    DATE_FORMAT(s.rating_pubdate, '%Y-%m-%d') AS `评级公告日期`,
    ROUND(s.yield_change_bp, 2) AS `最近5次估值收益率变化(bp)`,
    ROUND(td.avg_trade_deviation_bp, 2) AS `近20日平均成交偏离(bp)`,
    le.event_type AS `最近负面事件类型`,
    DATE_FORMAT(le.announcement_date, '%Y-%m-%d') AS `负面事件公告日期`
FROM scope s
LEFT JOIN trade_deviation td
  ON td.MAINCODE = s.MAINCODE
LEFT JOIN latest_event le
  ON le.MAINCODE = s.MAINCODE
ORDER BY s.yield_change_bp DESC, s.bond_code ASC;
