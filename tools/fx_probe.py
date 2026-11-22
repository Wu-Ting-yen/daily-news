import urllib.request, re
UA = {"User-Agent": "Mozilla/5.0"}
def get(u):
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=40)
        return r.status, r.read()
    except Exception as e:
        return "ERR", str(e).encode()
for u in ["https://cpx.cbc.gov.tw/Data/ExportToAPIInfo",
          "https://data.gov.tw/api/v2/rest/dataset/6025",
          "https://www.cbc.gov.tw/tw/lp-302-1.html",
          "https://www.cbc.gov.tw/tw/np-1214-1.html"]:
    st, b = get(u); t = b.decode("utf-8", "replace")
    print("=====", u, st, len(b))
    for m in re.finditer(r".{0,200}(外匯存底|FileName|準備資產|外匯準備).{0,300}", t):
        print("  >", re.sub(r"\s+", " ", m.group(0))[:500])
    if "6025" in u: print(t[:3000])
for fn in ["EF01M01","EF02M01","EF03M01","EG01M01","EG02M01","EF16M01","EF17M01","BP01M01","BP02M01","EF14M01","EF13M01","EF12M01"]:
    st, b = get("https://cpx.cbc.gov.tw/API/DataAPI/Get?FileName=" + fn)
    t = b.decode("utf-8", "replace")
    print("-----", fn, st, len(b), re.sub(r"\s+", " ", t[:600]))
