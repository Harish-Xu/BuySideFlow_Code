WITH params AS (
    SELECT DATE('2026-02-28') AS anchor_date
),
cashflow_index AS (
    -- 用指数基本信息表限定指数集合，再用证券主表名称筛“现金流”主题
    SELECT DISTINCT
        ibi.INDEXCODE AS index_innercode,
        sm.SECUCODE AS index_code,
        COALESCE(sm.SECUABBR, sm.CHINAMEABBR, sm.CHINAME) AS index_name
    FROM lc_indexbasicinfo ibi
    JOIN secumain sm
      ON sm.INNERCODE = ibi.INDEXCODE
    WHERE COALESCE(sm.CHINAME, '') LIKE '%现金流%'
       OR COALESCE(sm.CHINAMEABBR, '') LIKE '%现金流%'
       OR COALESCE(sm.SECUABBR, '') LIKE '%现金流%'
       OR COALESCE(sm.ENGNAME, '') LIKE '%Cash Flow%'
       OR COALESCE(sm.ENGNAMEABBR, '') LIKE '%Cash Flow%'
),
tracked_share_raw AS (
    -- 先取截至观察日有效，且能严格映射到主基金主代码的 ETF 跟踪关系
    SELECT
        mt.TRACEDINDEXCODE AS index_innercode,
        mt.INNERCODE AS share_innercode,
        LPAD(CAST(fa.MAINCODE AS CHAR), 6, '0') AS main_fund_code,
        ROW_NUMBER() OVER (
            PARTITION BY mt.INNERCODE, mt.TRACEDINDEXCODE
            ORDER BY mt.EXECUTEDATE DESC,
                     COALESCE(mt.INFOPUBLDATE, mt.INSERTTIME) DESC,
                     mt.INSERTTIME DESC,
                     mt.ID DESC
        ) AS rn
    FROM mf_investtargetcriterion mt
    JOIN mf_fundarchives fa
      ON fa.INNERCODE = mt.INNERCODE
    JOIN cashflow_index ci
      ON ci.index_innercode = mt.TRACEDINDEXCODE
    JOIN params p
      ON 1 = 1
    WHERE mt.TRACEDINDEXCODE IS NOT NULL
      AND mt.INVESTTARGET = 7
      AND (mt.EXECUTEDATE IS NULL OR mt.EXECUTEDATE <= p.anchor_date)
      AND (mt.CANCELDATE IS NULL OR mt.CANCELDATE > p.anchor_date)
      AND (
            (mt.INFOPUBLDATE IS NOT NULL
             AND mt.INFOPUBLDATE < DATE_ADD(p.anchor_date, INTERVAL 1 DAY))
         OR (mt.INFOPUBLDATE IS NULL
             AND mt.INSERTTIME < DATE_ADD(p.anchor_date, INTERVAL 1 DAY))
      )
      AND fa.TYPE = 4
      AND COALESCE(fa.IFFOF, 2) <> 1
      AND fa.MAINCODE IS NOT NULL
      AND CAST(fa.MAINCODE AS CHAR) <> ''
      AND fa.ESTABLISHMENTDATE <= p.anchor_date
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE > p.anchor_date)
),
tracked_main_fund AS (
    -- 折算到主基金口径，仅保留 ETF 本体
    SELECT DISTINCT
        ts.index_innercode,
        mfa.INNERCODE AS main_fund_innercode,
        sm.SECUCODE AS fund_code,
        COALESCE(sm.SECUABBR, sm.CHINAMEABBR, sm.CHINAME) AS fund_name
    FROM tracked_share_raw ts
    JOIN mf_fundarchives mfa
      ON LPAD(CAST(mfa.SECURITYCODE AS CHAR), 6, '0') = ts.main_fund_code
    JOIN secumain sm
      ON sm.INNERCODE = mfa.INNERCODE
     AND sm.SECUCATEGORY = 8
    JOIN params p
      ON 1 = 1
    WHERE ts.rn = 1
      AND mfa.TYPE = 4
      AND COALESCE(mfa.IFFOF, 2) <> 1
      AND mfa.ESTABLISHMENTDATE <= p.anchor_date
      AND (mfa.EXPIREDATE IS NULL OR mfa.EXPIREDATE > p.anchor_date)
      AND mfa.INSERTTIME < DATE_ADD(p.anchor_date, INTERVAL 1 DAY)
      AND mfa.MAINCODE IS NOT NULL
      AND CAST(mfa.MAINCODE AS CHAR) <> ''
      AND LPAD(CAST(mfa.MAINCODE AS CHAR), 6, '0') = LPAD(CAST(mfa.SECURITYCODE AS CHAR), 6, '0')
      AND COALESCE(sm.SECUABBR, sm.CHINAMEABBR, sm.CHINAME, '') NOT LIKE '%联接%'
),
latest_scale AS (
    -- 取主基金截至观察日最新可得合并规模
    SELECT
        sa.INNERCODE AS main_fund_innercode,
        sa.ENDDATE AS scale_date,
        sa.COMBNVI AS fund_scale,
        ROW_NUMBER() OVER (
            PARTITION BY sa.INNERCODE
            ORDER BY sa.ENDDATE DESC, sa.INSERTTIME DESC, sa.ID DESC
        ) AS rn
    FROM mf_scaleanalysis sa
    JOIN params p
      ON 1 = 1
    WHERE sa.ENDDATE <= p.anchor_date
      AND sa.INSERTTIME < DATE_ADD(p.anchor_date, INTERVAL 1 DAY)
      AND sa.COMBNVI IS NOT NULL
),
index_agg AS (
    SELECT
        tmf.index_innercode,
        COUNT(DISTINCT tmf.main_fund_innercode) AS track_etf_count,
        SUM(COALESCE(ls.fund_scale, 0)) AS total_track_scale
    FROM tracked_main_fund tmf
    LEFT JOIN latest_scale ls
      ON ls.main_fund_innercode = tmf.main_fund_innercode
     AND ls.rn = 1
    GROUP BY tmf.index_innercode
),
largest_fund AS (
    SELECT
        tmf.index_innercode,
        tmf.fund_name,
        ls.fund_scale,
        ROW_NUMBER() OVER (
            PARTITION BY tmf.index_innercode
            ORDER BY CASE WHEN ls.fund_scale IS NULL THEN 1 ELSE 0 END,
                     ls.fund_scale DESC,
                     tmf.fund_code ASC
        ) AS rn
    FROM tracked_main_fund tmf
    LEFT JOIN latest_scale ls
      ON ls.main_fund_innercode = tmf.main_fund_innercode
     AND ls.rn = 1
)
SELECT
    ci.index_code AS `指数代码`,
    ci.index_name AS `指数简称`,
    ia.track_etf_count AS `跟踪 ETF 数量`,
    ROUND(ia.total_track_scale / 100000000, 2) AS `合计跟踪规模（亿元）`,
    lf.fund_name AS `最大 ETF 名称`,
    ROUND(lf.fund_scale / 100000000, 2) AS `最大 ETF 规模（亿元）`
FROM index_agg ia
JOIN cashflow_index ci
  ON ci.index_innercode = ia.index_innercode
LEFT JOIN largest_fund lf
  ON lf.index_innercode = ia.index_innercode
 AND lf.rn = 1
ORDER BY ia.total_track_scale DESC, ci.index_code ASC;
