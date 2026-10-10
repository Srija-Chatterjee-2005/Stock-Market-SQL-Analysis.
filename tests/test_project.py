"""Independent checks of assignment checkpoints, SQL safety, filters and execution timing."""
from pathlib import Path
import sqlite3
import sys
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine import (ROOT,STOCKS,build_connection,dataframe,task_queries,filtered_rows,
                    filtered_summary,rebased_rows,run_readonly_query,set_selected_view)
from simulator import simulate


@pytest.fixture(scope="module")
def conn():
    c=build_connection()
    yield c
    c.close()


def test_source_coverage_and_quality(conn):
    calendars=[]
    for table in STOCKS.values():
        d=dataframe(conn,f"SELECT * FROM {table} ORDER BY date")
        assert len(d)==889
        assert d.date.is_unique
        assert (d.date.iloc[0],d.date.iloc[-1])==('2015-01-01','2018-07-31')
        assert d.close_price.notna().all() and d.close_price.gt(0).all()
        assert d.deliverable_qty.isna().sum()==1
        assert d.pct_deli_qty.isna().sum()==1
        assert (d.high_price >= d[['open_price','close_price','low_price']].max(axis=1)).all()
        assert (d.low_price <= d[['open_price','close_price']].min(axis=1)).all()
        calendars.append(tuple(d.date))
    assert len(set(calendars))==1
    assert conn.execute("SELECT COUNT(*) FROM master_table").fetchone()[0]==889


def test_all_student_checkpoints(conn):
    q=task_queries()
    assert tuple(dataframe(conn,q['01 | History coverage']).iloc[0])==(889,'2015-01-01','2018-07-31')
    e=dataframe(conn,q["02 | Eicher's five highest closes"])
    assert len(e)==5 and e.close_price.iloc[0]==32786.4
    assert e.date.str.startswith('2017-09').all()
    annual=dataframe(conn,q['03 | TCS annual average'])
    assert annual.loc[annual.year.eq('2016'),'avg_close'].iloc[0]==2419.0
    gaps=dataframe(conn,q['04 | Missing delivery quantities'])
    assert len(gaps)==6 and gaps.date.nunique()==2
    b=dataframe(conn,q['05 | Bajaj moving averages'])
    assert b.ma20.isna().sum()==19 and b.ma50.isna().sum()==49
    assert tuple(b[b.ma20.notna()].iloc[0][['date','ma20']])==('2015-01-29',2415.53)
    assert tuple(b[b.ma50.notna()].iloc[0][['date','ma50']])==('2015-03-13',2283.8)
    assert b.iloc[-1].ma20==2918.51
    b2=dataframe(conn,q['07 | Bajaj crossover signals'])
    assert b2[b2.signal.eq('Buy')].date.iloc[0]=='2015-05-18'
    assert b2[b2.signal.eq('Sell')].date.iloc[0]=='2015-08-24'
    assert b2.signal.value_counts().to_dict()=={'Hold':866,'Buy':12,'Sell':11}
    assert dataframe(conn,q['09 | Signal on a given date']).iloc[0,0]=='Buy'
    all_sig=dataframe(conn,q['10 | All six signal summaries'])
    assert (all_sig.buys.sum(),all_sig.sells.sum())==(56,57)
    raw=dataframe(conn,q['11 | Raw full-period price change'])
    assert raw.iloc[0].stock=='TVS Motors' and raw.iloc[0]['pct_change']==86.9
    assert raw['pct_change'].lt(0).sum()==2
    adjusted=dataframe(conn,q['13 | Bonus-adjusted IT returns'])
    assert dict(zip(adjusted.stock,adjusted.adjusted_pct_change))=={'Infosys':38.2,'TCS':52.4}


def test_windows_signals_and_risk_against_independent_pandas(conn):
    all_rows=dataframe(conn,'SELECT * FROM v_analytics ORDER BY basis,stock,date')
    for (basis,stock),d in all_rows.groupby(['basis','stock']):
        close=d.close_price.reset_index(drop=True)
        m20,m50=close.rolling(20).mean(),close.rolling(50).mean()
        expected=np.where((m20>m50)&(m20.shift()<=m50.shift()),'Buy',
                         np.where((m20<m50)&(m20.shift()>=m50.shift()),'Sell','Hold'))
        np.testing.assert_allclose(d.ma20,m20,equal_nan=True,rtol=1e-10)
        np.testing.assert_allclose(d.ma50,m50,equal_nan=True,rtol=1e-10)
        assert d.signal.tolist()==expected.tolist()
        np.testing.assert_allclose(d.daily_return_pct,close.pct_change()*100,equal_nan=True,rtol=1e-9)
        np.testing.assert_allclose(d.drawdown_pct,(close/close.cummax()-1)*100,equal_nan=True,rtol=1e-9,atol=1e-9)
        summary=dataframe(conn,'SELECT * FROM v_stock_summary WHERE basis=? AND stock=?',(basis,stock)).iloc[0]
        assert np.isclose(summary.annual_volatility_pct,(close.pct_change()*100).std(ddof=1)*np.sqrt(252))
        assert np.isclose(summary.max_drawdown_pct,((close/close.cummax()-1)*100).min())


def test_exact_adjustment_boundaries_and_changed_signals(conn):
    for stock,day in [('Infosys','2015-06-15'),('TCS','2018-05-31')]:
        data=dataframe(conn,'SELECT * FROM v_analytics WHERE basis=? AND stock=? ORDER BY date',('Adjusted',stock))
        before=data.date.lt(day)
        np.testing.assert_allclose(data.loc[before,'close_price'],data.loc[before,'raw_close']/2)
        np.testing.assert_allclose(data.loc[~before,'close_price'],data.loc[~before,'raw_close'])
        assert abs(data.loc[data.date.eq(day),'daily_return_pct'].iloc[0])<1
    changes=dataframe(conn,'SELECT * FROM v_signal_changes ORDER BY stock,date')
    assert len(changes)==5
    tcs=changes[changes.stock.eq('TCS')].iloc[0]
    assert (tcs.date,tcs.raw_signal,tcs.adjusted_signal)==('2018-06-05','Sell','Hold')


def test_filter_scope_warmup_and_rebase(conn):
    d=filtered_rows(conn,'Adjusted',['TCS','Infosys'],'2018-05-01','2018-06-30')
    assert set(d.stock)=={'TCS','Infosys'}
    assert d.ma50.notna().all()  # filtering must not restart the 50-day window
    s=filtered_summary(conn,'Adjusted',['TCS','Infosys'],'2018-05-01','2018-06-30')
    for _,r in s.iterrows():
        per=d[d.stock.eq(r.stock)]
        assert np.isclose(r.price_change_pct,100*(per.close_price.iloc[-1]/per.close_price.iloc[0]-1))
        assert r.buys==per.signal.eq('Buy').sum() and r.sells==per.signal.eq('Sell').sum()
    rebase=rebased_rows(conn,'Adjusted',['TCS','Infosys'],'2018-05-01','2018-06-30')
    assert rebase.groupby('stock').first().indexed_price.eq(100).all()
    assert filtered_rows(conn,'Raw',[],'2018-05-01','2018-06-30').empty
    assert filtered_rows(conn,'Raw',['TCS'],'2018-07-29','2018-07-29').empty
    set_selected_view(conn,'Adjusted',['TCS'],'2018-05-01','2018-06-30')
    selected,_,_=run_readonly_query(conn,'SELECT * FROM selected_prices')
    assert selected.stock.eq('TCS').all() and selected.date.between('2018-05-01','2018-06-30').all()


@pytest.mark.parametrize('sql',[
 'DELETE FROM bajaj_auto;', 'DROP TABLE bajaj_auto;',
 "ATTACH DATABASE '/tmp/other.db' AS another;",'PRAGMA writable_schema=ON;',
 'SELECT 1; SELECT 2;', 'WITH x AS (SELECT 1) DELETE FROM bajaj_auto;',
 "SELECT load_extension('bad');",'CREATE TABLE x(a INTEGER);'])
def test_playground_blocks_writes(conn,sql):
    with pytest.raises(ValueError): run_readonly_query(conn,sql)
    assert conn.execute('SELECT COUNT(*) FROM bajaj_auto').fetchone()[0]==889


def test_playground_cap_timeout_errors_and_duplicate_columns(conn):
    d,_,cut=run_readonly_query(conn,'SELECT * FROM bajaj_auto ORDER BY date;',row_limit=100)
    assert len(d)==100 and cut
    d,_,cut=run_readonly_query(conn,'SELECT 1 AS x, 2 AS x;')
    assert list(d.columns)==['x','x_2'] and not cut
    d,_,_=run_readonly_query(conn,'WITH x AS (SELECT 1 AS a) SELECT * FROM x;')
    assert d.iloc[0,0]==1
    with pytest.raises(ValueError): run_readonly_query(conn,'SELECT missing FROM bajaj_auto')
    with pytest.raises(ValueError,match='time limit'):
        run_readonly_query(conn,'WITH RECURSIVE seq(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM seq) SELECT SUM(x) FROM seq;',timeout_seconds=.01)
    assert conn.execute('SELECT COUNT(*) FROM bajaj_auto').fetchone()[0]==889


def test_sqlite_script_is_fresh_and_repeatable():
    script=(ROOT/'sql/Stock_Market_Analysis_SQLite.sql').read_text()
    fresh=sqlite3.connect(':memory:')
    for _ in range(2):
        fresh.executescript(script)
        assert fresh.execute('SELECT COUNT(*) FROM v_prices').fetchone()[0]==5334
        assert fresh.execute("SELECT SUM(buys), SUM(sells) FROM v_stock_summary WHERE basis='Raw'").fetchone()==(56,57)
    fresh.close()


def test_scenario_next_open_and_fee_math():
    rows=pd.DataFrame({'basis':['Adjusted']*3,'stock':['Demo']*3,
                       'date':['2020-01-01','2020-01-02','2020-01-03'],
                       'open_price':[100.,120.,90.],'close_price':[100.,110.,95.],
                       'signal':['Buy','Sell','Hold']})
    gross=simulate(rows,initial_cash=120.,fee_bps=0)
    assert gross['trades'].date.tolist()==['2020-01-02','2020-01-03']
    assert gross['trades'].signal_date.tolist()==['2020-01-01','2020-01-02']
    assert gross['trades'].fill_price.tolist()==[120.,90.]
    assert gross['final_cash']==90.
    net=simulate(rows,initial_cash=120.,fee_bps=10)
    assert np.isclose(net['final_cash'],120./(120.*1.001)*90.*.999)


def test_streamlit_interactions():
    from streamlit.testing.v1 import AppTest
    at=AppTest.from_file(str(ROOT/'app.py'),default_timeout=40).run()
    assert not at.exception
    at.multiselect(key='stocks').set_value(['TCS']).run()
    assert not at.exception
    at.selectbox(key='price_basis').set_value('Raw').run()
    assert not at.exception
    at.button(key='reset_filters').click().run()
    assert at.multiselect(key='stocks').value==list(STOCKS)
    assert at.selectbox(key='price_basis').value=='Adjusted'
    for page in ['Stock explorer','Signals & comparisons','Corporate actions','SQL playground','Data & downloads']:
        at.radio(key='page').set_value(page).run()
        assert not at.exception, page
    at.radio(key='page').set_value('SQL playground').run()
    at.button(key='run_sql').click().run()
    assert not at.exception and at.success
    at.text_area(key='sql_editor').set_value('DROP TABLE tcs;').run()
    at.button(key='run_sql').click().run()
    assert at.error and not at.exception
    at.button(key='reset_sql').click().run()
    assert 'WITH prices' in at.text_area(key='sql_editor').value
    at.radio(key='page').set_value('Signals & comparisons').run()
    at.date_input(key='lookup_date').set_value(pd.Timestamp('2018-07-29').date()).run()
    assert any('No trading row' in i.value for i in at.info)
    at.radio(key='page').set_value('Overview').run()
    at.multiselect(key='stocks').set_value([]).run()
    assert not at.exception and any('Select at least one stock' in i.value for i in at.info)
