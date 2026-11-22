import urllib.request, json, os, re
UA={"User-Agent":"Mozilla/5.0"}
TOK=os.environ.get("FINMIND_TOKEN","")
def get(u):
    try:
        r=urllib.request.urlopen(urllib.request.Request(u,headers=UA),timeout=40); return r.status,r.read()
    except Exception as e: return "ERR",str(e).encode()
def show(u,n=900):
    st,b=get(u); t=b.decode("utf-8","replace"); print("=====",u[:150],st,len(b)); print(re.sub(r"\s+"," ",t[:n])); return t
for d in ["20010102","20080915","20200319"]:
    t=show(f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={d}&selectType=MS&response=json",1500)
show("https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date=20200319&selectType=ALL&response=json",1200)
show("https://www.tpex.org.tw/web/stock/margin_trading/margin_balance/margin_bal_result.php?l=zh-tw&o=json&d=097/09/15",1200)
show("https://www.tpex.org.tw/web/stock/margin_trading/margin_balance/margin_bal_result.php?l=zh-tw&o=json&d=090/01/02",600)
fm="https://api.finmindtrade.com/api/v4/data?"
for ds,extra in [("TaiwanStockTotalMarginPurchaseShortSale","start_date=2001-01-01&end_date=2001-01-10"),
                 ("TaiwanTotalExchangeMarginMaintenance","start_date=2001-01-01&end_date=2026-11-21"),
                 ("TaiwanStockTotalMarginPurchaseShortSale","start_date=2026-11-10&end_date=2026-11-21"),
                 ("TaiwanStockPER","data_id=2330&start_date=2001-01-01&end_date=2001-02-01"),
                 ("TaiwanStockFinancialStatements","data_id=2330&start_date=2001-01-01&end_date=2002-12-31"),
                 ("TaiwanStockMonthRevenue","data_id=2330&start_date=2002-01-01&end_date=2002-06-01"),
                 ("TaiwanStockInstitutionalInvestorsBuySell","data_id=2330&start_date=2005-01-01&end_date=2005-01-10"),
                 ("TaiwanStockTotalInstitutionalInvestors","start_date=2005-01-01&end_date=2005-01-10"),
                 ("TaiwanStockPrice","data_id=TAIEX&start_date=2001-01-01&end_date=2001-01-10"),
                 ("TaiwanStockInfo","")]:
    u=fm+f"dataset={ds}&{extra}"+(f"&token={TOK}" if TOK else "")
    st,b=get(u); t=b.decode("utf-8","replace")
    try:
        j=json.loads(t); dd=j.get("data") or []
        print("=====FM",ds,extra,st,j.get("msg"),"rows",len(dd)); print(dd[:2]); print(dd[-2:])
        if ds=="TaiwanTotalExchangeMarginMaintenance" and dd:
            print("first",dd[0],"min",min(dd,key=lambda x:list(x.values())[-1]))
        if ds=="TaiwanStockInfo": print([x for x in dd if x.get("stock_id")=="2330"])
    except Exception as e: print("=====FM",ds,st,t[:300])
show("https://query1.finance.yahoo.com/v8/finance/chart/2330.TW?period1=978307200&period2=1010000000&interval=1d",400)
show("https://query1.finance.yahoo.com/v8/finance/chart/%5ETWII?period1=978307200&period2=1010000000&interval=1d",300)
# twse 融資維持率 page candidates
for u in ["https://www.twse.com.tw/rwd/zh/marginTrading/TWT93U?date=20201120&response=json",
          "https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN"]:
    show(u,500)
