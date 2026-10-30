-- Shanghai and Shenzhen have the same session dates in this backtest.
SELECT DISTINCT DATE(TRADINGDATE) AS trade_date
FROM qt_tradingdaynew
WHERE TRADINGDATE >= '{{ HISTORY_START_DATE }}'
  AND TRADINGDATE < '{{ END_DATE }}'
  AND SECUMARKET IN (83,90) AND IFTRADINGDAY = 1
ORDER BY trade_date;
