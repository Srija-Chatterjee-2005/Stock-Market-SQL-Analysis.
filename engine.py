"""Reproducible SQL-first analytics and a bounded, read-only SQLite playground."""
from __future__ import annotations

import csv
import hashlib
import math
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import sqlparse

ROOT = Path(__file__).resolve().parent
STOCKS = {
    "Bajaj Auto": "bajaj_auto",
    "Eicher Motors": "eicher_motors",
    "Hero Motocorp": "hero_motocorp",
    "Infosys": "infosys",
    "TCS": "tcs",
    "TVS Motors": "tvs_motors",
}
FILES = {stock: f"{stock}.csv" for stock in STOCKS}
COLUMNS = ["date", "open_price", "high_price", "low_price", "close_price", "wap",
           "no_of_shares", "no_of_trades", "total_turnover", "deliverable_qty",
           "pct_deli_qty", "spread_high_low", "spread_close_open"]
EVENTS = [
    ("Infosys", "2015-06-15", 2.0, "1:1 bonus issue",
     "https://www.infosys.com/investors/reports-filings/quarterly-results/2015-2016/q1/documents/q1-2016.pdf"),
    ("TCS", "2018-05-31", 2.0, "1:1 bonus issue",
     "https://www.tcs.com/content/dam/tcs/pdf/discover-tcs/investor-relations/faq/annual-report-18-19-intimation-record-date-agm-24.pdf"),
]


def source_rows():
    """Parse the original CSVs strictly, without filling legitimate missing fields."""
    result = {}
    for stock, table in STOCKS.items():
        with (ROOT / "data" / FILES[stock]).open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.reader(handle)
            header = next(reader)
            if len(header) != len(COLUMNS):
                raise ValueError(f"Unexpected column count in {FILES[stock]}")
            rows = []
            for line, row in enumerate(reader, 2):
                if len(row) != 13:
                    raise ValueError(f"Invalid row {line} in {FILES[stock]}")
                iso = datetime.strptime(row[0].strip(), "%d-%B-%Y").date().isoformat()
                values = [iso]
                for i, value in enumerate(row[1:], 1):
                    value = value.strip()
                    number = None if value == "" else float(value)
                    if number is not None and not math.isfinite(number):
                        raise ValueError(f"Non-finite number in {FILES[stock]}, row {line}")
                    if i in (6, 7, 9) and number is not None:
                        if not number.is_integer():
                            raise ValueError(f"Non-integer share/trade count in {FILES[stock]}")
                        number = int(number)
                    values.append(number)
                if values[4] is None or values[4] <= 0:
                    raise ValueError(f"Invalid closing price in {FILES[stock]}, row {line}")
                rows.append(tuple(values))
        if len({r[0] for r in rows}) != len(rows):
            raise ValueError(f"Duplicate trading dates in {FILES[stock]}")
        result[table] = sorted(rows)
    return result


def price_union():
    return "\nUNION ALL\n".join(
        f"SELECT '{stock}' AS stock, {', '.join(COLUMNS)} FROM {table}"
        for stock, table in STOCKS.items())


def analytics_select():
    return """WITH windows AS (
    SELECT p.*,
        ROW_NUMBER() OVER (PARTITION BY basis, stock ORDER BY date) AS trading_day,
        COUNT(*) OVER (PARTITION BY basis, stock ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS n20,
        COUNT(*) OVER (PARTITION BY basis, stock ORDER BY date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS n50,
        AVG(close_price) OVER (PARTITION BY basis, stock ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS a20,
        AVG(close_price) OVER (PARTITION BY basis, stock ORDER BY date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS a50,
        LAG(close_price) OVER (PARTITION BY basis, stock ORDER BY date) AS prev_close,
        MAX(close_price) OVER (PARTITION BY basis, stock ORDER BY date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_peak
    FROM v_price_basis p
), ma AS (
    SELECT *, CASE WHEN n20 = 20 THEN a20 END AS ma20,
              CASE WHEN n50 = 50 THEN a50 END AS ma50 FROM windows
), lagged AS (
    SELECT *, LAG(ma20) OVER (PARTITION BY basis, stock ORDER BY date) AS prev_ma20,
              LAG(ma50) OVER (PARTITION BY basis, stock ORDER BY date) AS prev_ma50 FROM ma
)
SELECT basis, stock, date, open_price, high_price, low_price, close_price, raw_close,
       no_of_shares, no_of_trades, total_turnover, deliverable_qty, pct_deli_qty,
       trading_day, ma20, ma50,
       CASE WHEN ma20 IS NULL OR ma50 IS NULL OR prev_ma20 IS NULL OR prev_ma50 IS NULL THEN 'Hold'
            WHEN ma20 > ma50 AND prev_ma20 <= prev_ma50 THEN 'Buy'
            WHEN ma20 < ma50 AND prev_ma20 >= prev_ma50 THEN 'Sell'
            ELSE 'Hold' END AS `signal`,
       CASE WHEN ma50 IS NULL THEN 'Warming up'
            WHEN ma20 > ma50 THEN 'Above 50-day MA'
            WHEN ma20 < ma50 THEN 'Below 50-day MA' ELSE 'Equal averages' END AS regime,
       100.0 * (close_price / NULLIF(prev_close, 0) - 1.0) AS daily_return_pct,
       100.0 * (close_price / NULLIF(running_peak, 0) - 1.0) AS drawdown_pct
FROM lagged"""


def views_sql(dialect="sqlite"):
    diff = "DATEDIFF(date, previous_signal_date)" if dialect == "mysql" else "CAST(julianday(date) - julianday(previous_signal_date) AS INTEGER)"
    scalar_max = "GREATEST" if dialect == "mysql" else "MAX"
    return f"""
DROP VIEW IF EXISTS v_signal_changes;
DROP VIEW IF EXISTS v_crossovers;
DROP VIEW IF EXISTS v_stock_summary;
DROP VIEW IF EXISTS v_analytics;
DROP VIEW IF EXISTS v_price_basis;
DROP VIEW IF EXISTS v_prices;
CREATE VIEW v_prices AS {price_union()};
CREATE VIEW v_price_basis AS
SELECT 'Raw' AS basis, stock, date, open_price, high_price, low_price, close_price,
       close_price AS raw_close, no_of_shares, no_of_trades, total_turnover, deliverable_qty, pct_deli_qty
FROM v_prices
UNION ALL
SELECT 'Adjusted' AS basis, p.stock, p.date,
       p.open_price / CASE WHEN p.date < c.ex_date THEN c.factor ELSE 1.0 END,
       p.high_price / CASE WHEN p.date < c.ex_date THEN c.factor ELSE 1.0 END,
       p.low_price / CASE WHEN p.date < c.ex_date THEN c.factor ELSE 1.0 END,
       p.close_price / CASE WHEN p.date < c.ex_date THEN c.factor ELSE 1.0 END,
       p.close_price, p.no_of_shares, p.no_of_trades, p.total_turnover, p.deliverable_qty, p.pct_deli_qty
FROM v_prices p LEFT JOIN corporate_actions c ON c.stock = p.stock;
CREATE VIEW v_analytics AS {analytics_select()};
CREATE VIEW v_stock_summary AS
WITH ranked AS (
 SELECT *, ROW_NUMBER() OVER (PARTITION BY basis, stock ORDER BY date DESC) AS newest
 FROM v_analytics
), latest_cross AS (
 SELECT basis, stock, date, `signal`, ROW_NUMBER() OVER (PARTITION BY basis, stock ORDER BY date DESC) AS rn
 FROM v_analytics WHERE `signal` <> 'Hold'
), stats AS (
 SELECT basis, stock, COUNT(*) AS trading_days, MIN(date) AS first_date, MAX(date) AS last_date,
 MAX(CASE WHEN trading_day = 1 THEN close_price END) AS first_close,
 MAX(CASE WHEN newest = 1 THEN close_price END) AS last_close,
 SUM(CASE WHEN `signal` = 'Buy' THEN 1 ELSE 0 END) AS buys,
 SUM(CASE WHEN `signal` = 'Sell' THEN 1 ELSE 0 END) AS sells,
 SUM(CASE WHEN `signal` = 'Hold' THEN 1 ELSE 0 END) AS holds,
 MIN(drawdown_pct) AS max_drawdown_pct,
 SQRT({scalar_max}(0.0, (SUM(daily_return_pct * daily_return_pct) - SUM(daily_return_pct) * SUM(daily_return_pct) / COUNT(daily_return_pct)) / NULLIF(COUNT(daily_return_pct) - 1, 0))) * SQRT(252.0) AS annual_volatility_pct
 FROM ranked GROUP BY basis, stock
)
SELECT s.*, 100.0 * (last_close / first_close - 1.0) AS price_change_pct,
 l.date AS last_signal_date, l.`signal` AS last_signal,
 r.`signal` AS final_day_signal, r.regime AS final_regime
FROM stats s LEFT JOIN latest_cross l ON l.basis = s.basis AND l.stock = s.stock AND l.rn = 1
JOIN ranked r ON r.basis = s.basis AND r.stock = s.stock AND r.newest = 1;
CREATE VIEW v_crossovers AS
WITH crosses AS (
 SELECT basis, stock, date, close_price, `signal`, trading_day,
        LAG(date) OVER (PARTITION BY basis, stock ORDER BY date) AS previous_signal_date,
        LAG(`signal`) OVER (PARTITION BY basis, stock ORDER BY date) AS previous_signal,
        LAG(trading_day) OVER (PARTITION BY basis, stock ORDER BY date) AS previous_signal_day
 FROM v_analytics WHERE `signal` <> 'Hold'
)
SELECT *, {diff} AS gap_calendar_days,
       trading_day - previous_signal_day AS gap_trading_days FROM crosses;
CREATE VIEW v_signal_changes AS
SELECT r.stock, r.date, r.`signal` AS raw_signal, a.`signal` AS adjusted_signal
FROM v_analytics r JOIN v_analytics a ON a.stock = r.stock AND a.date = r.date AND a.basis = 'Adjusted'
WHERE r.basis = 'Raw' AND r.`signal` <> a.`signal`;
"""


def task_queries(dialect="sqlite"):
    year = "YEAR(date)" if dialect == "mysql" else "strftime('%Y', date)"
    all_prices = "\nUNION ALL\n".join(f"SELECT '{stock}' AS stock, date, close_price FROM {table}" for stock, table in STOCKS.items())
    return {
        "01 | History coverage": "SELECT COUNT(*) AS trading_days, MIN(date) AS first_day, MAX(date) AS last_day FROM bajaj_auto;",
        "02 | Eicher's five highest closes": "SELECT date, close_price FROM eicher_motors ORDER BY close_price DESC, date LIMIT 5;",
        "03 | TCS annual average": f"SELECT {year} AS year, ROUND(AVG(close_price), 2) AS avg_close FROM tcs GROUP BY {year} ORDER BY year;",
        "04 | Missing delivery quantities": "\nUNION ALL\n".join(f"SELECT '{table}' AS stock, date FROM {table} WHERE deliverable_qty IS NULL" for table in STOCKS.values()) + " ORDER BY stock, date;",
        "05 | Bajaj moving averages": "SELECT date, close_price, ma20, ma50 FROM bajaj1 ORDER BY date;",
        "06 | Master table": "SELECT date, bajaj, tcs, tvs, infosys, eicher, hero FROM master_table ORDER BY date;",
        "07 | Bajaj crossover signals": "SELECT date, close_price, `signal` FROM bajaj2 ORDER BY date;",
        "08 | Bajaj signal counts": "SELECT `signal`, COUNT(*) AS days FROM bajaj2 GROUP BY `signal` ORDER BY `signal`;",
        "09 | Signal on a given date": "SELECT `signal` FROM bajaj2 WHERE date = '2018-06-21';",
        "10 | All six signal summaries": f"""WITH prices AS (
{all_prices}
), ma AS (
 SELECT stock, date, close_price,
 CASE WHEN ROW_NUMBER() OVER (PARTITION BY stock ORDER BY date) >= 20
 THEN AVG(close_price) OVER (PARTITION BY stock ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) END AS ma20,
 CASE WHEN ROW_NUMBER() OVER (PARTITION BY stock ORDER BY date) >= 50
 THEN AVG(close_price) OVER (PARTITION BY stock ORDER BY date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) END AS ma50
 FROM prices
), lagged AS (
 SELECT *, LAG(ma20) OVER (PARTITION BY stock ORDER BY date) AS prev_ma20,
 LAG(ma50) OVER (PARTITION BY stock ORDER BY date) AS prev_ma50 FROM ma
), sig AS (
 SELECT stock, date, close_price,
 CASE WHEN ma20 IS NULL OR ma50 IS NULL OR prev_ma20 IS NULL OR prev_ma50 IS NULL THEN 'Hold'
 WHEN ma20 > ma50 AND prev_ma20 <= prev_ma50 THEN 'Buy'
 WHEN ma20 < ma50 AND prev_ma20 >= prev_ma50 THEN 'Sell' ELSE 'Hold' END AS `signal`
 FROM lagged
), latest AS (
 SELECT *, ROW_NUMBER() OVER (PARTITION BY stock ORDER BY date DESC) AS rn FROM sig WHERE `signal` <> 'Hold'
)
SELECT s.stock, SUM(CASE WHEN s.`signal` = 'Buy' THEN 1 ELSE 0 END) AS buys,
 SUM(CASE WHEN s.`signal` = 'Sell' THEN 1 ELSE 0 END) AS sells,
 l.date AS last_signal_date, l.`signal` AS last_signal
FROM sig s LEFT JOIN latest l ON l.stock = s.stock AND l.rn = 1
GROUP BY s.stock, l.date, l.`signal` ORDER BY s.stock;""",
        "11 | Raw full-period price change": f"""WITH prices AS ({all_prices}), ends AS (
 SELECT stock, MIN(date) AS first_date, MAX(date) AS last_date FROM prices GROUP BY stock
)
SELECT e.stock, f.close_price AS first_close, l.close_price AS last_close,
 ROUND(100.0 * (l.close_price / f.close_price - 1.0), 1) AS pct_change
FROM ends e JOIN prices f ON f.stock = e.stock AND f.date = e.first_date
JOIN prices l ON l.stock = e.stock AND l.date = e.last_date ORDER BY pct_change DESC, e.stock;""",
        "12 | Worst daily raw price move": f"""WITH prices AS ({all_prices}), moves AS (
 SELECT stock, date, close_price, 100.0 * (close_price / LAG(close_price) OVER (PARTITION BY stock ORDER BY date) - 1.0) AS pct_move FROM prices
), ranked AS (
 SELECT *, ROW_NUMBER() OVER (PARTITION BY stock ORDER BY pct_move, date) AS rn FROM moves WHERE pct_move IS NOT NULL
)
SELECT stock, date, close_price, ROUND(pct_move, 1) AS pct_move FROM ranked WHERE rn = 1 ORDER BY pct_move, stock;""",
        "13 | Bonus-adjusted IT returns": """WITH adjusted AS (
 SELECT 'TCS' AS stock, date, CASE WHEN date < '2018-05-31' THEN close_price / 2.0 ELSE close_price END AS adj_close FROM tcs
 UNION ALL
 SELECT 'Infosys', date, CASE WHEN date < '2015-06-15' THEN close_price / 2.0 ELSE close_price END FROM infosys
)
SELECT stock, ROUND(100.0 * (MAX(CASE WHEN date = '2018-07-31' THEN adj_close END) /
 MAX(CASE WHEN date = '2015-01-01' THEN adj_close END) - 1.0), 1) AS adjusted_pct_change
FROM adjusted GROUP BY stock ORDER BY stock;""",
        "14 | Adjusted crossover summary": "SELECT stock, buys, sells, last_signal_date, last_signal, ROUND(price_change_pct, 2) AS price_change_pct FROM v_stock_summary WHERE basis = 'Adjusted' ORDER BY stock;",
        "15 | Signals changed by adjustment": "SELECT * FROM v_signal_changes ORDER BY stock, date;",
        "16 | Fast reversals within 30 calendar days": "SELECT stock, previous_signal_date, previous_signal, date, `signal`, gap_calendar_days, gap_trading_days FROM v_crossovers WHERE basis = 'Adjusted' AND gap_calendar_days <= 30 ORDER BY stock, date;",
        "17 | Adjusted volatility and drawdown": "SELECT stock, ROUND(annual_volatility_pct, 2) AS annual_volatility_pct, ROUND(max_drawdown_pct, 2) AS max_drawdown_pct FROM v_stock_summary WHERE basis = 'Adjusted' ORDER BY stock;",
        "18 | Selected dashboard rows": "SELECT stock, date, close_price, ma20, ma50, `signal`, daily_return_pct FROM selected_prices ORDER BY stock, date;",
    }


def setup_analysis_sql(dialect="sqlite"):
    return """
DROP TABLE IF EXISTS bajaj1;
CREATE TABLE bajaj1 AS
SELECT date, close_price,
 CASE WHEN ROW_NUMBER() OVER (ORDER BY date) >= 20
 THEN ROUND(AVG(close_price) OVER (ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW), 2) END AS ma20,
 CASE WHEN ROW_NUMBER() OVER (ORDER BY date) >= 50
 THEN ROUND(AVG(close_price) OVER (ORDER BY date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW), 2) END AS ma50
FROM bajaj_auto;
DROP TABLE IF EXISTS master_table;
CREATE TABLE master_table AS
SELECT b.date, b.close_price AS bajaj, t.close_price AS tcs, v.close_price AS tvs,
 i.close_price AS infosys, e.close_price AS eicher, h.close_price AS hero
FROM bajaj_auto b JOIN tcs t ON t.date = b.date JOIN tvs_motors v ON v.date = b.date
JOIN infosys i ON i.date = b.date JOIN eicher_motors e ON e.date = b.date
JOIN hero_motocorp h ON h.date = b.date;
DROP TABLE IF EXISTS bajaj2;
CREATE TABLE bajaj2 AS
WITH lagged AS (
 SELECT *, LAG(ma20) OVER (ORDER BY date) AS prev_ma20,
 LAG(ma50) OVER (ORDER BY date) AS prev_ma50 FROM bajaj1
)
SELECT date, close_price,
 CASE WHEN ma20 IS NULL OR ma50 IS NULL OR prev_ma20 IS NULL OR prev_ma50 IS NULL THEN 'Hold'
 WHEN ma20 > ma50 AND prev_ma20 <= prev_ma50 THEN 'Buy'
 WHEN ma20 < ma50 AND prev_ma20 >= prev_ma50 THEN 'Sell' ELSE 'Hold' END AS `signal`
FROM lagged;
"""


def build_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    for table, rows in source_rows().items():
        types = ["TEXT PRIMARY KEY"] + ["INTEGER" if i in (6, 7, 9) else "REAL" for i in range(1, 13)]
        definitions = ", ".join(f"`{col}` {kind}" for col, kind in zip(COLUMNS, types))
        conn.execute(f"CREATE TABLE {table} ({definitions})")
        conn.executemany(f"INSERT INTO {table} VALUES ({','.join('?' for _ in COLUMNS)})", rows)
    conn.execute("CREATE TABLE corporate_actions(stock TEXT PRIMARY KEY, ex_date TEXT, factor REAL, event TEXT, source_url TEXT)")
    conn.executemany("INSERT INTO corporate_actions VALUES (?,?,?,?,?)", EVENTS)
    conn.executescript(setup_analysis_sql() + views_sql())
    conn.commit()
    return conn


def dataframe(conn, sql, params=()):
    return pd.read_sql_query(sql, conn, params=params)


def filtered_rows(conn, basis, stocks, start, end):
    if not stocks:
        return dataframe(conn, "SELECT * FROM v_analytics WHERE 0")
    placeholders = ",".join("?" for _ in stocks)
    return dataframe(conn, f"SELECT * FROM v_analytics WHERE basis = ? AND stock IN ({placeholders}) AND date BETWEEN ? AND ? ORDER BY stock, date", (basis, *stocks, str(start), str(end)))


def filtered_summary(conn, basis, stocks, start, end):
    if not stocks:
        return pd.DataFrame()
    marks = ",".join("?" for _ in stocks)
    sql = f"""WITH scope AS (
 SELECT * FROM v_analytics WHERE basis = ? AND stock IN ({marks}) AND date BETWEEN ? AND ?
), ranked AS (
 SELECT *, ROW_NUMBER() OVER (PARTITION BY stock ORDER BY date) AS first_rn,
 ROW_NUMBER() OVER (PARTITION BY stock ORDER BY date DESC) AS last_rn FROM scope
), crosses AS (
 SELECT *, ROW_NUMBER() OVER (PARTITION BY stock ORDER BY date DESC) AS rn
 FROM scope WHERE `signal` <> 'Hold'
)
SELECT r.stock, COUNT(*) AS observations, MIN(r.date) AS first_date, MAX(r.date) AS last_date,
 MAX(CASE WHEN first_rn = 1 THEN r.close_price END) AS first_close,
 MAX(CASE WHEN last_rn = 1 THEN r.close_price END) AS last_close,
 100.0 * (MAX(CASE WHEN last_rn = 1 THEN r.close_price END) / MAX(CASE WHEN first_rn = 1 THEN r.close_price END) - 1.0) AS price_change_pct,
 SUM(CASE WHEN r.`signal` = 'Buy' THEN 1 ELSE 0 END) AS buys,
 SUM(CASE WHEN r.`signal` = 'Sell' THEN 1 ELSE 0 END) AS sells,
 MAX(CASE WHEN last_rn = 1 THEN r.regime END) AS final_regime,
 MAX(CASE WHEN last_rn = 1 THEN r.`signal` END) AS final_day_signal,
 c.date AS last_signal_date, c.`signal` AS last_signal
FROM ranked r LEFT JOIN crosses c ON c.stock = r.stock AND c.rn = 1
GROUP BY r.stock, c.date, c.`signal` ORDER BY price_change_pct DESC, r.stock"""
    return dataframe(conn, sql, (basis, *stocks, str(start), str(end)))


def rebased_rows(conn, basis, stocks, start, end):
    if not stocks:
        return pd.DataFrame()
    marks = ",".join("?" for _ in stocks)
    return dataframe(conn, f"""WITH selected AS (
 SELECT * FROM v_analytics WHERE basis = ? AND stock IN ({marks}) AND date BETWEEN ? AND ?
)
SELECT *, 100.0 * close_price / FIRST_VALUE(close_price) OVER (PARTITION BY stock ORDER BY date) AS indexed_price
FROM selected ORDER BY stock, date""", (basis, *stocks, str(start), str(end)))


def run_readonly_query(conn, sql, row_limit=1000, timeout_seconds=5.0):
    """Allow one SELECT/CTE. Engine authorization, deadline and fetch cap enforce limits."""
    if not sql.strip():
        raise ValueError("Write a SELECT query first.")
    statements = [s for s in sqlparse.parse(sql) if str(s).strip()]
    if len(statements) != 1:
        raise ValueError("Run one SELECT statement at a time.")
    if statements[0].get_type() != "SELECT":
        raise ValueError("The playground accepts SELECT and WITH ... SELECT queries.")
    if not 1 <= int(row_limit) <= 20000:
        raise ValueError("Choose a row limit between 1 and 20,000.")
    started = time.perf_counter()
    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}

    def authorize(action, arg1, arg2, db, trigger):
        if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in {"load_extension", "readfile", "writefile"}:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY

    conn.set_authorizer(authorize)
    conn.set_progress_handler(lambda: int(time.perf_counter() - started > timeout_seconds), 1000)
    try:
        cursor = conn.execute(sql)
        columns = [item[0] for item in cursor.description]
        # Name duplicate output columns deterministically, so exports and charts remain usable.
        names, counts = [], {}
        for col in columns:
            counts[col] = counts.get(col, 0) + 1
            names.append(col if counts[col] == 1 else f"{col}_{counts[col]}")
        rows = cursor.fetchmany(int(row_limit) + 1)
        truncated = len(rows) > row_limit
        result = pd.DataFrame.from_records(rows[:row_limit], columns=names)
    except sqlite3.Error as exc:
        if "interrupted" in str(exc).lower():
            raise ValueError("Query exceeded the time limit. Reduce the rows or simplify the joins.") from exc
        raise ValueError(f"SQL error: {exc}") from exc
    finally:
        conn.set_authorizer(None)
        conn.set_progress_handler(None, 0)
    return result, (time.perf_counter() - started) * 1000.0, truncated


def set_selected_view(conn, basis, stocks, start, end):
    # Inputs come from controls and are quoted here; arbitrary SQL is handled separately.
    quote = lambda x: "'" + str(x).replace("'", "''") + "'"
    names = ",".join(quote(x) for x in stocks) or "NULL"
    conn.execute("DROP VIEW IF EXISTS selected_prices")
    conn.execute(f"CREATE TEMP VIEW selected_prices AS SELECT * FROM v_analytics WHERE basis={quote(basis)} AND stock IN ({names}) AND date BETWEEN {quote(start)} AND {quote(end)}")


def dataset_fingerprint():
    h = hashlib.sha256()
    for filename in sorted(FILES.values()):
        h.update((ROOT / "data" / filename).read_bytes())
    return h.hexdigest()
