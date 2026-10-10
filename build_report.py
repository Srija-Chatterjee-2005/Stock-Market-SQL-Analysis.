"""Create the source-backed insights PDF and reproducible chart assets."""
from pathlib import Path
import html
import json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from reportlab.platypus import (SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,
                               Image,PageBreak,KeepTogether)
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet,ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from engine import ROOT,STOCKS,EVENTS,build_connection,dataframe,filtered_rows,task_queries,dataset_fingerprint
from simulator import simulate

NAVY='#142240'; PURPLE='#4F46E5'; TEAL='#0D9488'; MUTED='#667891'; RED='#E05260'
PALETTE={'Bajaj Auto':PURPLE,'Eicher Motors':TEAL,'Hero Motocorp':'#D88F09','Infosys':'#8B5CF6','TCS':'#2563EB','TVS Motors':RED}
OUT=ROOT/'reports'; ASSETS=OUT/'charts'
ASSETS.mkdir(parents=True,exist_ok=True)

font_dir=ROOT/'assets/fonts'
pdfmetrics.registerFont(TTFont('DejaVu',str(font_dir/'DejaVuSans.ttf')))
pdfmetrics.registerFont(TTFont('DejaVuBold',str(font_dir/'DejaVuSans-Bold.ttf')))
pdfmetrics.registerFontFamily('DejaVu',normal='DejaVu',bold='DejaVuBold')
styles=getSampleStyleSheet()
styles.add(ParagraphStyle('BodyCustom',fontName='DejaVu',fontSize=9.3,leading=14,textColor=colors.HexColor(NAVY),spaceAfter=9))
styles.add(ParagraphStyle('SmallCustom',fontName='DejaVu',fontSize=7.8,leading=11.5,textColor=colors.HexColor(MUTED),spaceAfter=6))
styles.add(ParagraphStyle('H1Custom',fontName='DejaVuBold',fontSize=22,leading=27,textColor=colors.HexColor(NAVY),spaceAfter=14))
styles.add(ParagraphStyle('H2Custom',fontName='DejaVuBold',fontSize=12.5,leading=18,textColor=colors.HexColor(NAVY),spaceBefore=8,spaceAfter=8))
styles.add(ParagraphStyle('TableCustom',fontName='DejaVu',fontSize=7.6,leading=10,textColor=colors.HexColor(NAVY)))
styles.add(ParagraphStyle('TableHead',fontName='DejaVuBold',fontSize=7.6,leading=10,textColor=colors.white))

def p(text,style='BodyCustom'):
    return Paragraph(text,styles[style])

def heading(text):
    return p(text,'H1Custom')

def sub(text):
    return p(text,'H2Custom')

def table(headers,rows,widths=None):
    data=[[p(str(x),'TableHead') for x in headers]]+[[p(html.escape(str(x)),'TableCustom') for x in row] for row in rows]
    t=Table(data,colWidths=widths or [499/len(headers)]*len(headers),repeatRows=1,hAlign='LEFT')
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor(NAVY)),
                          ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#F3F5FC')]),
                          ('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),8),
                          ('RIGHTPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),7),
                          ('BOTTOMPADDING',(0,0),(-1,-1),7),
                          ('LINEBELOW',(0,0),(-1,0),.4,colors.HexColor('#DCE2EE'))]))
    return t

def img(path,width=499,height=235):
    return Image(str(path),width=width,height=height)

def caption(text):
    return p(text,'SmallCustom')

def fmt(x):
    return f'{x:+.2f}%'

def footer(canvas,doc):
    canvas.saveState()
    w,h=A4
    canvas.setStrokeColor(colors.HexColor('#DFE5F0'))
    canvas.line(48,h-42,w-48,h-42)
    canvas.setFont('DejaVuBold',7.2);canvas.setFillColor(colors.HexColor(MUTED))
    canvas.drawString(48,h-32,'STOCK MARKET ANALYSIS')
    canvas.setFont('DejaVu',7)
    canvas.drawString(48,26,'Srija Chatterjee  |  10 October 2026  |  Historical data through 31 July 2018')
    canvas.drawRightString(w-48,26,str(doc.page))
    canvas.restoreState()

def style_axes(ax):
    ax.spines[['top','right']].set_visible(False)
    ax.spines[['bottom','left']].set_color('#DCE3EE')
    ax.grid(axis='y',color='#E7ECF4',linewidth=.7)
    ax.set_axisbelow(True)
    ax.tick_params(colors=MUTED,labelsize=9)

def charts(conn):
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':9,'axes.labelcolor':MUTED,'figure.facecolor':'white'})
    d=dataframe(conn,"SELECT stock,date,100.0*close_price/FIRST_VALUE(close_price) OVER (PARTITION BY stock ORDER BY date) AS idx FROM v_analytics WHERE basis='Adjusted' ORDER BY stock,date")
    fig,ax=plt.subplots(figsize=(9,4.5))
    for stock,group in d.groupby('stock'):
        ax.plot(pd.to_datetime(group.date),group.idx,label=stock,color=PALETTE[stock],linewidth=1.6)
    ax.axhline(100,color='#A7B4C9',linestyle='--',linewidth=.8)
    ax.set_ylabel('Indexed adjusted close (first day = 100)')
    ax.xaxis.set_major_locator(mdates.YearLocator());ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
    ax.legend(loc='upper left',ncol=3,fontsize=8,frameon=False)
    style_axes(ax);fig.tight_layout();fig.savefig(ASSETS/'adjusted_performance.png',dpi=200);plt.close(fig)
    s=dataframe(conn,"SELECT stock,basis,price_change_pct FROM v_stock_summary ORDER BY stock,basis").pivot(index='stock',columns='basis',values='price_change_pct')
    fig,ax=plt.subplots(figsize=(9,3.9));y=np.arange(len(s));height=.34
    ax.barh(y+height/2,s.Raw,height,label='Raw',color='#A8B6CE')
    ax.barh(y-height/2,s.Adjusted,height,label='Bonus adjusted',color=PURPLE)
    for i,(_,row) in enumerate(s.iterrows()):
        for offset,val in [(height/2,row.Raw),(-height/2,row.Adjusted)]:
            ax.text(val+(1.5 if val>=0 else -1.5),i+offset,f'{val:+.1f}%',va='center',ha='left' if val>=0 else 'right',fontsize=8)
    ax.set_yticks(y,s.index);ax.set_xlim(-45,105);ax.axvline(0,color='#ADB9CB',linewidth=.8)
    ax.set_xlabel('Full-period price change (%)');ax.legend(loc='lower right',frameon=False,fontsize=8)
    style_axes(ax);fig.tight_layout();fig.savefig(ASSETS/'raw_adjusted_returns.png',dpi=200);plt.close(fig)
    fig,axs=plt.subplots(1,2,figsize=(9,3.3))
    for ax,stock,event in zip(axs,['Infosys','TCS'],['2015-06-15','2018-05-31']):
        d=dataframe(conn,"SELECT date,basis,close_price FROM v_analytics WHERE stock=? AND date BETWEEN date(?,'-35 day') AND date(?,'+35 day') ORDER BY basis,date",(stock,event,event))
        for basis,g in d.groupby('basis'):
            ax.plot(pd.to_datetime(g.date),g.close_price,color=PURPLE if basis=='Adjusted' else '#A8B6CE',label=basis,linewidth=1.6)
        ax.axvline(pd.Timestamp(event),color=RED,linestyle=':',linewidth=1)
        ax.set_title(stock+' | '+event,color=NAVY,fontweight='bold',fontsize=10)
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%d %b'));ax.xaxis.set_major_locator(mdates.MonthLocator())
        ax.set_ylabel('Close (INR)');style_axes(ax)
    axs[0].legend(frameon=False,fontsize=8);fig.tight_layout();fig.savefig(ASSETS/'bonus_events.png',dpi=200);plt.close(fig)

def main():
    conn=build_connection();charts(conn)
    summaries=dataframe(conn,'SELECT * FROM v_stock_summary ORDER BY basis,stock')
    raw=summaries[summaries.basis.eq('Raw')].set_index('stock')
    adj=summaries[summaries.basis.eq('Adjusted')].set_index('stock')
    rapid=dataframe(conn,"SELECT stock,COUNT(*) AS quick_reversals FROM v_crossovers WHERE basis='Adjusted' AND gap_calendar_days<=30 GROUP BY stock ORDER BY stock").set_index('stock').quick_reversals.to_dict()
    scenario=[]
    for stock in STOCKS:
        d=filtered_rows(conn,'Adjusted',[stock],'2015-01-01','2018-07-31')
        gross=simulate(d,fee_bps=0);net=simulate(d,fee_bps=10)
        scenario.append({'stock':stock,'gross_return_pct':gross['return_pct'],'net_return_pct':net['return_pct'],'fees_inr':net['fees_inr'],'executions':net['executions'],'drag_pp':gross['return_pct']-net['return_pct']})
    pd.DataFrame(scenario).to_csv(OUT/'query_results/execution_scenario.csv',index=False)
    story=[]
    # 1. Findings first.
    story += [Spacer(1,25),caption('SQL PROJECT / INSIGHTS REPORT'),heading('Stock Market Analysis'),
              p('Moving averages, crossover signals and the effect of bonus-share adjustments.'),
              Spacer(1,12),table(['Period','Stocks','Trading days each','Source rows'],[['01 Jan 2015 - 31 Jul 2018','6','889','5,334']],[185,60,114,140]),
              Spacer(1,20),sub('What the analysis found'),
              p('TVS Motors records the largest full-period price increase, <b>86.91%</b>, followed by Eicher Motors at <b>82.56%</b>. Every stock has a positive first-to-last price change after the two identified bonus issues are handled.'),
              p('TCS and Infosys illustrate why raw share prices can mislead. Their apparent declines become gains of <b>52.36%</b> and <b>38.17%</b> after historical prices are put on the same share basis.'),
              p('The unadjusted SQL reproduces the assignment total of <b>56 Buy</b> and <b>57 Sell</b> crossovers. Recomputing on adjusted prices removes the TCS Sell event on 5 June 2018 and changes four Infosys event dates.'),
              p('A positive long-run price change does not guarantee a profitable crossover strategy. The execution scenario on page 8 produces negative results for some stocks even though their full-period prices increased.'),
              sub('How to read this report'),
              p('Raw results answer the course checkpoints. Adjusted results support comparisons across the bonus dates. Each section identifies the SQL task or view behind its numbers and states the limitations that affect interpretation.'),
              caption('Evidence: tasks 10-13, v_stock_summary and v_signal_changes. Price change excludes dividends, brokerage and taxes. Signals describe this historical sample and do not forecast future returns.'),
              Spacer(1,12),p('<b>Prepared by Srija Chatterjee</b><br/>Submission date: 10 October 2026'),PageBreak()]
    # 2. Method and source quality.
    gaps=dataframe(conn,task_queries()['04 | Missing delivery quantities'])
    story += [heading('The data and the method'),p('The six supplied CSVs contain one row per stock per observed trading date. Dates arrive newest first as text such as 31-July-2018. The loader converts them to ISO dates and sorts them chronologically before any window calculations.'),
              table(['Check','Verified result'],[['Coverage','889 rows per stock; 2015-01-01 to 2018-07-31'],['Trading calendar','The six date sets match exactly; the master join keeps 889 dates'],['Closing prices','No missing or non-positive closes; no duplicate stock/date keys'],['Delivery fields','One missing delivery quantity and delivery percentage per stock']],[139,360]),
              sub('Missing values remain missing'),table(['Table','Missing delivery date'],gaps.values.tolist(),[240,259]),
              caption('Evidence: tasks 1, 4 and 6 plus source validation. The shared dates suggest a reporting/source gap, but the files do not establish its cause. These missing fields do not affect close-price moving averages.'),
              sub('A crossing needs two complete windows'),
              p('<b>MA20</b> averages the current close and previous 19 trading observations. <b>MA50</b> uses the current close and previous 49. Values stay NULL until 20 or 50 observations exist. A Buy requires MA20 &gt; MA50 today and MA20 &lt;= MA50 on the previous trading row. A Sell uses the opposite inequalities. Every other day is Hold.'),
              p('Bajaj assignment tables round the averages to two decimals as requested. The six-stock and adjusted calculations keep unrounded values. All window functions partition by stock and price basis. Date filters apply after the full-history averages are calculated.'),
              caption('Evidence: tasks 5, 7 and 10. The first non-NULL Bajaj MA20 is 2,415.53 on 29 January 2015. MA50 first appears at 2,283.80 on 13 March 2015. A moving average describes past prices, so its crossing inherently arrives after earlier price movements.'),PageBreak()]
    # 3. Corrected performance.
    story += [heading('Performance on a consistent share basis'),img(ASSETS/'adjusted_performance.png',height=250),
              caption('Adjusted close rebased to 100 on 1 January 2015. Evidence: v_analytics and FIRST_VALUE by stock. The chart shows price history, not a dividend-reinvested portfolio.'),
              table(['Stock','First adjusted close (INR)','Last close (INR)','Price change'],[[stock,f'{r.first_close:,.2f}',f'{r.last_close:,.2f}',fmt(r.price_change_pct)] for stock,r in adj.sort_values('price_change_pct',ascending=False).iterrows()],[135,143,123,98]),
              sub('Growth came with declines along the way'),
              p('The two strongest endpoint performers did not move smoothly upward. TVS Motors has a maximum observed drawdown of <b>34.59%</b>, and Eicher Motors <b>28.32%</b>. Their ending gains therefore conceal substantial intermediate declines.'),
              p('The endpoint formula is last close / first close - 1. It is sensitive to the chosen dates and says nothing about the timing of an investor\'s actual entries or exits. The six stocks are a supplied sample, not a representative market portfolio.'),
              caption('Evidence: tasks 11 and 13, v_stock_summary. Drawdown compares each adjusted close with the highest adjusted close observed up to that date. No broader-market benchmark or dividend series is supplied.'),PageBreak()]
    # 4. Original tasks remain reproducible.
    story += [heading('The assignment results, reproduced'),sub('Original-price crossover summary'),
              table(['Stock','Buy','Sell','Latest crossing date','Latest event'],[[stock,int(r.buys),int(r.sells),r.last_signal_date,r.last_signal] for stock,r in raw.iterrows()],[141,44,44,168,102]),
              caption('Evidence: task 10, using full-precision raw moving averages. Total: 56 Buy and 57 Sell events. On 31 July 2018 all six daily event values are Hold, which is different from the latest non-Hold crossover.'),
              sub('Eicher\'s five highest closes'),table(['Date','Close (INR)'],[[r.date,f'{r.close_price:,.2f}'] for _,r in dataframe(conn,task_queries()["02 | Eicher's five highest closes"]).iterrows()],[249,250]),
              p('All five occur in <b>September 2017</b>. The maximum is INR 32,786.40 on 7 September. That concentration describes this historical sample and does not establish why the peak occurred.'),
              sub('TCS annual averages are not directly comparable'),table(['Calendar year','Raw average close (INR)'],[[r.year,f'{r.avg_close:,.2f}'] for _,r in dataframe(conn,task_queries()['03 | TCS annual average']).iterrows()],[249,250]),
              caption('Evidence: tasks 2 and 3. 2018 ends in July and mixes pre/post-bonus raw price levels. Its average should not be called the best year. The 2016 checkpoint is exactly INR 2,419.00.'),PageBreak()]
    # 5. Corporate actions.
    story += [heading('The two bonus-share events'),
              table(['Stock','Ex-bonus trading date','Issue','Price treatment'],[['Infosys','15 Jun 2015','1:1 bonus','Divide OHLC before date by 2'],['TCS','31 May 2018','1:1 bonus','Divide OHLC before date by 2']],[88,130,79,202]),
              p('A 1:1 bonus doubles the number of shares. Comparing the unadjusted share price across that change creates an artificial drop. The event day and later prices remain unchanged. Only these identified events are adjusted, and reported volume is kept as supplied.'),
              img(ASSETS/'bonus_events.png',height=185),
              caption('Evidence: task 12 finds raw one-day moves of -49.86% for Infosys and -50.40% for TCS. On the adjusted basis the same dates move +0.28% and -0.80%. The ex-bonus dates are independently confirmed in the issuer documents linked on page 10.'),
              table(['Stock','Raw price change','Adjusted change','Difference (pp)'],[[s,fmt(raw.loc[s].price_change_pct),fmt(adj.loc[s].price_change_pct),f'{adj.loc[s].price_change_pct-raw.loc[s].price_change_pct:+.2f}'] for s in ['Infosys','TCS']],[97,127,141,134]),
              sub('The signals must be recomputed too'),
              table(['Stock / date','Raw event','Adjusted event'],[[f'{r.stock} / {r.date}',r.raw_signal,r.adjusted_signal] for _,r in dataframe(conn,'SELECT * FROM v_signal_changes ORDER BY stock,date').iterrows()],[280,108,111]),
              caption('Evidence: tasks 13-15. TCS changes from 12 Buy / 13 Sell to 12 / 12; its latest adjusted crossover is Buy on 20 April 2018. Infosys changes from 9 / 9 to 10 / 10. Four Infosys dates change because the pre-bonus prices influence the later 20/50-day windows.'),PageBreak()]
    # 6-7. A clear conclusion for every stock.
    notes={
      'Bajaj Auto':'The modest endpoint gain comes with 11 quick reversals. Its December 2015 cluster changes from Sell to Buy to Sell to Buy within 17 calendar days. A recent bullish crossing can coexist with a weak cumulative strategy result.',
      'Eicher Motors':'The stock has strong full-period growth and only one quick reversal. Its latest bearish crossing describes the more recent moving-average relationship. It does not contradict the much longer endpoint gain.',
      'Hero Motocorp':'The small endpoint gain and four quick reversals leave limited room for costs in a mechanical trading rule. The latest bearish crossing agrees with the final MA20-below-MA50 regime, while the multi-year endpoint remains slightly positive.',
      'Infosys':'Bonus adjustment changes the stock from an apparent loser to a positive endpoint performer. Three crossings emerge in July 2015 and the raw Buy on 18 August 2015 disappears. Its latest bullish crossing also agrees with the final MA regime.',
      'TCS':'The raw Sell on 5 June 2018 disappears after the bonus adjustment. The latest corrected crossing is Buy on 20 April 2018, and the final MA20 remains above MA50. The correction therefore changes both the long-run direction and the latest-event interpretation.',
      'TVS Motors':'The biggest endpoint gain comes with the highest adjusted annual volatility and deepest drawdown in this sample. Four quick reversals and a final bearish MA regime show why the endpoint winner is not automatically a low-risk choice.'}
    for group in [['Bajaj Auto','Eicher Motors','Hero Motocorp'],['Infosys','TCS','TVS Motors']]:
        story += [heading('What each stock tells us')]
        for stock in group:
            r=adj.loc[stock]
            story += [sub(stock),table(['Adjusted change','Buy / Sell','Latest adjusted crossover'],[[fmt(r.price_change_pct),f'{int(r.buys)} / {int(r.sells)}',f'{r.last_signal} on {r.last_signal_date}']],[127,103,269]),
                      p(notes[stock]),
                      caption(f"Evidence: v_stock_summary, task 14 and task 16. Quick reversals within 30 calendar days: {rapid.get(stock,0)}. Annualized sample volatility: {r.annual_volatility_pct:.2f}%. Maximum observed drawdown: {r.max_drawdown_pct:.2f}%."),
                      caption('Caveat: a latest crossing and a multi-year endpoint measure different horizons. Neither alone establishes future direction. Volatility uses daily close changes and a 252-trading-day annualization assumption.'),Spacer(1,10)]
        story += [p('For a multi-year comparison, use the adjusted price history. For a description of recent momentum, inspect the latest adjusted crossover and MA regime. Disagreement between the two is a reason to examine the time horizon, rather than to treat one as a prediction.'),PageBreak()]
    # 8. Costs and timing.
    story += [heading('Frequent signals have an execution cost'),
              table(['Stock','Quick reversals','Scenario executions','Fees paid (INR)'],[[s,rapid.get(s,0),next(x['executions'] for x in scenario if x['stock']==s),f"{next(x['fees_inr'] for x in scenario if x['stock']==s):,.2f}"] for s in STOCKS],[137,110,127,125]),
              caption('Evidence: task 16 for reversal counts; execution_scenario.csv for the modeled fees. A reversal is an opposite event no more than 30 calendar days after the previous event. Overlapping transitions are counted individually.'),
              sub('An explicit scenario, not observed brokerage'),
              p('Each stock starts with INR 100,000 in cash. Buy invests the available cash at the next observed trading-day open. Sell exits at the next open. The scenario allows fractional units, ignores Sell events when already in cash, and liquidates any remaining position at the final close. Signals are never filled at the same close that generated them.'),
              p('The fee assumption is <b>10 basis points (0.10%) of traded value per execution</b>, with zero slippage. Actual brokerage, taxes and dividends are not supplied. Fees and account outcomes below are modeled in Python from the SQL-generated adjusted signals and OHLC.'),
              table(['Stock','Zero-cost return','Return with fees','Cost drag (pp)'],[[x['stock'],fmt(x['gross_return_pct']),fmt(x['net_return_pct']),f"{x['drag_pp']:.2f}"] for x in scenario],[137,130,129,103]),
              sub('What this example establishes'),
              p('Bajaj Auto\'s strategy changes from +1.32% before fees to -1.09% with the assumed fee. TCS has +52.36% adjusted endpoint growth but a -26.55% strategy result under these execution rules. Entry timing and periods spent in cash materially change the outcome.'),
              caption('Caveat: these rules are an illustrative extension of the assignment. No parameter tuning, dividend reinvestment, liquidity restriction or benchmark comparison is included. A final close liquidation is an explicit terminal convention. This is not evidence that the same rule will perform similarly in another period.'),PageBreak()]
    # 9. Deck corrections and practical conclusion.
    story += [heading('Which course conclusions change?'),
              table(['Course slide statement','Verified result / correction'],[
                  ['Infosys: 3% decrease','Raw change is -30.91%; adjusted change is +38.17%.'],
                  ['TCS endpoints quoted as 2,548.20 and 2,454.10','Actual first close is 2,548.20; final close is 1,941.25. Raw -23.82% is the correctly computed percentage.'],
                  ['TCS latest direction down on 5 Jun 2018','That raw Sell is absent on adjusted prices. Latest adjusted event: Buy on 20 Apr 2018.'],
                  ['Eicher: 82.5% increase','Computed raw/adjusted change is 82.5623%, which rounds to 82.6% at one decimal.'],
                  ['Buy/sell recommendations based on latest events','A crossing is historical and lagging. The files do not support a current investment recommendation.']],[181,318]),
              caption('Evidence: source course slides compared with tasks 10-15. The original unadjusted event counts are reproducible; correcting the price basis changes the interpretation for TCS and Infosys.'),
              sub('What is still missing'),
              p('A real investment assessment would need dividends and other corporate actions, a comparable market benchmark, news and fundamentals, actual transaction costs and taxes, and a review of data provenance. Volume is reported by the source and is not restated for share-count changes.'),
              p('The analysis has no data after July 2018. It does not evaluate current prices or events. It uses six selected companies, so findings should not be generalized to the whole market. The 2018 annual group covers only seven months.'),
              sub('The conclusion'),
              p('The main result is that a reproducible SQL answer still needs a trustworthy price basis. Moving averages calculated from unadjusted corporate-action jumps can create false signals and misleading performance comparisons. Preserving the raw results and showing the adjustment openly makes the final analysis easier to verify.'),
              p('TVS Motors and Eicher Motors lead the corrected endpoint comparison. TCS and Infosys also show positive adjusted growth. The signal and cost analysis adds a separate lesson: endpoint growth, recent momentum and a tradable execution result measure different things.'),
              caption('Evidence: tasks 10-17 and the stated execution scenario. No future-return claims are made.'),PageBreak()]
    # 10. Exact verification map and sources.
    checkpoints=[
        ['1','Coverage','889 rows; 2015-01-01 to 2018-07-31'],
        ['2','Eicher top closes','5 rows; all Sep 2017; highest INR 32,786.40'],
        ['3','TCS annual mean','2016 = INR 2,419.00; 4 annual groups'],
        ['4','NULL delivery quantity','6 rows on 2 distinct dates'],
        ['5','Bajaj moving averages','MA20 starts 29 Jan; MA50 starts 13 Mar; 889 rows'],
        ['6','Master table','889 rows, 7 columns, complete aligned closes'],
        ['7','Bajaj crossover events','First Buy 18 May 2015; first Sell 24 Aug 2015'],
        ['8','Bajaj counts','12 Buy, 866 Hold, 11 Sell'],
        ['9','Signal lookup','2018-06-21 = Buy; no trading date = NULL/no row'],
        ['10','Six-stock raw summary','56 Buy and 57 Sell in total'],
        ['11','Raw endpoint change','TVS 86.9%; TCS and Infosys appear negative'],
        ['12','Worst raw days','Infosys 15 Jun 2015; TCS 31 May 2018'],
        ['13','Adjusted IT change','Infosys 38.2%; TCS 52.4% (one decimal)']]
    story += [heading('Queries, checks and sources'),table(['Task','Check','Verified output'],checkpoints,[39,140,320]),
              caption('The SQL file includes all 13 tasks, their setup tables and additional views. The SQLite script is self-contained and repeatable. The MySQL script embeds the same input records and includes the bajaj_signal date function. Automated tests compare full SQL series with independent pandas rolling calculations.'),
              sub('Source material'),
              caption('Supplied files: Bajaj Auto.csv, Eicher Motors.csv, Hero Motocorp.csv, Infosys.csv, TCS.csv and TVS Motors.csv. Assignment instructions: SQL_Stock_Market_Analysis_Student_Guide.pdf and SQL- Stock Market Analysis.pptx. Dataset hash: '+dataset_fingerprint()),
              caption('<b>Infosys:</b> Additional information for quarter ended 30 June 2015, PDF page 5. Confirms 1:1 bonus and 15 June ex-bonus pricing. <link href="'+EVENTS[0][4]+'" color="'+PURPLE+'">Open issuer document</link>.'),
              caption('<b>TCS:</b> Annual Report 2018-19, Corporate Governance, printed page 89 (PDF page 92). Confirms trading ex-bonus from 31 May 2018 and adjustment for the 1:1 bonus. <link href="'+EVENTS[1][4]+'" color="'+PURPLE+'">Open issuer document</link>.'),
              caption('Technical references: <link href="https://www.sqlite.org/windowfunctions.html" color="'+PURPLE+'">SQLite window functions</link>; <link href="https://dev.mysql.com/doc/refman/8.0/en/window-functions.html" color="'+PURPLE+'">MySQL 8 window functions</link>; <link href="https://docs.streamlit.io/develop/api-reference/app-testing" color="'+PURPLE+'">Streamlit app testing</link>.')]
    pdf=OUT/'Stock_Market_Insights.pdf'
    doc=SimpleDocTemplate(str(pdf),pagesize=A4,leftMargin=48,rightMargin=48,topMargin=60,bottomMargin=48,title='Stock Market Analysis - Insights',author='Srija Chatterjee')
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    conn.close()
    print(pdf)

if __name__=='__main__':
    main()
