# Stock Market Analysis

Prepared for **Srija Chatterjee** · submission deadline **10 October 2026**.

A complete analysis of the six supplied Indian stock datasets, covering **1 January 2015–31 July 2018**. All 13 student-guide tasks are completed in SQL, with four additional SQL diagnostics and an interactive Streamlit playground.

## Files to submit

| Deliverable | File |
|---|---|
| MySQL 8 analysis, including embedded data and the Bajaj signal function | `sql/Stock_Market_Analysis_MySQL.sql` |
| Ten-page insights report with methods, numerical evidence, charts, caveats, and issuer sources | `reports/Stock_Market_Insights.pdf` |
| Narrated walkthrough video | `reports/Stock_Market_Walkthrough.mp4` |
| Walkthrough transcript for a personal presentation | `reports/Video_Walkthrough_Script.txt` |
| Streamlit dashboard and live SQL playground | `app.py`, supported by `engine.py` and `simulator.py` |

The MP4 uses a generic synthetic narrator and verified captures of the working app. If your course requires your own voice or camera, use the included transcript to make your personal recording. These files are ready to upload to your course portal; no portal submission or public deployment has been performed.

## Run the dashboard

Use **Python 3.12** and extract the ZIP before running it. Keep the supplied directory structure intact.

Windows: double-click `run_app.bat`, or open a terminal in this folder and run:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m streamlit run app.py
```

macOS/Linux:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m streamlit run app.py
```

The browser normally opens at `http://localhost:8501`. The dashboard builds its database automatically from the six included CSVs. No database login, external API key, or uploaded file is required. Installation needs internet access to download Python packages; the dashboard's analysis runs locally. Issuer links and the optional Google font use the internet, with a local font fallback.

## Dashboard functionality

- **Overview:** adjustable stock selection, raw/adjusted price basis, date range, indexed-price comparison, price-change ranking, latest crossovers, and CSV export.
- **Stock explorer:** line/candlestick switch, 20/50-day moving-average toggle, Buy/Sell markers, volume, daily price moves, drawdown, and row export.
- **Signals & comparisons:** event-type filter, complete signal CSV, quick-reversal table, date lookup, and a cost/slippage scenario with equity curve and trade log.
- **Corporate actions:** original-versus-adjusted price comparison, event charts, changed signal dates, and links to issuer evidence. This page deliberately uses the full source period.
- **SQL playground:** editable queries for every assignment task, table/view schema inspector, real read-only execution, configurable row cap, timeout, CSV/JSON/query downloads, result chart, and session query history.
- **Data & downloads:** submission files, quality checks, missing delivery records, searchable rows, full filtered CSV, and SQLite database download.

Date filters preserve the moving-average history and original drawdown peaks. The default view uses adjusted prices. Original assignment checks use raw data.

## Run the MySQL submission

1. Use MySQL **8.0 or later** and an account permitted to create a database, tables, views, and a stored function.
2. Open `sql/Stock_Market_Analysis_MySQL.sql` in MySQL Workbench and execute the **entire script**.
3. Review the result grids. All 5,334 source rows are embedded; no CSV paths or `LOCAL INFILE` setup are needed.
4. For the requested lookup, run `SELECT bajaj_signal('2018-06-21');` — the answer is `Buy`. A non-trading date returns `NULL`.

The script creates and rebuilds objects in the dedicated `stock_market_analysis` database, including the six stock tables, `bajaj1`, `master_table`, `bajaj2`, the adjustment table, analytical views, and `bajaj_signal`. Rerunning it resets this project's generated database objects. If binary logging is enabled on a restricted hosted MySQL server, stored-function creation may need database-administrator configuration; use a local MySQL instance with the required permissions.

The standalone SQLite companion is `sql/Stock_Market_Analysis_SQLite.sql`. The Streamlit playground uses SQLite, so use `strftime('%Y', date)` there; the MySQL file uses `YEAR(date)`. Window functions need SQLite 3.25+; risk diagnostics also use `SQRT`, available in the tested Python 3.12 SQLite build.

## Core definitions and checked results

- A 20-day average is the current close plus the previous 19 **trading observations**; a 50-day average uses 49 previous observations. The first 19/49 values remain `NULL`.
- Buy: current MA20 > MA50 and previous MA20 <= MA50. Sell: current MA20 < MA50 and previous MA20 >= MA50. Initial or unchanged comparisons are Hold.
- Hold is a daily event label, not a portfolio position. The latest crossover and the final-day event are different fields.
- Assignment Bajaj averages are rounded to two decimals. Cross-stock/adjusted averages retain full precision.
- Infosys's 1:1 bonus ex-date is **15 June 2015**, and TCS's is **31 May 2018**. Open/high/low/close prices strictly before the event are divided by 2. Event-day prices are unchanged; reported trading volume is preserved.
- Price change = `100 × (last close / first close − 1)`, excluding dividends.
- Volatility is sample standard deviation of daily close-to-close changes, annualized with `√252`. Maximum drawdown uses the full-history running close peak.
- Quick reversal: opposite crossover within **30 calendar days**; the table also reports elapsed trading observations.

| Stock | Adjusted price change | Adjusted Buy / Sell | Latest adjusted crossover |
|---|---:|---:|---|
| Bajaj Auto | +10.05% | 12 / 11 | Buy, 21 June 2018 |
| Eicher Motors | +82.56% | 6 / 7 | Sell, 6 June 2018 |
| Hero Motocorp | +6.00% | 9 / 9 | Sell, 22 May 2018 |
| Infosys | +38.17% | 10 / 10 | Buy, 7 May 2018 |
| TCS | +52.36% | 12 / 12 | Buy, 20 April 2018 |
| TVS Motors | +86.91% | 8 / 8 | Sell, 17 May 2018 |

Raw assignment counts sum to **56 Buy and 57 Sell**; adjusted counts sum to **57 Buy and 57 Sell**. All final-day event labels are Hold. Raw TCS price change is −23.82%, and raw Infosys is −30.91%; those figures are distorted by bonus-share price changes. The PDF reconciles errors in the supplied course slides against query results.

The optional scenario is an explicitly modeled Python calculation using SQL-produced prices and events. It starts with INR 100,000 cash, fills a close signal at the next observed open, is long-only with fractional units, and liquidates at the final close. Fee and slippage sliders are assumptions. Dividends, taxes, actual brokerage, and market impact are unavailable. The source ends in 2018; signals are historical outputs.

## Rebuild and validate

```bash
python build_project.py
python build_report.py
python -m pip install pytest==9.1.1
python -m pytest tests -q
```

`build_project.py` regenerates both SQL scripts, the SQLite database, the numbered query-result CSVs, and the data profile. `build_report.py` regenerates the report and charts. The delivered MP4 and screenshots are already included and do not need regeneration to run the application.

See `VALIDATION.txt` for the executed checks. Independent pandas calculations validate every raw/adjusted moving average, event, return, drawdown, and volatility. Both complete SQL scripts were rerun, and the MySQL outputs reconcile with SQLite. Original records and the dataset SHA-256 are provided under `data/` and `reports/data_profile.json`.

## Deploy on Streamlit Community Cloud

Create a GitHub repository containing this project's files, including the `data`, `sql`, `reports`, and `.streamlit` folders. In Streamlit Community Cloud, select that repository, set the main file to `app.py`, and choose Python 3.12. The requirements file supplies dependencies. Local execution is verified; an online Streamlit URL is not included in this package.

## Issuer sources

- Infosys quarterly results, Q1 FY2016, additional information page 5: https://www.infosys.com/investors/reports-filings/quarterly-results/2015-2016/q1/documents/q1-2016.pdf
- TCS Annual Report 2018–19, printed page 89: https://www.tcs.com/content/dam/tcs/pdf/discover-tcs/investor-relations/faq/annual-report-18-19-intimation-record-date-agm-24.pdf

The six original CSVs, student guide, and course presentation were supplied in the uploaded assignment ZIP. The report references numbered task exports so that every main claim can be inspected.
