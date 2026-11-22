import urllib.request, re, json
UA={"User-Agent":"Mozilla/5.0"}
def get(u):
    try: return urllib.request.urlopen(urllib.request.Request(u,headers=UA),timeout=40).read().decode("utf-8","replace")
    except Exception as e: return "ERR "+str(e)
B="https://www.twse.com.tw"
js=get(B+"/dashboard/js/credit-margin.js")
for m in re.finditer(r'.{0,300}(BFIJ3U|MI_MARGN_HISTORY|MI_MARGN_TREND|marginHistory|tpex-api).{0,300}',js): print("JS>",m.group(0)[:600])
m=get(B+"/dashboard/js/main.js")
for x in re.finditer(r'.{0,200}(tpex-api|"/rwd").{0,200}',m): print("MAIN>",x.group(0)[:400])
for u in ["/rwd/zh/marginTrading/BFIJ3U?response=json","/rwd/zh/marginTrading/BFIJ3U?date=20200319&response=json","/rwd/zh/marginTrading/BFIJ3U?date=20081027&response=json",
          "/rwd/zh/marginTrading/BFIJ3U_TREND?response=json","/rwd/zh/marginTrading/MI_MARGN_HISTORY?response=json","/rwd/zh/marginTrading/MI_MARGN_TREND?response=json",
          "/rwd/zh/marginTrading/BFIJ3U_TREND?startDate=20010101&endDate=20261121&response=json","/rwd/zh/marginTrading/MI_MARGN_HISTORY?startDate=20010101&endDate=20261121&response=json"]:
    t=get(B+u); print("=====",u,len(t)); print(re.sub(r"\s+"," ",t[:1500]))
for u in ["https://www.tpex.org.tw/tpex-api/dashboardOtc/marginHistory?lang=zh-tw","https://www.twse.com.tw/tpex-api/dashboardOtc/marginHistory?lang=zh-tw","https://www.tpex.org.tw/www/zh-tw/dashboardOtc/marginHistory?lang=zh-tw"]:
    t=get(u); print("=====",u,len(t)); print(re.sub(r"\s+"," ",t[:1200]))
