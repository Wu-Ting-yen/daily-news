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

# 證交所官方：全市場擔保維持率、追繳戶數／金額、處分戶數／金額（儀表板 BFIJ3U）
import datetime as dt
def tw(u):
    for i in range(3):
        try:
            return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=40).read())
        except Exception as e:
            time.sleep(10)
    return None
def bf(d):
    j = tw(f"https://www.twse.com.tw/rwd/zh/marginTrading/BFIJ3U?date={d:%Y%m%d}&response=json")
    time.sleep(2.5)
    return j if j and j.get("stat") == "OK" else None
os.makedirs("data/bt", exist_ok=True)
off = {}
try:
    off = json.load(open("data/bt/official.json"))
except Exception:
    pass
today = dt.date.today()
start = None
if off:
    start = dt.date.fromisoformat(max(off)) + dt.timedelta(days=1)
else:
    # 找最早有資料的月份（每月第 3～9 天試一次）
    for y in range(2015, today.year + 1):
        for m in range(1, 13):
            if dt.date(y, m, 1) > today: break
            hit = None
            for dd in range(3, 10):
                d = dt.date(y, m, dd)
                if d.weekday() < 5:
                    hit = bf(d); break
            if hit:
                start = dt.date(y, m, 1) - dt.timedelta(days=35); break
        if start: break
    print("官方維持率最早約", start)
d = start
while d and d <= today:
    if d.weekday() < 5 and d.isoformat() not in off:
        j = bf(d)
        if j:
            off[d.isoformat()] = {k: j.get(k) for k in ("keepRate", "callAccNum", "callAmt", "exeAccNum", "exeAmt", "belowAccNum", "crdAmt")}
    d += dt.timedelta(days=1)
json.dump(dict(sorted(off.items())), open("data/bt/official.json", "w"), separators=(",", ":"))
print("official days", len(off), min(off) if off else None)
