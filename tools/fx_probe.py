import urllib.request, json, sys, ssl
UA = {"User-Agent": "Mozilla/5.0"}
def get(u, data=None):
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, data=data, headers=UA), timeout=40)
        b = r.read()
        return r.status, b
    except Exception as e:
        return "ERR", str(e).encode()
urls = [
 "https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS?date=20261120&selectType=ALLBUT0999&response=json",
 "https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS?date=20260105&selectType=ALLBUT0999&response=json",
 "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=20261120&type=ALLBUT0999&response=json",
 "https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS_cat?date=20261120&response=json",
 "https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS_sort_20?date=20261120&response=json",
 "https://www.cbc.gov.tw/tw/lp-645-1.html",
 "https://www.cbc.gov.tw/tw/lp-302-1.html",
 "https://cpx.cbc.gov.tw/API/DataAPI/Get?FileName=EF15M01",
 "https://www.cbc.gov.tw/public/data/OpenData/%E5%A4%96%E5%8C%AF%E5%B1%80/res.csv",
 "https://data.gov.tw/api/v2/rest/dataset?q=%E5%A4%96%E5%8C%AF%E5%AD%98%E5%BA%95",
 "https://query1.finance.yahoo.com/v8/finance/chart/TWD=X?range=1y&interval=1d",
 "https://www.taifex.com.tw/cht/3/dailyFXRateDown?queryStartDate=2026/11/01&queryEndDate=2026/11/20",
]
for u in urls:
    st, b = get(u)
    t = b.decode("utf-8", "replace")
    print("=====", u, st, len(b))
    if u.find("MI_QFIIS?") > 0 and st == 200:
        j = json.loads(t); print(j.get("stat"), j.get("fields")); print((j.get("data") or [])[:3]); print("rows", len(j.get("data") or []))
        print({k: (str(v)[:300]) for k, v in j.items() if k not in ("data", "fields")})
    elif "MI_INDEX" in u and st == 200:
        j = json.loads(t)
        for tb in j.get("tables", []):
            print(tb.get("title"), tb.get("fields"), (tb.get("data") or [])[:2])
    else:
        print(t[:1500])
