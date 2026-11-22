#!/usr/bin/env python3
"""加權指數（2001 起）＋外資每日買賣超（2004 起）→ bt_raw/taiex.json, bt_raw/inst.json"""
import json, os, urllib.request, time
os.makedirs("bt_raw", exist_ok=True)
tok = os.environ.get("FINMIND_TOKEN", "")
def fm(q):
    u = "https://api.finmindtrade.com/api/v4/data?" + q + (f"&token={tok}" if tok else "")
    for i in range(4):
        try:
            return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=120).read())["data"]
        except Exception as e:
            print("err", e); time.sleep(20)
    return []
ix = {x["date"]: x["close"] for x in fm("dataset=TaiwanStockPrice&data_id=TAIEX&start_date=2000-10-01")}
inst = {}
for x in fm("dataset=TaiwanStockTotalInstitutionalInvestors&start_date=2004-01-01"):
    if x["name"] in ("Foreign_Investor", "Foreign_Dealer_Self"):
        inst[x["date"]] = inst.get(x["date"], 0) + (x["buy"] - x["sell"]) / 1e8
json.dump(ix, open("bt_raw/taiex.json", "w")); json.dump({k: round(v, 2) for k, v in inst.items()}, open("bt_raw/inst.json", "w"))
print("taiex", len(ix), min(ix), max(ix), "inst", len(inst))
