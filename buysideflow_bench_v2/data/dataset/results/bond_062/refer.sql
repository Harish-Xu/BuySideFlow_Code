-- bond_062: holding-period full-price return across the 2025-06-20 coupon date.
--
-- Schema evidence:
--   bond_code.SECUCODE/SECUABBR identify the bond.
--   bond_basicinfo.COUPONRATE is coupon rate and PAYINTERESTEFFENCY is payment
--   frequency per year.
--   bond_basicquote.CLOSEDIRTYPRICE is full close price; WEIGHTEDDIRTYPRICE is
--   full weighted price and is used only when the close full price is missing.

SELECT
    bc.MAINCODE,
    bc.INNERCODE,
    bc.SECUCODE AS bond_code,
    bc.SECUABBR AS bond_name,
    bi.COUPONRATE AS coupon_rate_pct,
    bi.PAYINTERESTEFFENCY AS pay_frequency,
    bi.PARVALUE AS par_value,
    MAX(CASE WHEN q.TRADINGDAY = DATE('2025-06-19')
             THEN COALESCE(q.CLOSEDIRTYPRICE, q.WEIGHTEDDIRTYPRICE)
        END) AS buy_full_price,
    MAX(CASE WHEN q.TRADINGDAY = DATE('2025-06-27')
             THEN COALESCE(q.CLOSEDIRTYPRICE, q.WEIGHTEDDIRTYPRICE)
        END) AS sell_full_price
FROM bond_code bc
JOIN bond_basicinfo bi
  ON bc.MAINCODE = bi.MAINCODE
LEFT JOIN bond_basicquote q FORCE INDEX (IX_BOND_BASICQUOTE)
  ON q.INNERCODE = bc.INNERCODE
 AND q.TRADINGDAY IN (DATE('2025-06-19'), DATE('2025-06-27'))
WHERE bc.SECUCODE = '102482392'
GROUP BY
    bc.MAINCODE,
    bc.INNERCODE,
    bc.SECUCODE,
    bc.SECUABBR,
    bi.COUPONRATE,
    bi.PAYINTERESTEFFENCY,
    bi.PARVALUE;
