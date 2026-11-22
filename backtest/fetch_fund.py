#!/usr/bin/env python3
"""回測用：每季 EPS ＋ 每月營收（FinMind）。用法：fetch_fund.py <第幾份> <共幾份>"""
import json, os, sys, time, urllib.request
UA = {"User-Agent": "Mozilla/5.0"}
part, parts = int(sys.argv[1]), int(sys.argv[2])
tok = os.environ.get("FINMIND_TOKEN", "")
uni = json.load(open("bt_raw/universe.json"))[part::parts]
def fm(ds, c):
    u = f"https://api.finmindtrade.com/api/v4/data?dataset={ds}&data_id={c}&start_date=1999-01-01" + (f"&token={tok}" if tok else "")
    for i in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=60) as r:
                j = json.loads(r.read())
            if j.get("status") == 402 or "limit" in str(j.get("msg", "")).lower():
                print("limit, wait", flush=True); time.sleep(600); continue
            return j.get("data") or []
        except Exception as e:
            print(" err", c, e, flush=True); time.sleep(30 * (i + 1))
    return []
out = {}
for i, c in enumerate(uni):
    eps = [[x["date"], x["value"]] for x in fm("TaiwanStockFinancialStatements", c) if x["type"] == "EPS"]
    rev = [[x["revenue_year"], x["revenue_month"], x["revenue"]] for x in fm("TaiwanStockMonthRevenue", c)]
    out[c] = {"eps": eps, "rev": rev}
    if i % 20 == 0: print(part, i, c, len(eps), len(rev), flush=True)
    time.sleep(12.5 if not tok else 1)
os.makedirs("bt_raw", exist_ok=True)
json.dump(out, open(f"bt_raw/fund_{part}.json", "w"), separators=(",", ":"))
