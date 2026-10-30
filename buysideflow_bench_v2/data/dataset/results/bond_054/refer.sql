-- Extracted from bond_054/refer.py (main SQL: sql)
-- Template: {{ name }} will be substituted from refer.py before execution.
-- DO NOT change the variable names here without updating refer.py.

SELECT DATE(TRADINGDATE) AS trade_date
    FROM qt_tradingdaynew FORCE INDEX (IX_QT_TRADINGDAYNEW)
    WHERE TRADINGDATE >= '{{ CALENDAR_START }}'
      AND TRADINGDATE < '{{ BACKTEST_END_EXCLUSIVE }}'
      AND SECUMARKET IN (83, 90)
      AND IFTRADINGDAY = 1
    GROUP BY DATE(TRADINGDATE)
    ORDER BY trade_date;
