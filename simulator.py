"""Educational long-only execution scenario, separate from the assignment's SQL analysis."""
import pandas as pd


def simulate(rows: pd.DataFrame, initial_cash=100000.0, fee_bps=10.0, slippage_bps=0.0):
    """A close signal may trade at the next observed day's open, never the same close.

    Fractional units, no shorting, no dividends/taxes, final close liquidation.
    rows must contain one stock and use bonus-adjusted OHLC.
    """
    if rows.empty or rows.stock.nunique() != 1 or not rows.basis.eq("Adjusted").all():
        raise ValueError("Select one stock with adjusted prices for this scenario.")
    if initial_cash <= 0 or not 0 <= fee_bps <= 100 or not 0 <= slippage_bps <= 100:
        raise ValueError("Invalid starting cash or costs.")
    d = rows.sort_values("date").reset_index(drop=True)
    fee, slip = fee_bps / 10000.0, slippage_bps / 10000.0
    cash, units, pending = float(initial_cash), 0.0, None
    ledger, equity = [], []
    for _, row in d.iterrows():
        if pending == "Buy" and units == 0:
            fill = float(row.open_price) * (1.0 + slip)
            units = cash / (fill * (1.0 + fee))
            gross = units * fill
            cost = gross * fee
            ledger.append({"date":row.date,"side":"Buy","fill_price":fill,"units":units,"fee_inr":cost,"signal_date":signal_date,"execution":"Next open"})
            cash = 0.0
        elif pending == "Sell" and units > 0:
            fill = float(row.open_price) * (1.0 - slip)
            gross, cost = units * fill, units * fill * fee
            ledger.append({"date":row.date,"side":"Sell","fill_price":fill,"units":units,"fee_inr":cost,"signal_date":signal_date,"execution":"Next open"})
            cash, units = gross - cost, 0.0
        equity.append({"date":row.date,"equity_inr":cash + units * float(row.close_price)})
        pending = row.signal if row.signal in ("Buy","Sell") else None
        signal_date = row.date
    if units > 0:
        fill = float(d.iloc[-1].close_price) * (1.0 - slip)
        gross, cost = units * fill, units * fill * fee
        ledger.append({"date":d.iloc[-1].date,"side":"Sell","fill_price":fill,"units":units,"fee_inr":cost,"signal_date":None,"execution":"Final close liquidation"})
        cash, units = gross - cost, 0.0
        equity[-1]["equity_inr"] = cash
    ledger_df = pd.DataFrame(ledger, columns=["date","side","fill_price","units","fee_inr","signal_date","execution"])
    equity_df = pd.DataFrame(equity)
    return {
        "equity":equity_df, "trades":ledger_df,
        "final_cash":cash, "return_pct":100.0*(cash/initial_cash-1.0),
        "fees_inr":float(ledger_df.fee_inr.sum()) if len(ledger_df) else 0.0,
        "executions":len(ledger_df),
    }
