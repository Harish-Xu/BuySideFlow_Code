WITH selected AS (
    SELECT
        d.bond_code,
        d.bond_name,
        d.issuer,
        v.remaining_years,
        v.valuation_yield_pct,
        (v.valuation_yield_pct - f.fdr007_pct) * 100 AS current_carry_bp,
        (v.valuation_yield_pct - f.fdr007_pct) * 100 - 50 AS scenario_carry_bp
    FROM tmp_b076_valuation v
    JOIN tmp_b076_rating r
      ON r.main_code = v.main_code
    JOIN tmp_b076_display d
      ON d.main_code = v.main_code
    CROSS JOIN tmp_b076_fdr f
    WHERE v.valuation_yield_pct > f.fdr007_pct
    ORDER BY
        (v.valuation_yield_pct - f.fdr007_pct) ASC,
        d.bond_code ASC
    LIMIT 10
)
SELECT
    bond_code AS `债券代码`,
    bond_name AS `债券简称`,
    issuer AS `发行人`,
    ROUND(remaining_years, 4) AS `剩余期限`,
    ROUND(valuation_yield_pct, 4) AS `估值收益率`,
    current_carry_bp AS `当前套息空间`,
    scenario_carry_bp AS `情景套息空间`
FROM selected
ORDER BY
    current_carry_bp ASC,
    `债券代码` ASC;
