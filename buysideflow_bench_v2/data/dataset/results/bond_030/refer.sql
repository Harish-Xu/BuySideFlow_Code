WITH
-- 信用债口径：按项目业务规则使用 bond_code 债券分类，排除可转债、可交换债、ABS、金融债等。
credit_bond_universe AS (
    SELECT DISTINCT
        MAINCODE
    FROM bond_code
    WHERE MAINCODE IS NOT NULL
      AND (
          BONDTYPELEVEL1 IN (1600, 1700, 1800, 2500, 2600, 2800, 2900, 3100, 3200, 3300, 3700)
          OR BONDTYPELEVEL2 IN (1901, 1903)
      )
),
-- 可见性由评级公告日决定，入库/更新时间不作为额外截止条件。
rating_records AS (
    SELECT
        r.MAINCODE,
        r.CRASNAME,
        r.CRCHANGE
    FROM bond_bdcreditgrading r
    JOIN credit_bond_universe u
      ON u.MAINCODE = r.MAINCODE
    WHERE r.INFOPUBLDATE >= '2025-01-01'
      AND r.INFOPUBLDATE < '2026-01-01'
      AND r.MAINCODE IS NOT NULL
      AND r.CRASNAME IS NOT NULL
      AND r.CRTYPECODE = 2
      AND r.CRSYSTEM = 1
      AND r.CRSTATUS = 1
),
-- 覆盖只数：机构在 2025 年内出具过长期债项评级的债券总数。
coverage AS (
    SELECT
        CRASNAME,
        COUNT(DISTINCT MAINCODE) AS covered_bond_cnt
    FROM rating_records
    GROUP BY CRASNAME
),
-- 下调只数：同一债券被同一机构年内多次调低只记一次。
downgraded_bond_agency AS (
    SELECT DISTINCT
        MAINCODE,
        CRASNAME
    FROM rating_records
    WHERE CRCHANGE = '调低'
),
downgrade_cnt AS (
    SELECT
        CRASNAME,
        COUNT(DISTINCT MAINCODE) AS down_bond_cnt
    FROM downgraded_bond_agency
    GROUP BY CRASNAME
),
down_bonds AS (
    SELECT DISTINCT MAINCODE
    FROM downgraded_bond_agency
),
bond_issuer AS (
    SELECT
        bc.MAINCODE,
        COALESCE(CAST(MAX(bc.COMPANYCODE) AS CHAR), MAX(bc.ISSUER), MAX(bi.ISSUER)) AS issuer_key
    FROM bond_code bc
    JOIN down_bonds db ON db.MAINCODE = bc.MAINCODE
    LEFT JOIN bond_basicinfo bi
      ON bi.MAINCODE = bc.MAINCODE
    GROUP BY bc.MAINCODE
),
downgrade_issuer_cnt AS (
    SELECT
        d.CRASNAME,
        COUNT(DISTINCT bi.issuer_key) AS down_issuer_cnt
    FROM downgraded_bond_agency d
    LEFT JOIN bond_issuer bi ON bi.MAINCODE = d.MAINCODE
    GROUP BY d.CRASNAME
),
-- 按发行事件去重，不能按主代码只保留最近一次发行。
-- 起始日缺失时，回退到发行类别+公告日（发行表业务唯一索引的组成字段）。
-- UPDATETIME 仅用于重复记录的确定性排序，不代表公开可见时间。
issue_event_ranked AS (
    SELECT
        i.MAINCODE,
        i.ACTUALISSUESIZE,
        ROW_NUMBER() OVER (
            PARTITION BY
                i.MAINCODE,
                DATE(i.ISSUEDATESTART),
                CASE WHEN i.ISSUEDATESTART IS NULL THEN i.ISSUETYPE END,
                CASE WHEN i.ISSUEDATESTART IS NULL THEN DATE(i.PUBLICANNOUNCEMENTDATE) END
            ORDER BY i.PUBLICANNOUNCEMENTDATE DESC, i.UPDATETIME DESC, i.ID DESC
        ) AS rn
    FROM bond_issuenew i
    JOIN down_bonds db ON db.MAINCODE = i.MAINCODE
    WHERE i.PUBLICANNOUNCEMENTDATE < '2026-01-01'
      AND COALESCE(i.ISSUEDATEEND, i.ISSUEDATESTART, i.PUBLICANNOUNCEMENTDATE) < '2026-01-01'
),
-- ACTUALISSUESIZE 为每笔实际发行总额（百万元），已含该笔的追加发行部分。
-- 不再加 ACTUALADDISSUESIZE，也不以只含首发规模的 bond_basicinfo 兜底。
-- 先保留最新事件记录，再检查金额；缺失时不回捞旧值、不报部分合计。
cumulative_issue_size AS (
    SELECT
        MAINCODE,
        CASE
            WHEN COUNT(*) = COUNT(ACTUALISSUESIZE) AND MIN(ACTUALISSUESIZE) >= 0
            THEN SUM(ACTUALISSUESIZE) * 0.01
        END AS issue_size_yi
    FROM issue_event_ranked
    WHERE rn = 1
    GROUP BY MAINCODE
),
downgrade_issue_size AS (
    SELECT
        d.CRASNAME,
        CASE WHEN COUNT(*) = COUNT(cis.issue_size_yi)
             THEN SUM(cis.issue_size_yi)
        END AS total_issue_size_yi
    FROM downgraded_bond_agency d
    LEFT JOIN cumulative_issue_size cis ON cis.MAINCODE = d.MAINCODE
    GROUP BY d.CRASNAME
)
SELECT
    cov.CRASNAME AS `评级机构`,
    cov.covered_bond_cnt AS `覆盖只数`,
    COALESCE(dc.down_bond_cnt, 0) AS `下调只数`,
    ROUND(COALESCE(dc.down_bond_cnt, 0) * 100.0 / cov.covered_bond_cnt, 2) AS `下调率`,
    COALESCE(di.down_issuer_cnt, 0) AS `涉及发行人家数`,
    ROUND(CASE WHEN COALESCE(dc.down_bond_cnt, 0) = 0 THEN 0
               ELSE ds.total_issue_size_yi END, 2) AS `下调债券合计发行规模`
FROM coverage cov
LEFT JOIN downgrade_cnt dc ON dc.CRASNAME = cov.CRASNAME
LEFT JOIN downgrade_issuer_cnt di ON di.CRASNAME = cov.CRASNAME
LEFT JOIN downgrade_issue_size ds ON ds.CRASNAME = cov.CRASNAME
WHERE cov.covered_bond_cnt >= 50
ORDER BY
    COALESCE(dc.down_bond_cnt, 0) * 1.0 / cov.covered_bond_cnt DESC,
    COALESCE(dc.down_bond_cnt, 0) DESC,
    cov.CRASNAME ASC;
