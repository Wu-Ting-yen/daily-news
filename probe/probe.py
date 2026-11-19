import urllib.request, json, time, sys
UA={"User-Agent":"Mozilla/5.0"}
def get(u):
    try:
        r=urllib.request.urlopen(urllib.request.Request(u,headers=UA),timeout=30); b=r.read()
        return r.status,b
    except Exception as e: return str(e),b""
urls={
 "bwibbu":"https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL",
 "rev":"https://openapi.twse.com.tw/v1/opendata/t187ap05_L",
 "basic":"https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
 "margin":"https://openapi.twse.com.tw/v1/opendata/t187ap17_L",
 "eps":"https://openapi.twse.com.tw/v1/opendata/t187ap14_L",
 "dayall":"https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
 "t86":"https://www.twse.com.tw/rwd/zh/fund/T86?date=20261118&selectType=ALLBUT0999&response=json",
 "yahoo":"https://query1.finance.yahoo.com/v8/finance/chart/2330.TW?range=6mo&interval=1d",
 "yahoo_idx":"https://query1.finance.yahoo.com/v8/finance/chart/%5ETWII?range=6mo&interval=1d",
 "fm_fin":"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockFinancialStatements&data_id=2330&start_date=2024-01-01",
 "fm_per":"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPER&data_id=2330&start_date=2021-11-01",
 "fm_rev":"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockMonthRevenue&data_id=2330&start_date=2024-01-01",
 "fm_price":"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPrice&data_id=2330&start_date=2026-08-01",
 "fm_inst":"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInstitutionalInvestorsBuySell&data_id=2330&start_date=2026-11-01",
 "mi_index":"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=20261118&type=ALLBUT0999&response=json",
}
for k,u in urls.items():
    s,b=get(u)
    t=b.decode("utf-8","replace")
    print("=====",k,s,len(b))
    try:
        j=json.loads(t)
        if isinstance(j,list): print("LIST len",len(j)); print(json.dumps(j[:2],ensure_ascii=False)[:1500])
        elif "data" in j and isinstance(j["data"],list): print("keys",list(j.keys())[:15],"n",len(j["data"])); print(json.dumps(j.get("fields"),ensure_ascii=False)[:800]); print(json.dumps(j["data"][:3],ensure_ascii=False)[:1500]); print(json.dumps(j["data"][-3:],ensure_ascii=False)[:1200])
        else: print(t[:1800])
    except Exception: print(t[:800])
    time.sleep(2)
