import urllib.request, json
UA={"User-Agent":"Mozilla/5.0"}
def get(u):
    try:
        r=urllib.request.urlopen(urllib.request.Request(u,headers=UA),timeout=40);return r.status,r.read()
    except Exception as e: return str(e),b""
for sw in ["https://openapi.twse.com.tw/v1/swagger.json","https://www.tpex.org.tw/openapi/swagger.json"]:
    s,b=get(sw); print("SW",sw,s,len(b))
    try:
        j=json.loads(b)
        for p,v in j.get("paths",{}).items():
            sm=(v.get("get") or {}).get("summary","")
            if "權證" in sm or "warrant" in p.lower(): print(p,sm)
    except Exception as e: print(e, b[:200])
for name,u in [("mi0999","https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=20261118&type=0999&response=json"),
               ("mi0999P","https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=20261118&type=0999P&response=json")]:
    s,b=get(u); print("==",name,s,len(b))
    try:
        j=json.loads(b)
        for t in j.get("tables",[]):
            print(t.get("title"),t.get("fields")); print(json.dumps(t.get("data",[])[:3],ensure_ascii=False)[:800])
    except Exception as e: print(b[:300])
