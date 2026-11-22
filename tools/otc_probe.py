"""一次性：算上市＋上櫃合計的外資持股市值比例"""
import json, sys, time, urllib.request, urllib.parse
sys.path.insert(0, ".")
import foreign as F

UA = {"User-Agent": "Mozilla/5.0"}

def raw(url, data=None):
    try:
        req = urllib.request.Request(url, data=data, headers=UA)
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.read()
    except Exception as e:
        print("  ERR", url[:100], e); return None

def js(b):
    try: return json.loads(b.decode("utf-8", "replace"))
    except Exception: return None

def otc_tables(j):
    """回傳 [(fields, rows)]，新舊格式都試"""
    out = []
    if not isinstance(j, (dict, list)): return out
    if isinstance(j, list) and j and isinstance(j[0], dict):
        out.append((list(j[0].keys()), [list(r.values()) for r in j])); return out
    for t in j.get("tables") or []:
        out.append((t.get("fields") or [], t.get("data") or []))
    if j.get("aaData"):
        out.append((j.get("fields") or [], j["aaData"]))
    return out

def otc_hold(d):
    roc = f"{d.year-1911}/{d:%m/%d}"
    cands = [
        ("GET", f"https://www.tpex.org.tw/web/stock/3insti/qfii/qfii_result.php?l=zh-tw&o=json&d={roc}", None),
        ("POST", "https://www.tpex.org.tw/www/zh-tw/insti/qfii", urllib.parse.urlencode({"date": f"{d:%Y/%m/%d}", "type": "AL", "response": "json"}).encode()),
        ("GET", f"https://www.tpex.org.tw/www/zh-tw/insti/qfii?date={d:%Y/%m/%d}&response=json", None),
        ("GET", "https://www.tpex.org.tw/openapi/v1/tpex_3insti_qfii", None),
    ]
    for m, u, data in cands:
        b = raw(u, data)
        print("  try", m, u[:90], "->", (b or b"")[:300])
        j = js(b) if b else None
        for f, rows in otc_tables(j):
            print("   fields", f, "rows", len(rows), rows[:2])
            if rows: return f, rows
        time.sleep(2)
    return None, None

def otc_close(d):
    roc = f"{d.year-1911}/{d:%m/%d}"
    j = js(raw(f"https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php?l=zh-tw&o=json&d={roc}") or b"")
    px = {}
    for f, rows in otc_tables(j):
        if not rows: continue
        for r in rows:
            c = str(r[0]).strip(); v = F.num(r[2])
            if v: px[c] = v
    if not px:
        b = raw(f"https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date={d:%Y/%m/%d}&response=json")
        print("  new close ->", (b or b"")[:300])
        for f, rows in otc_tables(js(b or b"")):
            for r in rows:
                c = str(r[0]).strip(); v = F.num(r[2])
                if v: px[c] = v
    return px

def pick(f, names):
    for i, x in enumerate(f):
        for n in names:
            if n in str(x): return i
    return None

import datetime as dt
for ds in sys.argv[1:]:
    d = dt.date.fromisoformat(ds)
    print("=====", d)
    h = F.holdings(d); time.sleep(3); px, _ = F.closes(d); time.sleep(3)
    tm = tf = tsm_f = 0.0; ss = sf = 0.0; tsh=(1,0)
    for c, (iss, fo) in (h or {}).items():
        p = (px or {}).get(c)
        if not p: continue
        tm += iss*p; tf += fo*p; ss += iss; sf += fo
        if c=="2330": tsh=(iss,fo)
        if c == "2330": tsm_f = fo*p
    print(f"上市 股數 {ss/1e9:.1f} 外資 {sf/1e9:.1f} = {sf/ss*100:.2f}%  台積 {tsh[1]/tsh[0]*100:.2f}% 台積占股數 {tsh[0]/ss*100:.2f}% 台積外資股占外資股 {tsh[1]/sf*100:.2f}%")
    print(f"上市 市值 {tm/1e12:.2f} 兆 外資 {tf/1e12:.2f} 兆 = {tf/tm*100:.2f}% 台積外資 {tsm_f/1e12:.2f} 兆")
    f, rows = otc_hold(d); time.sleep(3)
    opx = otc_close(d)
    print("  otc closes", len(opx))
    if not rows: continue
    ic = pick(f, ["代號"]) or 0
    iss_i = pick(f, ["發行股數"]); fo_i = pick(f, ["僑外資及陸資持有股數", "全體外資及陸資持有股數", "外資及陸資持有股數", "持有股數"])
    print("  idx", ic, iss_i, fo_i)
    om = of = 0.0; n = 0; big = []; os_ = of_ = 0.0
    for r in rows:
        c = str(r[ic]).strip()
        if not F.common(c): continue
        a, b = F.num(r[iss_i]), F.num(r[fo_i]); p = opx.get(c)
        if not (a and b is not None and p): continue
        n += 1; om += a*p; of += b*p; os_ += a; of_ += b; big.append((b*p, c, a*p))
    big.sort(reverse=True)
    print(f"上櫃 {n} 檔 市值 {om/1e12:.2f} 兆 外資 {of/1e12:.2f} 兆 = {of/om*100:.2f}%")
    print("  上櫃外資最多:", [(c, round(v/1e9), round(m/1e9)) for v, c, m in big[:5]])
    print(f"上櫃 股數 {os_/1e9:.1f} 外資 {of_/1e9:.1f} = {of_/os_*100:.2f}%  合計股數口徑 {(sf+of_)/(ss+os_)*100:.2f}%")
    M, FV = tm+om, tf+of
    print(f"合計 市值 {M/1e12:.2f} 兆 外資 {FV/1e12:.2f} 兆 = {FV/M*100:.2f}%  台積 {tsm_f/M*100:.2f}%  其他 {(FV-tsm_f)/M*100:.2f}%")
