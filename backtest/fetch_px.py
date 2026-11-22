#!/usr/bin/env python3
"""回測用：全部上市櫃普通股 2000 年起日股價（Yahoo）→ bt_raw/px.json.gz
每檔：[日期(距1970天數)], 還原收盤(含息), 收盤, 成交值(百萬元)
另存 bt_raw/universe.json：每月底成交值前 300 大名單（之後抓財報只抓這些）"""
import gzip, json, os, time, urllib.request, datetime as dt
UA = {"User-Agent": "Mozilla/5.0"}
OUT = "bt_raw"; os.makedirs(OUT, exist_ok=True)

def jget(u, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=40) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            if "404" in str(e): return None
            time.sleep(3 * (i + 1))
    return None

info = jget("https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInfo")["data"]
seen, codes = set(), []
for x in info:
    c = x["stock_id"]
    if len(c) == 4 and c.isdigit() and c[0] != "0" and x["type"] in ("twse", "tpex") and c not in seen and x["industry_category"] not in ("Index", "ETF", "存託憑證", "受益證券"):
        seen.add(c); codes.append((c, x["type"], x["stock_name"], x["industry_category"]))
print("codes", len(codes), flush=True)
px, meta = {}, {}
p1 = int(dt.datetime(2000, 1, 1).timestamp()); p2 = int(time.time())
for i, (c, t, nm, ind) in enumerate(codes):
    res = None
    for sfx in ([".TW", ".TWO"] if t == "twse" else [".TWO", ".TW"]):
        j = jget(f"https://query1.finance.yahoo.com/v8/finance/chart/{c}{sfx}?period1={p1}&period2={p2}&interval=1d&events=div,split")
        try:
            res = j["chart"]["result"][0]
            if res.get("timestamp"): break
        except Exception:
            res = None
    if not res or not res.get("timestamp"): continue
    q = res["indicators"]["quote"][0]; adj = (res["indicators"].get("adjclose") or [{}])[0].get("adjclose") or q["close"]
    D, A, C, V = [], [], [], []
    for ts, a, cl, v in zip(res["timestamp"], adj, q["close"], q["volume"]):
        if cl is None or a is None: continue
        D.append(int((ts + 8 * 3600) // 86400)); A.append(round(a, 3)); C.append(round(cl, 2)); V.append(round(cl * (v or 0) / 1e6, 1))
    px[c] = [D, A, C, V]; meta[c] = [t, nm, ind]
    if i % 100 == 0: print(i, c, nm, len(D), flush=True)
    time.sleep(0.25)
json.dump(meta, open(f"{OUT}/meta.json", "w"), ensure_ascii=False)
with gzip.open(f"{OUT}/px.json.gz", "wt") as f: json.dump(px, f, separators=(",", ":"))
# 每月底成交值（近 60 日平均）前 300 大
import bisect
months = sorted({d // 30 for v in px.values() for d in v[0][::20]})
uni = set()
for y in range(2001, dt.date.today().year + 1):
    for m in range(1, 13):
        day = (dt.date(y, m, 28) - dt.date(1970, 1, 1)).days
        if day > p2 // 86400: break
        sc = []
        for c, (D, A, C, V) in px.items():
            k = bisect.bisect_right(D, day)
            if k < 60 or D[k - 1] < day - 10: continue
            sc.append((sum(V[k - 60:k]) / 60, c))
        sc.sort(reverse=True); uni.update(c for _, c in sc[:300])
json.dump(sorted(uni), open(f"{OUT}/universe.json", "w"))
print("stocks", len(px), "universe", len(uni))
