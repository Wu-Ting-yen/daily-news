#!/usr/bin/env python3
"""回測用：每日全市場融資資料（2001 起）→ data/bt/margin/<年>.json
每天：上市 MI_MARGN(ALL) + MI_INDEX 收盤價 → 融資張數、融資金額、擔保品市值（估維持率用）
      上櫃 融資餘額 + 收盤價 → 融資張數、融資市值
用法：python backtest/margin_hist.py 2001-01-01 2003-12-31
"""
import datetime as dt, json, os, sys, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "bt", "margin")
UA = {"User-Agent": "Mozilla/5.0"}
GAP = float(os.environ.get("GAP", "2.2"))


def jget(url):
    for i in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            print("  retry", i + 1, url[-60:], e, flush=True)
            time.sleep(20 * (i + 1))
    return None


def num(s):
    try:
        return float(str(s).replace(",", "").strip())
    except Exception:
        return None


def tables(j):
    """新舊格式都轉成 [(title, fields, data)]"""
    out = []
    for t in j.get("tables") or []:
        if t.get("fields"):
            out.append((t.get("title", ""), t["fields"], t.get("data") or []))
    for k in list(j.keys()):
        if k.startswith("fields") and isinstance(j[k], list):
            sfx = k[6:]
            out.append((j.get("title" + sfx, ""), j[k], j.get("data" + sfx) or []))
    return out


def twse(d):
    s = d.strftime("%Y%m%d")
    m = jget(f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={s}&selectType=ALL&response=json")
    if not m or m.get("stat") != "OK":
        return None
    time.sleep(GAP)
    shares = money = None
    per = {}
    for title, f, data in tables(m):
        if f and f[0] == "項目":
            for r in data:
                if str(r[0]).startswith("融資(交易單位)"):
                    shares = num(r[5])
                elif str(r[0]).startswith("融資金額"):
                    money = num(r[5])
        elif "代號" in f or "股票代號" in f:
            ic = 0
            # 第一個「今日餘額」是融資
            ib = f.index("今日餘額")
            for r in data:
                v = num(r[ib])
                if v:
                    per[str(r[ic]).strip()] = v
    p = jget(f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={s}&type=ALLBUT0999&response=json")
    time.sleep(GAP)
    px, taiex = {}, None
    if p and p.get("stat") == "OK":
        for title, f, data in tables(p):
            if "證券代號" in f and "收盤價" in f:
                ic, ip = f.index("證券代號"), f.index("收盤價")
                for r in data:
                    v = num(r[ip])
                    if v:
                        px[str(r[ic]).strip()] = v
            for r in data:
                if r and str(r[0]).strip() in ("發行量加權股價指數", "臺灣加權股價指數"):
                    taiex = taiex or num(r[1])
    coll = sum(n * 1000 * px[c] for c, n in per.items() if c in px)
    cov = sum(n for c, n in per.items() if c in px) / (sum(per.values()) or 1)
    out = {"sh": shares, "money": money * 1000 if money else None, "coll": round(coll), "cov": round(cov, 3), "taiex": taiex, "n": len(per)}
    if not px:  # 2004/2 以前證交所沒有全部個股收盤價，先存各檔融資張數，之後用其他股價補算
        out["per"] = per
    return out


def tpex(d):
    roc = f"{d.year - 1911:03d}/{d.month:02d}/{d.day:02d}"
    m = jget(f"https://www.tpex.org.tw/web/stock/margin_trading/margin_balance/margin_bal_result.php?l=zh-tw&o=json&d={roc}")
    time.sleep(GAP)
    if not m:
        return None
    per = {}
    for title, f, data in tables(m):
        if "資餘額" in f:
            ic, ib = f.index("代號"), f.index("資餘額")
            for r in data:
                v = num(r[ib])
                if v:
                    per[str(r[ic]).strip()] = v
    if not per:
        return None
    p = jget(f"https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php?l=zh-tw&o=json&d={roc}")
    time.sleep(GAP)
    px = {}
    if p:
        for title, f, data in tables(p):
            if "代號" in f and "收盤" in f:
                ic, ip = f.index("代號"), f.index("收盤")
                for r in data:
                    v = num(r[ip])
                    if v:
                        px[str(r[ic]).strip()] = v
    coll = sum(n * 1000 * px[c] for c, n in per.items() if c in px)
    return {"sh": sum(per.values()), "coll": round(coll), "cov": round(sum(n for c, n in per.items() if c in px) / sum(per.values()), 3)}


def main(a, b):
    a, b = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
    os.makedirs(OUT, exist_ok=True)
    cache = {}
    d = a
    while d <= b:
        y = d.year
        if y not in cache:
            p = os.path.join(OUT, f"{y}.json")
            cache[y] = json.load(open(p)) if os.path.exists(p) else {}
        k = d.isoformat()
        if d.weekday() < 5 and k not in cache[y]:
            t = twse(d)
            if t:
                o = tpex(d)
                cache[y][k] = {"tw": t, "otc": o}
                print(k, "上市融資", t["sh"], "金額", t["money"], "擔保", t["coll"], "維持率≈",
                      round(t["coll"] / t["money"] * 100, 1) if t["money"] else None, "cov", t["cov"],
                      "| 上櫃", o and o["sh"], flush=True)
                if len(cache[y]) % 20 == 0:
                    json.dump(cache[y], open(os.path.join(OUT, f"{y}.json"), "w"), separators=(",", ":"))
        d += dt.timedelta(days=1)
    for y, v in cache.items():
        json.dump(dict(sorted(v.items())), open(os.path.join(OUT, f"{y}.json"), "w"), separators=(",", ":"))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
