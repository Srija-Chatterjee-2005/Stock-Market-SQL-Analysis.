"""Stock Market Analysis: a SQL-backed Streamlit dashboard and live playground."""
from datetime import date
from io import BytesIO
from pathlib import Path
import hashlib
import html
import json
import sqlite3

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from engine import (ROOT, STOCKS, EVENTS, build_connection, dataset_fingerprint,
                    dataframe, filtered_rows, filtered_summary, rebased_rows,
                    task_queries, run_readonly_query, set_selected_view)
from simulator import simulate

st.set_page_config(page_title="Stock Market Analysis",page_icon="📈",layout="wide",initial_sidebar_state="auto")

COLORS = {"Bajaj Auto":"#4F46E5","Eicher Motors":"#0D9488","Hero Motocorp":"#F59E0B",
          "Infosys":"#8B5CF6","TCS":"#2563EB","TVS Motors":"#E15A68"}
PAGES = ["Overview","Stock explorer","Signals & comparisons","Corporate actions","SQL playground","Data & downloads"]
SIGNAL_COLOR = {"Buy":"#059669","Sell":"#E05260","Hold":"#72819A"}
MIN_DATE, MAX_DATE = date(2015,1,1), date(2018,7,31)

st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap');
html,body,[class*="css"],.stApp {font-family:'DM Sans',sans-serif;}
.block-container {padding-top:1.6rem;padding-bottom:2.5rem;max-width:1550px;}
header[data-testid="stHeader"]{background:rgba(245,247,252,.9);}
[data-testid="stSidebar"]{background:#121D34;}
[data-testid="stSidebar"] h1,[data-testid="stSidebar"] h2,[data-testid="stSidebar"] h3,[data-testid="stSidebar"] h4,
[data-testid="stSidebar"] p,[data-testid="stSidebar"] label,[data-testid="stSidebar"] .stCaption,
[data-testid="stSidebar"] [data-testid="stWidgetLabel"]{color:#E6EAF4 !important;}
[data-testid="stSidebar"] hr{border-color:#32415C;}
[data-testid="stSidebar"] [data-baseweb="tag"]{background:#3E4C78;}
[data-testid="stSidebar"] [data-testid="stBaseButton-secondary"]{color:#17243B;}
[data-testid="stSidebar"] button p{color:#17243B !important;}
[data-testid="stSidebarCollapseButton"] button{color:#E6EAF4 !important;}
h1{font-size:2rem !important;letter-spacing:-.055rem;font-weight:700 !important;}
h2{font-size:1.38rem !important;letter-spacing:-.02rem;}
h3{font-size:1.04rem !important;}
[data-testid="stMetric"]{background:#FFFFFF;border:1px solid #E3E8F1;border-radius:16px;padding:18px 20px;box-shadow:0 3px 16px #15254005;}
[data-testid="stMetricLabel"]{color:#67758D;font-size:.86rem;}
[data-testid="stMetricValue"]{font-size:1.8rem;letter-spacing:-.04rem;}
[data-testid="stVerticalBlockBorderWrapper"]>div{border-radius:16px;}
[data-testid="stPlotlyChart"]{border-radius:12px;overflow:hidden;}
[data-testid="stDataFrame"]{border-radius:10px;}
.hero{background:linear-gradient(110deg,#182445,#233771);border-radius:20px;padding:27px 30px;color:#FFF;margin-bottom:22px;}
.hero h1{color:#FFF;margin:0;padding:0 !important;font-size:2rem !important;}
.hero p{color:#CBD4F1;font-size:.92rem;margin:9px 0 0;line-height:1.6;}
.pill{display:inline-block;background:#394B79;padding:4px 10px;border-radius:20px;font-size:.75rem;color:#DBE7FF;margin-bottom:11px;}
.note{border-left:3px solid #5A55E9;background:#EEF0FF;padding:13px 17px;border-radius:0 10px 10px 0;color:#344367;font-size:.9rem;margin-bottom:12px;}
.mini{color:#8DA0C4;font-size:.76rem;line-height:1.6;}
div[data-testid="stTextArea"] textarea{font-family:ui-monospace,SFMono-Regular,Consolas,monospace !important;line-height:1.55;}
.stButton button,.stDownloadButton button{border-radius:9px;}
@media(max-width:750px){.block-container{padding:1rem;}.hero{padding:20px;}[data-testid="stMetric"]{padding:13px;}
[data-testid="stMain"] [data-testid="stHorizontalBlock"]{flex-direction:column;}
[data-testid="stMain"] [data-testid="stColumn"]{width:100% !important;flex:1 1 100% !important;min-width:0 !important;}}
</style>""",unsafe_allow_html=True)


@st.cache_data(show_spinner="Preparing the SQL database...")
def database_bytes(fingerprint):
    conn = build_connection()
    data = conn.serialize()
    conn.close()
    return data


def new_connection():
    conn = sqlite3.connect(":memory:")
    conn.deserialize(database_bytes(dataset_fingerprint()))
    return conn


def chart(fig, height=340):
    fig.update_layout(template="plotly_white",height=height,
                      paper_bgcolor="rgba(0,0,0,0)",plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(family="DM Sans, Arial",color="#52637A",size=12),
                      margin=dict(l=12,r=18,t=18,b=24),
                      legend=dict(orientation="h",yanchor="bottom",y=1.02,xanchor="left",x=0,title_text=""),
                      hovermode="x unified")
    fig.update_xaxes(showgrid=False,zeroline=False)
    fig.update_yaxes(gridcolor="#E8EDF5",zerolinecolor="#D5DDEA")
    st.plotly_chart(fig,width="stretch",config={"displaylogo":False,"modeBarButtonsToRemove":["lasso2d","select2d"]})


def table(df,key=None):
    st.dataframe(df,hide_index=True,width="stretch",key=key)


def csv_button(df,label,filename,key):
    st.download_button(label,df.to_csv(index=False).encode("utf-8"),file_name=filename,mime="text/csv",key=key)


def reset_filters():
    st.session_state["stocks"] = list(STOCKS)
    st.session_state["price_basis"] = "Adjusted"
    st.session_state["date_range"] = (MIN_DATE,MAX_DATE)


def section(title,detail=None):
    st.subheader(title)
    if detail:
        st.caption(detail)


def query_history(sql,rows,elapsed):
    log=st.session_state.setdefault("query_history",[])
    log.insert(0,{"query":sql,"rows":rows,"elapsed_ms":round(elapsed,2)})
    del log[10:]


def render_overview(conn,rows,summary,basis,stocks,start,end):
    count_buy,count_sell=int(summary.buys.sum()),int(summary.sells.sum())
    best=summary.iloc[0]
    cols=st.columns(4)
    cols[0].metric("Stocks in view",str(len(summary)))
    cols[1].metric("Trading dates",f"{rows.date.nunique():,}")
    cols[2].metric("Highest price change",f"{best.price_change_pct:+.1f}%",help=f"{best.stock}. Close on the last selected trading date / close on the first selected trading date minus 1.")
    cols[3].metric("Crossover events",f"{count_buy+count_sell}",help=f"{count_buy} Buy crossings and {count_sell} Sell crossings in the selected dates. Events are historical rule outputs.")
    st.write("")
    left,right=st.columns([1.7,1])
    with left, st.container(border=True):
        section("How the stocks moved","Rebased to 100 at each stock's first selected close. Price change excludes dividends.")
        rebased=rebased_rows(conn,basis,stocks,start,end)
        fig=px.line(rebased,x="date",y="indexed_price",color="stock",color_discrete_map=COLORS)
        fig.add_hline(y=100,line_dash="dot",line_color="#A9B6CC")
        fig.update_layout(yaxis_title="Indexed close",xaxis_title=None)
        chart(fig,370)
    with right,st.container(border=True):
        section("Price change by stock","First-to-last selected close")
        display=summary.sort_values("price_change_pct")
        fig=px.bar(display,x="price_change_pct",y="stock",orientation="h",color="stock",color_discrete_map=COLORS,text=display.price_change_pct.map(lambda x:f"{x:+.1f}%"))
        fig.update_layout(showlegend=False,xaxis_title="Price change (%)",yaxis_title=None)
        fig.update_traces(textposition="outside",cliponaxis=False)
        low=min(0.0,float(display.price_change_pct.min()))
        high=max(0.0,float(display.price_change_pct.max()))
        span=max(1.0,high-low)
        fig.update_xaxes(range=[low-.25*span if low<0 else 0,high+.3*span if high>0 else .3*span])
        chart(fig,370)
    with st.container(border=True):
        section("What the latest signals say","Latest crossover is the most recent Buy/Sell inside the selected period. Hold means no crossing on that day.")
        view=summary[["stock","first_close","last_close","price_change_pct","buys","sells","last_signal_date","last_signal","final_regime"]].copy()
        view.columns=["Stock","First close (INR)","Last close (INR)","Price change (%)","Buy events","Sell events","Latest crossover date","Latest crossover","Final MA regime"]
        for col in ["First close (INR)","Last close (INR)","Price change (%)"]:
            view[col]=view[col].round(2)
        table(view)
        csv_button(summary,"Download this summary","selected_stock_summary.csv","overview_csv")
    if basis=="Raw":
        st.info("Raw prices preserve the assignment checkpoints. TCS and Infosys contain bonus-share price jumps. Use Adjusted for price-change comparisons across those events.")
    else:
        st.caption("Adjusted OHLC divides prices before Infosys 15 June 2015 and TCS 31 May 2018 by 2. Volume remains as reported. Data ends 31 July 2018.")


def render_explorer(conn,rows,summary,basis,stocks,start,end):
    cols=st.columns([2,1,1])
    stock=cols[0].selectbox("Explore a stock",stocks,key="explore_stock")
    style=cols[1].selectbox("Price chart",["Line","Candlestick"],key="chart_style")
    show_ma=cols[2].checkbox("Show 20/50-day averages",value=True,key="show_ma")
    d=rows[rows.stock.eq(stock)].copy()
    s=summary[summary.stock.eq(stock)].iloc[0]
    metrics=st.columns(4)
    metrics[0].metric("Last selected close",f"INR {s.last_close:,.2f}")
    metrics[1].metric("Period price change",f"{s.price_change_pct:+.2f}%")
    metrics[2].metric("Buy / Sell events",f"{int(s.buys)} / {int(s.sells)}")
    metrics[3].metric("Final day event",s.final_day_signal,help="Latest crossover and final-day event are different. Hold does not mean cash or no trend.")
    with st.container(border=True):
        section(f"{stock}: price, averages and crossings","Moving averages use the full available history before date filters. The first 19/49 observations remain blank.")
        fig=make_subplots(rows=2,cols=1,shared_xaxes=True,vertical_spacing=.06,row_heights=[.77,.23])
        if style=="Line":
            fig.add_trace(go.Scatter(x=d.date,y=d.close_price,name=f"{basis} close",line=dict(color=COLORS[stock],width=2.2)),row=1,col=1)
        else:
            fig.add_trace(go.Candlestick(x=d.date,open=d.open_price,high=d.high_price,low=d.low_price,close=d.close_price,name=f"{basis} OHLC",increasing_line_color="#0D9488",decreasing_line_color="#E05260"),row=1,col=1)
        if show_ma:
            for col,label,color in [("ma20","20-day MA","#F59E0B"),("ma50","50-day MA","#8B5CF6")]:
                fig.add_trace(go.Scatter(x=d.date,y=d[col],name=label,line=dict(color=color,width=1.7)),row=1,col=1)
        for signal,symbol in [("Buy","triangle-up"),("Sell","triangle-down")]:
            events=d[d.signal.eq(signal)]
            fig.add_trace(go.Scatter(x=events.date,y=events.close_price,mode="markers",name=signal,marker=dict(color=SIGNAL_COLOR[signal],size=12,symbol=symbol,line=dict(color="white",width=1))),row=1,col=1)
        fig.add_trace(go.Bar(x=d.date,y=d.no_of_shares,name="Reported shares traded",marker_color="#C5D2E8"),row=2,col=1)
        fig.update_layout(xaxis_rangeslider_visible=False,yaxis_title="Price (INR)",yaxis2_title="Shares",bargap=.1)
        chart(fig,560)
    a,b=st.columns(2)
    with a,st.container(border=True):
        section("Daily price moves","Close-to-close change. The first visible day may use a previous close before the date filter.")
        fig=px.bar(d,x="date",y="daily_return_pct",color=np.where(d.daily_return_pct.ge(0),"Up","Down"),color_discrete_map={"Up":"#0D9488","Down":"#E05260"})
        fig.update_layout(xaxis_title=None,yaxis_title="Daily move (%)",showlegend=False)
        chart(fig,270)
    with b,st.container(border=True):
        section("Decline from the previous peak","Peak measured from the beginning of the dataset, including history before the selected range.")
        fig=px.area(d,x="date",y="drawdown_pct",color_discrete_sequence=["#8B5CF6"])
        fig.update_layout(xaxis_title=None,yaxis_title="Drawdown (%)")
        chart(fig,270)
    with st.expander("Inspect the displayed trading rows"):
        table(d[["date","open_price","high_price","low_price","close_price","ma20","ma50","signal","no_of_shares"]])
        csv_button(d,"Download displayed stock data",f"{stock.replace(' ','_')}_{basis.lower()}.csv","explorer_csv")


def render_signals(conn,rows,summary,basis,stocks,start,end):
    overview,lookup,scenario=st.tabs(["Signal history","Date lookup","Execution scenario"])
    with overview:
        a,b=st.columns([1,1.5])
        with a,st.container(border=True):
            section("Crossover frequency")
            long=summary.melt(id_vars="stock",value_vars=["buys","sells"],var_name="event",value_name="count")
            long.event=long.event.map({"buys":"Buy","sells":"Sell"})
            fig=px.bar(long,x="stock",y="count",color="event",barmode="group",color_discrete_map=SIGNAL_COLOR)
            fig.update_layout(xaxis_title=None,yaxis_title="Events")
            chart(fig,340)
        with b,st.container(border=True):
            section("Signal events","Historical crossings inside the sidebar selection")
            choice=st.multiselect("Event types",["Buy","Sell","Hold"],default=["Buy","Sell"],key="signal_types")
            events=rows[rows.signal.isin(choice)][["stock","date","close_price","ma20","ma50","signal"]].sort_values(["date","stock"],ascending=[False,True])
            table(events.head(300),key="signal_events")
            csv_button(events,"Download all matching events","filtered_signal_events.csv","signals_csv")
        section("Signals that reversed within a month","A quick reversal is an opposite crossover within 30 calendar days. It is a warning about trading frequency, not proof of a loss.")
        marks=",".join("?" for _ in stocks)
        rapid=dataframe(conn,f"SELECT stock, previous_signal_date, previous_signal, date, `signal`, gap_calendar_days, gap_trading_days FROM v_crossovers WHERE basis=? AND stock IN ({marks}) AND date BETWEEN ? AND ? AND gap_calendar_days<=30 ORDER BY stock,date",(basis,*stocks,str(start),str(end)))
        table(rapid)
        st.caption("The previous crossing can be before the selected start date. The new crossing must be inside the selection.")
    with lookup:
        cols=st.columns(2)
        stock=cols[0].selectbox("Stock for date lookup",stocks,key="lookup_stock")
        day=cols[1].date_input("Trading date to inspect",value=date(2018,6,21),min_value=MIN_DATE,max_value=MAX_DATE,key="lookup_date")
        lookup_row=dataframe(conn,"SELECT stock,date,close_price,ma20,ma50,`signal`,regime FROM v_analytics WHERE basis=? AND stock=? AND date=?",(basis,stock,str(day)))
        st.caption("Date lookup uses the chosen price basis and stock. Its date control works independently of the sidebar range.")
        if lookup_row.empty:
            st.info("No trading row exists for that date. A missing day is not a Hold signal.")
        else:
            table(lookup_row)
            st.success(f"{stock} on {day.strftime('%d %b %Y')}: {lookup_row.iloc[0].signal}")
    with scenario:
        st.markdown('<div class="note">Educational execution scenario. A close-of-day crossing can trade at the next observed open. Adjusted prices handle the two bonus issues.</div>',unsafe_allow_html=True)
        a,b,c=st.columns(3)
        stock=a.selectbox("Stock for scenario",stocks,key="scenario_stock")
        fee=b.slider("Fee per transaction (basis points)",0,100,10,key="fee_bps",help="10 basis points is 0.10% of traded value. This is an assumption, not measured brokerage.")
        slip=c.slider("Slippage per transaction (basis points)",0,100,0,key="slippage_bps")
        scenario_rows=filtered_rows(conn,"Adjusted",[stock],start,end)
        result=simulate(scenario_rows,fee_bps=fee,slippage_bps=slip)
        gross=simulate(scenario_rows,fee_bps=0,slippage_bps=0)
        cols=st.columns(4)
        cols[0].metric("Final account value",f"INR {result['final_cash']:,.0f}")
        cols[1].metric("Scenario return",f"{result['return_pct']:+.2f}%")
        cols[2].metric("Fees paid",f"INR {result['fees_inr']:,.0f}")
        cols[3].metric("Trade executions",result["executions"])
        fig=px.line(result["equity"],x="date",y="equity_inr",color_discrete_sequence=["#4F46E5"])
        fig.update_layout(xaxis_title=None,yaxis_title="Account value (INR)")
        chart(fig,320)
        st.caption(f"Starts with INR 100,000 in cash at the first selected date. Long-only, fractional units, initial Sell events ignored, no same-day fills, final-close liquidation if invested. Dividends and taxes excluded. Zero-cost scenario return: {gross['return_pct']:+.2f}%. Cost drag: {gross['return_pct']-result['return_pct']:.2f} percentage points.")
        table(result["trades"].round(4))
        csv_button(result["trades"],"Download the scenario trade log","execution_scenario.csv","scenario_csv")


def render_actions(conn,stocks):
    st.caption("Corporate-action analysis uses the full supplied period, independent of sidebar dates. The stock selection still applies.")
    section("Two price cliffs change the story","Pre-event prices are divided by 2 for a 1:1 bonus issue. The event day itself stays unchanged.")
    marks=",".join("?" for _ in stocks)
    summary=dataframe(conn,f"SELECT stock,basis,price_change_pct,buys,sells,last_signal_date,last_signal FROM v_stock_summary WHERE stock IN ({marks}) ORDER BY stock,basis",stocks)
    comparison=summary.pivot(index="stock",columns="basis",values="price_change_pct").reset_index()
    comparison["Change (percentage points)"]=comparison.Adjusted-comparison.Raw
    comparison.columns=["Stock","Adjusted price change (%)","Raw price change (%)","Change (percentage points)"]
    table(comparison.round(2))
    charts=st.columns(2)
    for i,stock in enumerate([s for s in ["Infosys","TCS"] if s in stocks]):
        event=next(e for e in EVENTS if e[0]==stock)
        with charts[i],st.container(border=True):
            section(f"{stock}: around {event[1]}",event[3])
            d=dataframe(conn,"SELECT date, close_price,basis FROM v_analytics WHERE stock=? AND date BETWEEN date(?,'-45 day') AND date(?,'+45 day') ORDER BY basis,date",(stock,event[1],event[1]))
            fig=px.line(d,x="date",y="close_price",color="basis",color_discrete_map={"Raw":"#A7B5CB","Adjusted":"#4F46E5"})
            fig.add_vline(x=event[1],line_dash="dot",line_color="#E05260")
            fig.update_layout(yaxis_title="Close (INR)",xaxis_title=None)
            chart(fig,300)
            st.link_button("Issuer source",event[4])
    section("How the crossover events change","Unrounded 20/50-day averages are recomputed after adjustment.")
    table(summary.round(2))
    differences=dataframe(conn,f"SELECT * FROM v_signal_changes WHERE stock IN ({marks}) ORDER BY stock,date",stocks)
    table(differences)
    st.markdown("The supplied course slides contain price and arithmetic errors for TCS and Infosys. Raw TCS is **-23.82%** from INR 2,548.20 to INR 1,941.25. Raw Infosys is **-30.91%**, rather than the slide's stated 3% decline. After bonus adjustment, they become **+52.36%** and **+38.17%** respectively. Eicher's computed **82.5623%** rounds to **82.6%** at one decimal.")
    st.caption("Only the two identified bonus events are adjusted. Adjusted price change excludes dividend reinvestment. TCS's June 2018 record/allotment dates differ from the 31 May ex-bonus trading date used here.")


def render_sql(conn):
    section("Live SQL playground","SQLite runs against the actual supplied dataset. The submission also includes a separate MySQL 8 script.")
    presets=task_queries()
    custom="Custom query"
    options=[custom,*presets]
    previous=st.session_state.get("loaded_preset",options[10])
    option=st.selectbox("Choose an assignment task",options,index=options.index(previous) if previous in options else 10,key="sql_preset")
    if st.session_state.get("loaded_preset") != option or "sql_editor" not in st.session_state:
        same=st.session_state.get("loaded_preset")==option
        draft=st.session_state.get("sql_draft") if same else None
        st.session_state["sql_editor"]=draft if draft is not None else presets.get(option,"SELECT stock, date, close_price, `signal` FROM selected_prices LIMIT 20;")
        st.session_state["loaded_preset"]=option
        st.session_state.pop("sql_result",None)
    editor,reference=st.columns([2.2,1])
    with editor:
        sql=st.text_area("SQL query",key="sql_editor",height=270,label_visibility="collapsed")
        st.session_state["sql_draft"]=sql
        a,b,c=st.columns([1,1,1.3])
        run=a.button("Run SQL",type="primary",width="stretch",key="run_sql")
        limit=b.selectbox("Result row limit",[100,1000,6000,20000],index=1,key="sql_row_limit",label_visibility="collapsed")
        reset=c.button("Reset query",width="stretch",key="reset_sql")
        if reset:
            st.session_state["pending_sql_reset"]=presets.get(option,"SELECT * FROM selected_prices LIMIT 20;")
            st.rerun()
        st.caption("Run one SELECT or WITH ... SELECT at a time. Queries use full tables unless you query selected_prices, which follows sidebar filters. Results stop at the chosen row limit. Execution limit: 5 seconds.")
    with reference,st.container(border=True):
        st.markdown("**Database reference**")
        relation=st.selectbox("Inspect a table or view",list(STOCKS.values())+["bajaj1","bajaj2","master_table","corporate_actions","v_analytics","v_stock_summary","v_crossovers","v_signal_changes","selected_prices"],key="schema_table")
        columns=dataframe(conn,f'PRAGMA table_info("{relation}")')
        table(columns[["name","type"]])
        st.caption("SQLite dates are YYYY-MM-DD text. MySQL uses DATE. NULL is a missing value. Quote signal with backticks for MySQL.")
    if run:
        try:
            result,elapsed,truncated=run_readonly_query(conn,sql,row_limit=limit)
            st.session_state["sql_result"]={"rows":result,"ms":elapsed,"truncated":truncated,"sql":sql,"scope":st.session_state["scope_token"]}
            query_history(sql,len(result),elapsed)
        except ValueError as exc:
            st.session_state.pop("sql_result",None)
            st.error(str(exc))
    saved=st.session_state.get("sql_result")
    if saved:
        if saved["sql"] != sql:
            st.info("Showing the last executed query's result. Run SQL to apply your editor changes.")
        if saved["scope"] != st.session_state["scope_token"]:
            st.info("The sidebar selection changed. Run SQL again to refresh this result.")
        st.success(f"{len(saved['rows']):,} rows displayed in {saved['ms']:.0f} ms")
        if saved["truncated"]:
            st.warning(f"The result contains more than {len(saved['rows']):,} rows. Increase the row limit to inspect more.")
        result=saved["rows"]
        table(result,key="sql_result_table")
        a,b,c=st.columns(3)
        with a:
            csv_button(result,"Download result CSV","sql_query_result.csv","sql_csv")
        b.download_button("Download this query",saved["sql"],file_name="playground_query.sql",mime="text/plain",key="query_download")
        c.download_button("Download result JSON",result.to_json(orient="records",indent=2),file_name="sql_result.json",mime="application/json",key="sql_json")
        numeric=list(result.select_dtypes(include="number").columns)
        if len(result)>0 and numeric and len(result.columns)>1:
            with st.expander("Chart this query result"):
                x=st.selectbox("X column",list(result.columns),key="sql_chart_x")
                y=st.selectbox("Numeric Y column",numeric,key="sql_chart_y")
                kind=st.selectbox("Result chart type",["Bar","Line","Scatter"],key="sql_chart_type")
                func={"Bar":px.bar,"Line":px.line,"Scatter":px.scatter}[kind]
                plotted=result.head(500)
                fig=func(plotted,x=x,y=y,color_discrete_sequence=["#4F46E5"])
                fig.update_layout(xaxis_title=x,yaxis_title=y)
                chart(fig,320)
                st.caption("Charts use the first 500 displayed result rows. Query GROUP BY or ORDER BY explicitly to control aggregation and order.")
    else:
        st.info("Choose a task and press Run SQL to execute it.")
    with st.expander("Recent queries in this session"):
        for item in st.session_state.get("query_history",[]):
            st.caption(f"{item['rows']} displayed rows · {item['elapsed_ms']} ms")
            st.code(item["query"],language="sql")


def render_downloads(conn,rows):
    section("Submission files")
    a,b,c=st.columns(3)
    deliverables=[(a,ROOT/"sql/Stock_Market_Analysis_MySQL.sql","MySQL analysis","text/plain"),
                  (b,ROOT/"sql/Stock_Market_Analysis_SQLite.sql","SQLite analysis","text/plain"),
                  (c,ROOT/"reports/Stock_Market_Insights.pdf","Insights PDF","application/pdf")]
    for col,path,label,mime in deliverables:
        if path.exists():
            col.download_button(label,path.read_bytes(),file_name=path.name,mime=mime,key=label)
        else:
            col.info(f"{label}: create this file with the included build scripts.")
    video=ROOT/"reports/Stock_Market_Walkthrough.mp4"
    transcript=ROOT/"reports/Video_Walkthrough_Script.txt"
    if video.exists():
        st.download_button("Walkthrough video",video.read_bytes(),file_name=video.name,mime="video/mp4",key="video_download")
    if transcript.exists():
        st.download_button("Walkthrough transcript",transcript.read_text(encoding="utf-8"),file_name=transcript.name,mime="text/plain",key="transcript_download")
    st.caption("The full insights PDF uses all six stocks over the complete supplied period. It does not change with filters.")
    section("Data quality","Original CSVs remain unchanged. Empty delivery fields stay NULL. No duplicate dates or invalid closing prices were found.")
    quality=[]
    for stock,table_name in STOCKS.items():
        d=dataframe(conn,f"SELECT COUNT(*) AS rows,COUNT(DISTINCT date) AS unique_dates,MIN(date) AS first_date,MAX(date) AS last_date,SUM(deliverable_qty IS NULL) AS missing_delivery FROM {table_name}").iloc[0].to_dict()
        quality.append({"stock":stock,**d})
    table(pd.DataFrame(quality))
    with st.expander("Missing delivery records"):
        table(dataframe(conn,task_queries()["04 | Missing delivery quantities"]))
    section("Inspect and export the selected source rows")
    search=st.text_input("Search stock name or date",placeholder="Example: TCS or 2018-05",key="data_search")
    display=rows
    if search.strip():
        term=search.strip().lower()
        display=rows[rows.stock.str.lower().str.contains(term,regex=False)|rows.date.str.contains(term,regex=False)]
    st.caption(f"{len(display):,} matching rows. Preview shows up to 300; the CSV contains every matching row.")
    table(display.head(300).round(4))
    a,b=st.columns(2)
    with a:
        csv_button(display,"Export all matching rows","selected_analytics_rows.csv","all_data_csv")
    b.download_button("Download original SQL database",database_bytes(dataset_fingerprint()),file_name="stock_market.db",mime="application/vnd.sqlite3",key="db_download")
    section("Method and sources")
    st.markdown("The SQL database calculates trailing 20/50 trading-day averages, crossover events, close-to-close price moves and drawdown. Windows use **trading observations**, not calendar days. Assignment Bajaj tables round the averages to two decimals; cross-stock and adjusted analytics keep full precision. Annual volatility uses the sample standard deviation of daily price moves multiplied by √252.")
    st.markdown("Prices stop at **31 July 2018**. The six source files and course materials supply the historical records. Dividend payments, benchmark returns, real brokerage and taxes are absent. Original prices and volume are preserved alongside adjusted OHLC.")
    for event in EVENTS:
        st.markdown(f"- [{event[0]} issuer documentation]({event[4]}): {event[1]} ex-bonus date, 1:1 bonus, adjustment factor 2.")
    st.caption(f"Dataset SHA-256: {dataset_fingerprint()}")


def main():
    st.session_state.setdefault("stocks",list(STOCKS))
    if "pending_sql_reset" in st.session_state:
        st.session_state["sql_editor"]=st.session_state.pop("pending_sql_reset")
    with st.sidebar:
        st.markdown("### Stock Market Analysis")
        st.markdown('<div class="mini">SQL analysis & interactive exploration<br>Six Indian stocks · historical dataset</div>',unsafe_allow_html=True)
        st.write("")
        page=st.radio("Workspace",PAGES,label_visibility="collapsed",key="page")
        st.divider()
        st.markdown("#### Filter the view")
        basis=st.selectbox("Price basis",["Adjusted","Raw"],key="price_basis",help="Adjusted corrects the two identified 1:1 bonus issues. Raw matches the assignment's original-price tasks.")
        stocks=st.multiselect("Stocks",list(STOCKS),key="stocks")
        period=st.date_input("Date range",value=(MIN_DATE,MAX_DATE),min_value=MIN_DATE,max_value=MAX_DATE,key="date_range",format="DD/MM/YYYY")
        st.button("Reset filters",on_click=reset_filters,width="stretch",key="reset_filters")
        st.divider()
        st.caption("Data ends 31 July 2018. Buy/Sell labels are historical crossover events.")
        st.caption("Prepared by Srija Chatterjee")
    st.markdown('<div class="hero"><div class="pill">01 JAN 2015 — 31 JUL 2018</div><h1>Stock Market Analysis</h1><p>Explore price trends, understand crossover signals, and run your own SQL against the supplied data.</p></div>',unsafe_allow_html=True)
    if not stocks:
        st.info("Select at least one stock in the sidebar to show the analysis.")
        return
    if not isinstance(period,(tuple,list)) or len(period)!=2:
        st.info("Choose both a start date and an end date.")
        return
    start,end=period
    if start>end:
        st.error("The start date must be on or before the end date.")
        return
    conn=new_connection()
    try:
        rows=filtered_rows(conn,basis,stocks,start,end)
        summary=filtered_summary(conn,basis,stocks,start,end)
        set_selected_view(conn,basis,stocks,start,end)
        st.session_state["scope_token"]=(basis,tuple(stocks),str(start),str(end))
        st.caption(f"{page}  ·  {basis} prices  ·  {start.strftime('%d %b %Y')} to {end.strftime('%d %b %Y')}  ·  {len(rows):,} stock-day rows")
        if rows.empty and page not in ["Corporate actions","SQL playground","Data & downloads"]:
            st.info("No trading rows fall in this date range. Try a weekday or extend the range.")
            return
        if page=="Overview":
            render_overview(conn,rows,summary,basis,stocks,start,end)
        elif page=="Stock explorer":
            render_explorer(conn,rows,summary,basis,stocks,start,end)
        elif page=="Signals & comparisons":
            render_signals(conn,rows,summary,basis,stocks,start,end)
        elif page=="Corporate actions":
            render_actions(conn,stocks)
        elif page=="SQL playground":
            render_sql(conn)
        else:
            render_downloads(conn,rows)
    finally:
        conn.close()


if __name__=="__main__":
    main()
