#!/usr/bin/env python3
"""外資持股比例（上市股票，市值加權）＋ 外匯存底 ＋ 美元匯率 → foreign.html

外資持股比例 = Σ(每檔外資持有股數 × 收盤價) ÷ Σ(每檔發行股數 × 收盤價)
台積電部分   = 台積電外資持有市值 ÷ 上市總市值
其他         = 總比例 − 台積電部分
只算上市普通股（4 位數代號、不含 0 開頭 ETF、91 開頭 TDR）。

資料：證交所 MI_QFIIS（外資持股）、MI_INDEX（收盤價、加權指數）；
期交所每日匯率（美元/新台幣）；央行外匯存底（每月）。
只用標準函式庫。快取在 data/foreign/，每天只補新的日子。
"""
import csv, datetime as dt, io, json, os, re, sys, time, urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(ROOT, "data", "foreign")
DAILY = os.path.join(DIR, "daily.json")
FX = os.path.join(DIR, "fx.json")
RES = os.path.join(DIR, "reserves.json")
START = dt.date(2026, 1, 1)
FX_START = dt.date(2025, 1, 1)
UA = {"User-Agent": "Mozilla/5.0 (daily-news bot)"}
TZ = dt.timezone(dt.timedelta(hours=8))
MAX_NEW = int(os.environ.get("MAX_NEW_DAYS", "400"))


def get(url, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
                return r.read()
        except Exception as e:
            print("  retry", i + 1, url[:90], e)
            time.sleep(6 * (i + 1))
    return None


def jget(url):
    b = get(url)
    if not b:
        return None
    try:
        return json.loads(b.decode("utf-8", "replace"))
    except Exception:
        return None


def num(s):
    try:
        return float(str(s).replace(",", "").strip())
    except Exception:
        return None


def load(p, default):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save(p, obj):
    os.makedirs(DIR, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def common(code):
    return len(code) == 4 and code.isdigit() and code[0] != "0" and not code.startswith("91")


# ---------- 每日外資持股 ----------
def closes(d):
    j = jget(f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={d:%Y%m%d}&type=ALLBUT0999&response=json")
    if not j or j.get("stat") != "OK":
        return None, None
    px, taiex = {}, None
    for tb in j.get("tables", []):
        f = tb.get("fields") or []
        if "證券代號" in f and "收盤價" in f:
            ic, ip = f.index("證券代號"), f.index("收盤價")
            for r in tb.get("data") or []:
                v = num(r[ip])
                if v:
                    px[str(r[ic]).strip()] = v
        for r in tb.get("data") or []:
            if r and str(r[0]).strip() == "發行量加權股價指數":
                taiex = num(r[1])
    return px, taiex


def holdings(d):
    j = jget(f"https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS?date={d:%Y%m%d}&selectType=ALLBUT0999&response=json")
    if not j or j.get("stat") != "OK" or not j.get("data"):
        return None
    f = j["fields"]
    ic, iss, ifo = f.index("證券代號"), f.index("發行股數"), f.index("全體外資及陸資持有股數")
    out = {}
    for r in j["data"]:
        c = str(r[ic]).strip()
        if common(c):
            a, b = num(r[iss]), num(r[ifo])
            if a and b is not None:
                out[c] = (a, b)
    return out


def day_row(d, last_px):
    h = holdings(d)
    if h is None:
        return None
    time.sleep(2.5)
    px, taiex = closes(d)
    if not px:
        return None
    last_px.update(px)
    mcap = fval = 0.0
    t_m = t_f = 0.0
    n = 0
    for c, (iss, fo) in h.items():
        p = px.get(c) or last_px.get(c)
        if not p:
            continue
        n += 1
        mcap += iss * p
        fval += fo * p
        if c == "2330":
            t_m, t_f = iss * p, fo * p
    if not mcap or not t_m:
        return None
    return {
        "d": d.isoformat(),
        "total": round(fval / mcap * 100, 2),          # 外資持股市值 ÷ 上市總市值
        "tsmc": round(t_f / mcap * 100, 2),            # 台積電外資持股市值 ÷ 上市總市值
        "other": round((fval - t_f) / mcap * 100, 2),
        "tsmc_own": round(t_f / t_m * 100, 2),         # 台積電自己的外資持股比例
        "tsmc_w": round(t_m / mcap * 100, 2),          # 台積電占上市市值
        "mcap": round(mcap / 1e12, 2),                 # 兆元
        "fval": round(fval / 1e12, 2),
        "taiex": taiex,
        "n": n,
    }


def update_daily():
    rows = load(DAILY, [])
    have = {r["d"] for r in rows}
    today = dt.datetime.now(TZ).date()
    last_px = {}
    d, new = START, 0
    while d <= today and new < MAX_NEW:
        if d.weekday() < 5 and d.isoformat() not in have:
            # 當天收盤後 16:00 前資料可能還沒出
            if d == today and dt.datetime.now(TZ).hour < 16:
                break
            r = day_row(d, last_px)
            if r:
                rows.append(r)
                print(" ", r["d"], "外資", r["total"], "台積", r["tsmc"], "其他", r["other"])
            else:
                print(" ", d, "無資料（休市）")
            new += 1
            time.sleep(2.5)
            if new % 20 == 0:
                rows.sort(key=lambda x: x["d"])
                save(DAILY, rows)
        d += dt.timedelta(days=1)
    rows.sort(key=lambda x: x["d"])
    save(DAILY, rows)
    return rows


# ---------- 匯率（期交所，美元/新台幣） ----------
def update_fx():
    fx = load(FX, {})
    today = dt.datetime.now(TZ).date()
    m = FX_START
    while m <= today:
        nm = (m.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        end = min(nm - dt.timedelta(days=1), today)
        done = any(k.startswith(m.strftime("%Y-%m")) for k in fx)
        if not done or end >= today - dt.timedelta(days=40):
            b = get(f"https://www.taifex.com.tw/cht/3/dailyFXRateDown?queryStartDate={m:%Y/%m/%d}&queryEndDate={end:%Y/%m/%d}")
            if b:
                t = b.decode("big5", "replace")
                for r in csv.reader(io.StringIO(t)):
                    if len(r) > 1 and re.match(r"\d{4}/\d{1,2}/\d{1,2}", r[0].strip()):
                        v = num(r[1])
                        if v and 20 < v < 50:
                            y, mo, da = map(int, r[0].strip().split("/"))
                            fx[f"{y:04d}-{mo:02d}-{da:02d}"] = v
            time.sleep(1.5)
        m = nm
    if not fx:  # 備援：Yahoo
        j = jget("https://query1.finance.yahoo.com/v8/finance/chart/TWD=X?range=2y&interval=1d")
        try:
            res = j["chart"]["result"][0]
            for ts, c in zip(res["timestamp"], res["indicators"]["quote"][0]["close"]):
                if c:
                    fx[dt.datetime.fromtimestamp(ts, TZ).date().isoformat()] = round(c, 3)
        except Exception:
            pass
    fx = dict(sorted(fx.items()))
    save(FX, fx)
    return fx


# ---------- 外匯存底（央行，每月） ----------
def update_reserves():
    res = load(RES, {})
    b = get("https://www.cbc.gov.tw/public/data/OpenData/WebF3.csv")
    if b:
        t = b.decode("utf-8-sig", "replace")
        print("  WebF3 開頭:", t[:120].replace("\n", " | "))
        for r in csv.reader(io.StringIO(t)):
            if len(r) < 2:
                continue
            m = re.match(r"^\s*(\d{4})\D?(\d{1,2})\s*$", r[0])
            v = num(r[1])
            if m and v:
                y, mo = int(m.group(1)), int(m.group(2))
                if y < 1911 - 100:  # 民國年
                    y += 1911
                if 1 <= mo <= 12:
                    res[f"{y:04d}-{mo:02d}"] = v
    res = dict(sorted(res.items()))
    save(RES, res)
    return res


if __name__ == "__main__":
    os.makedirs(DIR, exist_ok=True)
    print("外資持股…")
    rows = update_daily()
    print("匯率…")
    fx = update_fx()
    print("外匯存底…")
    res = update_reserves()
    import foreign_page
    foreign_page.build(rows, fx, res)
    print("完成", len(rows), "天", len(fx), "匯率", len(res), "個月存底")
