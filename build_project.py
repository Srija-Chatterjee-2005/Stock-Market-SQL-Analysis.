"""Create self-contained SQL scripts, the playground database and reviewed query exports."""
from pathlib import Path
import json
import sqlite3
from engine import (ROOT, STOCKS, COLUMNS, EVENTS, source_rows, task_queries,
                    setup_analysis_sql, views_sql, build_connection, dataframe, dataset_fingerprint)


def literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return repr(value)


def make_script(dialect):
    mysql = dialect == "mysql"
    intro = """-- STOCK MARKET ANALYSIS | Srija Chatterjee | 10 October 2026
-- Original supplied data: six stocks, 889 trading days each, 2015-01-01 to 2018-07-31.
-- All 13 student-guide tasks plus adjusted signals, quick reversals and risk diagnostics.
-- Embedded INSERTs make this file reproducible: no CSV path or LOCAL INFILE setting is needed.
-- Raw tables preserve the original data. Adjusted prices divide pre-event OHLC by 2.
-- Prices exclude dividends, costs and taxes. Historical signals are not current advice.
-- Tasks 5/7 round Bajaj averages to 2 decimals as requested. Task 10 uses full precision.
"""
    if mysql:
        intro += "-- Requires MySQL 8.0+. Open in Workbench and execute the complete script.\nCREATE DATABASE IF NOT EXISTS stock_market_analysis;\nUSE stock_market_analysis;\n"
    else:
        intro += "-- SQLite 3.25+ with math functions. python build_project.py uses Python's bundled SQLite.\n"
    # Remove dependent views before rebuilding raw tables, making the script safely repeatable.
    intro += "\n".join(f"DROP VIEW IF EXISTS {v};" for v in ["v_signal_changes","v_crossovers","v_stock_summary","v_analytics","v_price_basis","v_prices"]) + "\n"
    for table, rows in source_rows().items():
        types = (["DATE PRIMARY KEY"] + ["BIGINT" if i in (6,7,9) else ("DECIMAL(24,8)" if i == 5 else "DECIMAL(24,2)") for i in range(1,13)]) if mysql else ["TEXT PRIMARY KEY"] + ["INTEGER" if i in (6,7,9) else "REAL" for i in range(1,13)]
        intro += f"\nDROP TABLE IF EXISTS {table};\nCREATE TABLE {table} (\n" + ",\n".join(f"    `{col}` {kind}" for col,kind in zip(COLUMNS,types)) + "\n);\n"
        for offset in range(0,len(rows),200):
            intro += f"INSERT INTO {table} VALUES\n" + ",\n".join("(" + ",".join(map(literal,row)) + ")" for row in rows[offset:offset+200]) + ";\n"
    intro += "\nDROP TABLE IF EXISTS corporate_actions;\nCREATE TABLE corporate_actions (stock VARCHAR(40) PRIMARY KEY, ex_date DATE, factor DECIMAL(6,2), event VARCHAR(50), source_url TEXT);\n" if mysql else "\nDROP TABLE IF EXISTS corporate_actions;\nCREATE TABLE corporate_actions (stock TEXT PRIMARY KEY, ex_date TEXT, factor REAL, event TEXT, source_url TEXT);\n"
    intro += "INSERT INTO corporate_actions VALUES\n" + ",\n".join("(" + ",".join(map(literal,event)) + ")" for event in EVENTS) + ";\n"
    intro += "\n-- Tasks 5, 6 and 7: create the required assignment tables.\n" + setup_analysis_sql(dialect)
    intro += "\n-- SQL views for reproducible raw/adjusted analytics.\n" + views_sql(dialect)
    if mysql:
        intro += """\n-- Task 9: reusable function. A missing trading date returns NULL.
DROP FUNCTION IF EXISTS bajaj_signal;
DELIMITER $$
CREATE FUNCTION bajaj_signal(d DATE)
RETURNS VARCHAR(4) DETERMINISTIC READS SQL DATA
BEGIN
    DECLARE s VARCHAR(4) DEFAULT NULL;
    SELECT MAX(`signal`) INTO s FROM bajaj2 WHERE `date` = d;
    RETURN s;
END $$
DELIMITER ;
SELECT bajaj_signal('2015-05-18') AS first_buy_check;
SELECT bajaj_signal('2016-01-04') AS hold_check;
SELECT bajaj_signal('2018-06-21') AS requested_date_signal;
"""
    for title,sql in task_queries(dialect).items():
        if title.startswith("18"):
            continue  # selected_prices is deliberately session-local to the dashboard.
        intro += "\n-- TASK " + title.replace(" | ",": ") + "\n" + sql + "\n"
    return intro


def main():
    (ROOT / "sql").mkdir(exist_ok=True)
    for dialect, name in [("mysql","Stock_Market_Analysis_MySQL.sql"),("sqlite","Stock_Market_Analysis_SQLite.sql")]:
        (ROOT / "sql" / name).write_text(make_script(dialect),encoding="utf-8")
    conn = build_connection()
    db_path = ROOT / "data" / "stock_market.db"
    with sqlite3.connect(db_path) as destination:
        conn.backup(destination)
    out = ROOT / "reports" / "query_results"
    out.mkdir(parents=True,exist_ok=True)
    for name, query in task_queries().items():
        if not name.startswith("18"):
            dataframe(conn,query).to_csv(out/f"task_{name[:2]}.csv",index=False)
    dataframe(conn,"SELECT * FROM v_stock_summary ORDER BY basis, stock").to_csv(out/"full_summary.csv",index=False)
    profile = []
    for stock, table in STOCKS.items():
        quality = dataframe(conn,f"SELECT COUNT(*) AS rows, COUNT(DISTINCT date) AS dates, MIN(date) AS first_date, MAX(date) AS last_date, SUM(deliverable_qty IS NULL) AS missing_delivery, SUM(pct_deli_qty IS NULL) AS missing_delivery_pct, SUM(close_price IS NULL OR close_price <= 0) AS invalid_close FROM {table}").iloc[0].to_dict()
        quality["stock"] = stock
        quality["original_file"] = f"{stock}.csv"
        profile.append(quality)
    (ROOT / "reports" / "data_profile.json").write_text(json.dumps({"dataset_sha256":dataset_fingerprint(),"stocks":profile,"corporate_actions":[{"stock":e[0],"ex_date":e[1],"factor":e[2],"source_url":e[4]} for e in EVENTS]},indent=2,default=str))
    print(dataframe(conn,"SELECT stock, basis, buys, sells, last_signal_date, last_signal, ROUND(price_change_pct,4) AS pct_change FROM v_stock_summary ORDER BY basis,stock").to_string(index=False))
    conn.close()


if __name__ == "__main__":
    main()
