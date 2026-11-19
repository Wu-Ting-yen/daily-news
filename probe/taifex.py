import urllib.request, urllib.parse, json
UA={"User-Agent":"Mozilla/5.0"}
def get(u,data=None):
    try:
        r=urllib.request.urlopen(urllib.request.Request(u,data=data,headers=UA),timeout=40);return r.status,r.read()
    except Exception as e: return str(e),b""
s,b=get("https://openapi.taifex.com.tw/swagger.json")
print("swagger",s,len(b))
try:
    j=json.loads(b)
    for p,v in j.get("paths",{}).items():
        print(p, (v.get("get") or {}).get("summary",""))
except Exception as e: print(b[:300])
for path in ["/v1/DailyMarketReportOpt","/v1/PutCallRatio","/v1/OpenInterestOfLargeTradersOptions"]:
    s,b=get("https://openapi.taifex.com.tw"+path); print("==",path,s,len(b)); print(b[:1200].decode("utf-8","replace"))
for name,u,form in [
 ("optDataDown","https://www.taifex.com.tw/cht/3/optDataDown",{"down_type":"1","commodity_id":"TXO","commodity_id2":"","queryStartDate":"2026/11/18","queryEndDate":"2026/11/18"}),
 ("pcRatioDown","https://www.taifex.com.tw/cht/3/pcRatioDown",{"queryStartDate":"2026/10/20","queryEndDate":"2026/11/18"}),
 ("callsAndPutsDateDown","https://www.taifex.com.tw/cht/3/callsAndPutsDateDown",{"queryStartDate":"2026/11/18","queryEndDate":"2026/11/18","commodityId":"TXO"}),
]:
    s,b=get(u,urllib.parse.urlencode(form).encode())
    t=b.decode("big5","replace") if b[:1]!=b"<" else b.decode("utf-8","replace")
    print("==",name,s,len(b)); print(t[:1500])
