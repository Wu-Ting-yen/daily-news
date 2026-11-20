#!/usr/bin/env python3
"""美股分析：市值前 10 大美國公司，用個股選擇權（未平倉、各履約價成交量、隱含波動度、偏斜）
和成交量分佈（Volume Profile）找出「地板」（支撐）在哪裡。

資料（全部免費、不用帳號）：
- 股票清單與市值：Nasdaq 股票篩選器 api.nasdaq.com
- 選擇權：Cboe 延遲報價 cdn.cboe.com（每個合約的未平倉、成交量、隱含波動度、Delta）
- 股價：Yahoo Finance 日線（一年）與 60 分鐘線（三個月）

輸出：usstocks.html、data/us_stocks.json、data/us_history/<日期>.json、data/us_signals.json
"""
import datetime as dt
import gzip
import html
import json
import math
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
TW = dt.timezone(dt.timedelta(hours=8))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept": "application/json,text/plain,*/*"}
TOP_N = 10
PREFER = {"GOOG", "BRK/B"}            # 同一家公司兩種股票時用哪一檔（GOOG／GOOGL 只看 GOOG）
SKIP = {"BRK/A", "GOOGL"}
FIXTURE = os.environ.get("FIXTURE")     # 本機測試：用 probe/out 的樣本資料

ZH = {"AAPL": "蘋果", "MSFT": "微軟", "NVDA": "輝達", "GOOG": "Google", "AMZN": "亞馬遜", "SPCX": "SpaceX",
      "AVGO": "博通", "TSM": "台積電 ADR", "SKHY": "SK 海力士", "ASML": "艾司摩爾", "BABA": "阿里巴巴", "NVO": "諾和諾德", "META": "Meta", "TSLA": "特斯拉", "LLY": "禮來", "BRK/B": "波克夏", "WMT": "沃爾瑪",
      "JPM": "摩根大通", "V": "Visa", "ORCL": "甲骨文", "NFLX": "Netflix", "XOM": "埃克森美孚", "MA": "萬事達卡",
      "COST": "好市多", "PLTR": "Palantir", "AMD": "超微", "JNJ": "嬌生", "UNH": "聯合健康", "HD": "家得寶",
      "PG": "寶僑", "BAC": "美國銀行", "ABBV": "艾伯維", "KO": "可口可樂", "CRM": "Salesforce", "MU": "美光"}


# ───────────────────────── 抓資料 ─────────────────────────

def get(url, tries=3, timeout=40):
    if FIXTURE:
        return fixture(url)
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as e:
            print("get fail", url[:90], e, file=sys.stderr)
            if 400 <= e.code < 500:
                return None   # 被拒絕就不重試，直接換下一個來源
            time.sleep(2 + 3 * i)
        except Exception as e:
            print("get fail", url[:90], e, file=sys.stderr)
            time.sleep(2 + 3 * i)
    return None


def fixture(url):
    p = lambda f: os.path.join(ROOT, "probe", "out", f)
    if "nasdaq" in url:
        return json.load(gzip.open(p("nasdaq.json.gz")))
    if "cboe" in url:
        f = "cboe_goog.json.gz" if "GOOG" in url else "cboe_aapl.json.gz"
        return json.load(gzip.open(p(f)))
    if "1h" in url:
        return json.load(open(p("y_h.json")))
    if "finance/chart" in url:
        return json.load(open(p("y_d.json")))
    return None


def load(path, default):
    try:
        with open(os.path.join(ROOT, path), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save(path, obj):
    full = os.path.join(ROOT, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def universe():
    """在美國掛牌的股票市值前 N 大（同一家公司只取一檔；台積電 TSM 這類外國公司的 ADR 也算）"""
    j = get("https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=10000&offset=0&download=true")
    rows = (((j or {}).get("data") or {}).get("rows")) or []
    out, seen = [], set()
    for r in sorted((r for r in rows if r.get("marketCap")), key=lambda r: -float(r["marketCap"] or 0)):
        sym = r["symbol"].strip()
        if sym in SKIP or "^" in sym:
            continue
        key = re.sub(r"\s+(Class [A-Z]\b|Common Stock|Capital Stock|Ordinary Shares).*", "", r["name"]).strip(" .,")
        if key in seen:
            continue
        seen.add(key)
        out.append({"sym": sym, "name": key, "cap": float(r["marketCap"]), "sector": r.get("sector") or "",
                    "industry": r.get("industry") or ""})
        if len(out) >= TOP_N:
            break
    if len(out) < TOP_N:   # 拿不到就用上一次的名單
        prev = load("data/us_stocks.json", {}).get("stocks") or []
        if prev:
            out = [{k: s[k] for k in ("sym", "name", "cap", "sector", "industry") if k in s} for s in prev]
    return out


def ysym(sym):
    return sym.replace("/", "-")


def yahoo(sym, rng="1y", interval="1d"):
    j = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?range={rng}&interval={interval}")
    try:
        r = j["chart"]["result"][0]
        q = r["indicators"]["quote"][0]
        tz = dt.timezone(dt.timedelta(seconds=r["meta"].get("gmtoffset", -18000)))
        out = []
        for i, t in enumerate(r["timestamp"]):
            c = q["close"][i]
            if c is None:
                continue
            d = dt.datetime.fromtimestamp(t, tz)
            out.append({"d": d.strftime("%Y-%m-%d"), "t": d.strftime("%H:%M"), "o": q["open"][i], "h": q["high"][i],
                        "l": q["low"][i], "c": c, "v": q["volume"][i] or 0})
        return out, r["meta"]
    except Exception:
        return [], {}


def cboe(sym):
    s = sym.replace("/", ".")
    j = get(f"https://cdn.cboe.com/api/global/delayed_quotes/options/{s}.json")
    return (j or {}).get("data")


# ── 備用來源：Nasdaq 選擇權報價表（有未平倉、成交量、買賣價，沒有隱含波動度 → 自己用 Black-Scholes 反推）──

RATE = 0.04   # 無風險利率（約略值，只影響隱含波動度一點點）


def ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs(S, K, T, sig, cp):
    if T <= 0 or sig <= 0:
        return max(0.0, S - K) if cp == "C" else max(0.0, K - S), (1.0 if S > K else 0.0) if cp == "C" else (-1.0 if S < K else 0.0)
    d1 = (math.log(S / K) + (RATE + sig * sig / 2) * T) / (sig * math.sqrt(T))
    d2 = d1 - sig * math.sqrt(T)
    if cp == "C":
        return S * ncdf(d1) - K * math.exp(-RATE * T) * ncdf(d2), ncdf(d1)
    return K * math.exp(-RATE * T) * ncdf(-d2) - S * ncdf(-d1), ncdf(d1) - 1


def implied(price, S, K, T, cp):
    """用二分法找出讓理論價等於市價的波動度"""
    intrinsic = max(0.0, S - K * math.exp(-RATE * T)) if cp == "C" else max(0.0, K * math.exp(-RATE * T) - S)
    if price <= intrinsic + 1e-4 or T <= 0:
        return None
    lo, hi = 0.01, 5.0
    if bs(S, K, T, hi, cp)[0] < price:
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if bs(S, K, T, mid, cp)[0] > price:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def nasdaq_chain(sym, spot, today):
    s = sym.replace("/", ".")
    j = get(f"https://api.nasdaq.com/api/quote/{urllib.parse.quote(s)}/option-chain?assetclass=stocks&limit=10000"
            "&fromdate=all&todate=undefined&money=all&type=all")
    rows = ((((j or {}).get("data") or {}).get("table") or {}).get("rows")) or []
    if not rows:
        return None, ""
    f = lambda v: float(str(v).replace(",", "")) if v not in (None, "", "--") else 0.0
    out, exp = [], None
    for r in rows:
        if r.get("expirygroup"):
            try:
                exp = dt.datetime.strptime(r["expirygroup"], "%B %d, %Y").date()
            except ValueError:
                exp = None
            continue
        if not exp or not r.get("strike"):
            continue
        k = f(r["strike"])
        dte = (exp - today).days
        T = max(dte, 0.5) / 365
        for cp, pre in (("C", "c_"), ("P", "p_")):
            bid, ask, last = f(r.get(pre + "Bid")), f(r.get(pre + "Ask")), f(r.get(pre + "Last"))
            # 盤後買賣價常常是空的，就改用最後成交價（收盤前最後一筆）
            # 買賣價差太大（盤前、盤後常見）時中間價不可靠，改用最後成交價
            mid = (bid + ask) / 2 if bid > 0 and ask > 0 else 0
            px = mid if mid and (ask - bid) / mid <= 0.25 else last
            iv = implied(px, spot, k, T, cp) if px and dte >= 0 else None
            delta = bs(spot, k, T, iv, cp)[1] if iv else 0
            out.append({"exp": exp, "dte": dte, "cp": cp, "k": k, "oi": f(r.get(pre + "Openinterest")),
                        "vol": f(r.get(pre + "Volume")), "iv": (iv or 0) * 100, "delta": delta,
                        "bid": bid, "ask": ask, "theo": px or last})
    asof = ((j.get("data") or {}).get("lastTrade") or "").replace("LAST TRADE:", "").strip()
    return out, asof


# ───────────────────────── 計算 ─────────────────────────

def parse_chain(data, today):
    """把 Cboe 合約代號（例如 GOOG261218C00290000）拆成 到期日、買/賣權、履約價"""
    out = []
    for o in data.get("options") or []:
        code = o.get("option", "")
        m = re.match(r"^(.*?)(\d{6})([CP])(\d{8})$", code)
        if not m:
            continue
        exp = dt.date(2000 + int(m.group(2)[:2]), int(m.group(2)[2:4]), int(m.group(2)[4:]))
        out.append({"exp": exp, "dte": (exp - today).days, "cp": m.group(3), "k": int(m.group(4)) / 1000,
                    "oi": o.get("open_interest") or 0, "vol": o.get("volume") or 0, "iv": (o.get("iv") or 0) * 100,
                    "delta": o.get("delta") or 0, "bid": o.get("bid") or 0, "ask": o.get("ask") or 0,
                    "theo": o.get("theo") or 0})
    return out


def nice_step(raw):
    for x in (0.5, 1, 2.5, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000):
        if x >= raw:
            return x
    return raw


def atm_iv(ch, spot, exp):
    """某個到期日的價平隱含波動度：最接近現價的履約價，買權賣權平均"""
    legs = [x for x in ch if x["exp"] == exp and x["iv"] > 1 and (x["bid"] > 0 or x["theo"] > 0)]
    if not legs:
        return None
    ks = sorted({x["k"] for x in legs}, key=lambda k: abs(k - spot))[:2]
    v = [x["iv"] for x in legs if x["k"] in ks]
    return round(sum(v) / len(v), 2) if v else None


def delta_iv(ch, exp, target, how=None):
    """某到期日、指定 Delta 的隱含波動度；how 會記下用了哪兩個履約價（給網頁說明用）"""
    legs = [x for x in ch if x["exp"] == exp and x["iv"] > 1 and (x["bid"] > 0 or x["theo"] > 0) and x["delta"]]
    if not legs:
        return None
    legs = sorted((x for x in legs if (x["delta"] < 0) == (target < 0)), key=lambda x: x["delta"])
    for a, b in zip(legs, legs[1:]):   # 在目標 Delta 兩側的兩個履約價之間內插
        if a["delta"] <= target <= b["delta"] and b["delta"] - a["delta"] < 0.3:
            w = (target - a["delta"]) / ((b["delta"] - a["delta"]) or 1)
            if how is not None:
                how += [[a["k"], round(a["delta"], 2), round(a["iv"], 1)], [b["k"], round(b["delta"], 2), round(b["iv"], 1)]]
            return round(a["iv"] + (b["iv"] - a["iv"]) * w, 2)
    best = min(legs, key=lambda x: abs(x["delta"] - target), default=None)
    if best and abs(best["delta"] - target) < 0.08:
        if how is not None:
            how.append([best["k"], round(best["delta"], 2), round(best["iv"], 1)])
        return round(best["iv"], 2)
    return None


def third_friday(y, m):
    d = dt.date(y, m, 15)
    return d + dt.timedelta(days=(4 - d.weekday()) % 7)


def options(sym, spot, today):
    data = cboe(sym)
    if data and data.get("options"):
        ch = [x for x in parse_chain(data, today) if x["dte"] >= 0]
        spot = data.get("current_price") or spot
        o = {"src": "Cboe", "spot": spot, "asof": (data.get("last_trade_time") or "")[:16].replace("T", " ") + "（美東）",
             "iv30": data.get("iv30"), "iv30_chg": data.get("iv30_change")}
    else:
        ch, asof = nasdaq_chain(sym, spot, today)
        ch = [x for x in ch or [] if x["dte"] >= 0]
        o = {"src": "Nasdaq", "spot": spot, "asof": asof, "iv30": None, "iv30_chg": None}
    if not ch:
        return None
    o["oi_c"] = int(sum(x["oi"] for x in ch if x["cp"] == "C"))
    o["oi_p"] = int(sum(x["oi"] for x in ch if x["cp"] == "P"))
    o["oi"] = o["oi_c"] + o["oi_p"]
    o["vol_c"] = int(sum(x["vol"] for x in ch if x["cp"] == "C"))
    o["vol_p"] = int(sum(x["vol"] for x in ch if x["cp"] == "P"))
    o["pc_vol"] = round(o["vol_p"] / o["vol_c"] * 100) if o["vol_c"] else None
    o["pc_oi"] = round(o["oi_p"] / o["oi_c"] * 100) if o["oi_c"] else None

    exps = sorted({x["exp"] for x in ch})
    # 期限結構：每個到期日的價平隱含波動度（到期前 3 天以內的太吵，不算）
    term = []
    for e in exps:
        d = (e - today).days
        if 3 <= d <= 400:
            v = atm_iv(ch, spot, e)
            if v:
                term.append([d, v])
    o["term"] = term
    if not o.get("iv30") and len(term) >= 2:
        a = max((t for t in term if t[0] <= 30), default=term[0], key=lambda t: t[0])
        b = min((t for t in term if t[0] >= 30), default=term[-1], key=lambda t: t[0])
        o["iv30"] = round(a[1] if a[0] == b[0] else a[1] + (b[1] - a[1]) * (30 - a[0]) / (b[0] - a[0]), 2)
        o["iv30_how"] = [a, b]

    def near(days):
        c = [e for e in exps if (e - today).days >= 7]
        return min(c, key=lambda e: abs((e - today).days - days)) if c else None
    e30 = near(30)
    if e30:
        o["e30"] = e30.isoformat()
        o["atm30"] = atm_iv(ch, spot, e30)
        hp, hc = [], []
        p25, c25 = delta_iv(ch, e30, -0.25, hp), delta_iv(ch, e30, 0.25, hc)
        o["p25_how"], o["c25_how"], o["e30_days"] = hp, hc, (e30 - today).days
        p10 = delta_iv(ch, e30, -0.10)
        o["p25"], o["c25"], o["p10"] = p25, c25, p10
        o["skew"] = round(p25 - c25, 2) if p25 and c25 else None
        # 預期波動：30 天價平跨式（買權＋賣權）的價格 ÷ 股價
        legs = [x for x in ch if x["exp"] == e30]
        if legs:
            k = min({x["k"] for x in legs}, key=lambda k: abs(k - spot))
            st = sum((x["bid"] + x["ask"]) / 2 if x["ask"] else x["theo"] for x in legs if x["k"] == k)
            o["move30"] = round(st / spot * 100, 1) if st else None
            o["move30_days"] = (e30 - today).days
    e90 = near(90)
    if e90 and e90 != e30:
        o["atm90"] = atm_iv(ch, spot, e90)
    if o.get("atm30") and o.get("atm90"):
        o["term_slope"] = round(o["atm90"] - o["atm30"], 2)   # 負的＝短天期比較貴（恐慌）

    # 最大痛點：最近的月結算日（每月第三個星期五）
    monthly = [e for e in exps if e == third_friday(e.year, e.month) and (e - today).days >= 1]
    if monthly:
        em = monthly[0]
        legs = [x for x in ch if x["exp"] == em and x["oi"]]
        ks = sorted({x["k"] for x in legs})
        if ks:
            def pain(s):
                return sum(x["oi"] * (max(0, s - x["k"]) if x["cp"] == "C" else max(0, x["k"] - s)) for x in legs)
            o["maxpain"] = min(ks, key=pain)
            o["maxpain_exp"] = em.isoformat()

    # 各履約價：未平倉（全部到期日加總）與今天成交量；只看現價 ±18%
    lo, hi = spot * 0.82, spot * 1.18
    strikes = {}
    for x in ch:
        if lo <= x["k"] <= hi:
            s = strikes.setdefault(x["k"], [0, 0, 0, 0])   # 買權未平倉、賣權未平倉、買權量、賣權量
            i = 0 if x["cp"] == "C" else 1
            s[i] += x["oi"]
            s[i + 2] += x["vol"]
    o["strikes"] = [[k] + [int(v) for v in s] for k, s in sorted(strikes.items())]
    below = [(k, s[1]) for k, s in strikes.items() if k <= spot]
    above = [(k, s[0]) for k, s in strikes.items() if k >= spot]
    if below:
        o["put_wall"] = max(below, key=lambda t: t[1])[0]
    if above:
        o["call_wall"] = max(above, key=lambda t: t[1])[0]
    pv = [(k, s[3]) for k, s in strikes.items() if k <= spot and s[3]]
    if pv:
        o["put_hot"] = max(pv, key=lambda t: t[1])[0]
    cv = [(k, s[2]) for k, s in strikes.items() if k >= spot and s[2]]
    if cv:
        o["call_hot"] = max(cv, key=lambda t: t[1])[0]
    return o


def vprofile(bars, nbin=28):
    """成交量分佈：把每根 K 棒的量平均分到最低～最高價之間，累加成各價位的量"""
    bars = [(b["h"], b["l"], b["v"]) for b in bars if b["h"] and b["l"] and b["v"] and b["h"] >= b["l"]]
    if len(bars) < 20:
        return None
    lo, hi = min(b[1] for b in bars), max(b[0] for b in bars)
    step = nice_step((hi - lo) / nbin)
    lo = math.floor(lo / step) * step
    n = max(1, math.ceil((hi - lo) / step))
    vol = [0.0] * n
    for h, l, v in bars:
        a, b = min(n - 1, int((l - lo) / step)), min(n - 1, int((h - lo) / step))
        for i in range(a, b + 1):
            vol[i] += v / (b - a + 1)
    poc = max(range(n), key=lambda i: vol[i])
    tot, inc, i0, i1 = sum(vol), vol[poc], poc, poc
    while inc < tot * 0.7 and (i0 > 0 or i1 < n - 1):
        up = vol[i1 + 1] if i1 < n - 1 else -1
        dn = vol[i0 - 1] if i0 > 0 else -1
        if up >= dn:
            i1 += 1; inc += up
        else:
            i0 -= 1; inc += dn
    p = lambda i: round(lo + step * i, 2)
    return {"lo": round(lo, 2), "step": step, "vol": [round(x) for x in vol], "poc": p(poc + 0.5),
            "val": p(i0), "vah": p(i1 + 1)}


def nodes(vp):
    """成交量的「山峰」（比左右兩邊都多、而且至少是最高峰的 45%）"""
    v = vp["vol"]
    sm = [(v[max(0, i - 1)] + 2 * v[i] + v[min(len(v) - 1, i + 1)]) / 4 for i in range(len(v))]
    mx = max(sm) or 1
    out = []
    for i in range(len(v)):
        l = sm[i - 1] if i > 0 else -1
        r = sm[i + 1] if i < len(v) - 1 else -1
        if sm[i] >= l and sm[i] >= r and sm[i] >= 0.45 * mx:
            out.append({"lo": round(vp["lo"] + vp["step"] * i, 2), "hi": round(vp["lo"] + vp["step"] * (i + 1), 2),
                        "w": round(sm[i] / mx, 2)})
    return out


def floors(price, vp, vp_y, o):
    """地板：現價下方的成交量山峰，再看選擇權賣權有沒有在同一個價位堆積（兩個重疊＝比較可靠）"""
    cands = []
    for src, p in (("3 個月成交密集", vp), ("一年成交密集", vp_y)):
        if not p:
            continue
        for nd in nodes(p):
            mid = (nd["lo"] + nd["hi"]) / 2
            if mid < price * 0.995 and mid > price * 0.75:
                cands.append({"lo": nd["lo"], "hi": nd["hi"], "mid": round(mid, 2), "w": nd["w"], "src": [src]})
    pw0 = (o or {}).get("put_wall")
    if pw0 and price * 0.75 < pw0 < price * 0.995 and not any(c["lo"] * 0.985 <= pw0 <= c["hi"] * 1.015 for c in cands):
        st = (vp or vp_y or {}).get("step") or pw0 * 0.01
        cands.append({"lo": pw0 - st / 2, "hi": pw0 + st / 2, "mid": pw0, "w": 0.3, "src": []})
    # 合併很接近的候選（3% 以內）
    cands.sort(key=lambda c: -c["mid"])
    merged = []
    for c in cands:
        if merged and abs(merged[-1]["mid"] - c["mid"]) / c["mid"] < 0.03:
            m = merged[-1]
            m["lo"], m["hi"] = min(m["lo"], c["lo"]), max(m["hi"], c["hi"])
            m["mid"] = round((m["lo"] + m["hi"]) / 2, 2)
            m["w"] = round(min(1.6, max(m["w"], c["w"]) + 0.25), 2)
            m["src"] = sorted(set(m["src"] + c["src"]))
        else:
            merged.append(dict(c))
    pw, ph = (o or {}).get("put_wall"), (o or {}).get("put_hot")
    strikes = {s[0]: s for s in (o or {}).get("strikes") or []}
    put_oi_max = max((s[2] for k, s in strikes.items() if k <= price), default=0) or 1
    for m in merged:
        score = m["w"]
        band = (m["lo"] * 0.985, m["hi"] * 1.015)
        oi_here = sum(s[2] for k, s in strikes.items() if band[0] <= k <= band[1])
        if pw and band[0] <= pw <= band[1]:
            score += 0.8
            m["src"].append(f"賣權未平倉最多 ${pw:g}")
        elif oi_here >= 0.5 * put_oi_max:
            score += 0.4
            m["src"].append("賣權未平倉偏多")
        if ph and band[0] <= ph <= band[1]:
            score += 0.3
            m["src"].append(f"今天賣權最熱 ${ph:g}")
        m["score"] = round(score, 2)
        m["stars"] = 3 if score >= 1.8 else 2 if score >= 1.1 else 1
        m["dist"] = round((price / m["hi"] - 1) * 100, 1)
    # 第一道地板：最近的一個，但太弱（一顆星）而下面有更強的、而且差不到 4%，就用強的
    merged = [m for m in merged if m["score"] >= 0.6][:4] or merged[:2]
    return merged


def rv(closes, n=20, detail=False):
    """實際波動：近 n 個交易日每天漲跌幅（取對數）的標準差 × √252（一年約 252 個交易日），換成年化百分比"""
    r = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - n, len(closes)) if i > 0]
    if len(r) <= 5:
        return None
    v = round(statistics.pstdev(r) * math.sqrt(252) * 100, 1)
    if detail:
        return {"rv": v, "sd": round(statistics.pstdev(r) * 100, 2), "max": round(max(r) * 100, 1), "min": round(min(r) * 100, 1),
                "big": sum(1 for x in r if abs(x) >= 0.03)}
    return v


# ───────────────────────── 判讀 ─────────────────────────

def read(s, prev, hist):
    """白話解讀，回傳 [(好/壞/中, 文字)]"""
    o, out = s.get("opt") or {}, []
    f = s["floors"][0] if s["floors"] else None
    if f:
        stars = "★" * f["stars"]
        if s["price"] < f["lo"]:
            out.append(("bad", f"收在第一道地板 ${f['lo']:,.0f}–{f['hi']:,.0f} 下方，地板暫時失守，下一道看 ${s['floors'][1]['hi']:,.0f}" if len(s["floors"]) > 1 else f"收在地板 ${f['lo']:,.0f} 下方，地板暫時失守"))
        elif f["dist"] <= 3:
            out.append(("mid", f"正在測試地板 ${f['lo']:,.0f}–{f['hi']:,.0f}（{stars}），只差 {f['dist']:.1f}%。守住的話常會反彈，跌破要小心"))
        else:
            out.append(("good", f"離第一道地板 ${f['lo']:,.0f}–{f['hi']:,.0f}（{stars}）還有 {f['dist']:.1f}%"))
    sk, skp = o.get("skew"), (prev.get("skew") if prev else None)
    if sk is not None:
        if skp is not None and sk - skp >= 1:
            out.append(("bad", f"偏斜（賣權比買權貴多少）從 {skp:.1f} 升到 {sk:.1f}：保險變貴，大家在買下跌保護"))
        elif skp is not None and sk - skp <= -1:
            out.append(("good", f"偏斜從 {skp:.1f} 降到 {sk:.1f}：搶保險的人變少，恐慌在退"))
        else:
            out.append(("mid", f"偏斜 {sk:.1f}（25 Delta 賣權減買權的隱含波動度）" + (f"，和前一天差不多" if skp is not None else "")))
    if o.get("term_slope") is not None and o["term_slope"] < -1:
        out.append(("bad", f"短天期隱含波動度比 3 個月還高 {abs(o['term_slope']):.1f}（期限結構倒掛）：市場在為近期的事件或下跌付高價"))
    if o.get("iv30") and s.get("rv20"):
        gap = o["iv30"] - s["rv20"]
        if gap > 8:
            out.append(("mid", f"隱含波動度 {o['iv30']:.0f}% 比實際波動 {s['rv20']:.0f}% 高很多：選擇權偏貴，市場預期接下來會晃更大（可能有財報或事件）"))
        elif gap < -5:
            out.append(("mid", f"隱含波動度 {o['iv30']:.0f}% 低於實際波動 {s['rv20']:.0f}%：最近跌漲很大但選擇權沒跟著變貴，市場覺得會平靜下來"))
    if o.get("pc_vol") is not None:
        pc = o["pc_vol"]
        out.append(("bad" if pc >= 110 else "good" if pc <= 60 else "mid",
                    f"今天賣權/買權成交量 {pc}%" + ("：買保險的量比買上漲的多" if pc >= 110 else "：大多數人在押上漲" if pc <= 60 else "")))
    if o.get("oi") and prev and prev.get("oi"):
        ch = (o["oi"] / prev["oi"] - 1) * 100
        dpo = (o["oi_p"] - prev.get("oi_p", o["oi_p"]))
        dco = (o["oi_c"] - prev.get("oi_c", o["oi_c"]))
        if abs(ch) >= 2:
            who = "賣權" if abs(dpo) > abs(dco) else "買權"
            out.append(("bad" if who == "賣權" and dpo > 0 else "mid",
                        f"總未平倉比前一天 {ch:+.1f}%，變動主要來自{who}（{dpo:+,} / {dco:+,} 口）"))
    if o.get("call_wall"):
        out.append(("mid", f"上方壓力：買權未平倉最多在 ${o['call_wall']:g}，離現價 {(o['call_wall'] / s['price'] - 1) * 100:+.1f}%"))
    if o.get("move30"):
        out.append(("mid", f"選擇權預期 {o.get('move30_days', 30)} 天內會上下動 ±{o['move30']:.1f}%（約 ${s['price'] * (1 - o['move30'] / 100):,.0f}–{s['price'] * (1 + o['move30'] / 100):,.0f}）"))
    return out


def status(s):
    f = s["floors"][0] if s["floors"] else None
    if not f:
        return "—", "mid"
    if s["price"] < f["lo"]:
        return "跌破地板", "bad"
    if f["dist"] <= 3:
        return "測試地板", "mid"
    return "地板上方", "good"


# ───────────────────────── 訊號紀錄 ─────────────────────────

def hist_days():
    d = os.path.join(ROOT, "data", "us_history")
    os.makedirs(d, exist_ok=True)
    return sorted(f[:-5] for f in os.listdir(d) if f.endswith(".json"))


def snap(s):
    o = s.get("opt") or {}
    f = s["floors"][0] if s["floors"] else {}
    return {"p": s["price"], "lo": s.get("low"), "oi": o.get("oi"), "oi_c": o.get("oi_c"), "oi_p": o.get("oi_p"),
            "iv30": o.get("iv30"), "atm30": o.get("atm30"), "atm90": o.get("atm90"), "skew": o.get("skew"),
            "pc_vol": o.get("pc_vol"), "floor": [f.get("lo"), f.get("hi"), f.get("stars")] if f else None,
            "term": o.get("term")}


def track(stocks, date, after_close):
    days = [d for d in hist_days() if d < date]
    past = {d: load(f"data/us_history/{d}.json", {}) for d in days[-30:]}
    for s in stocks:
        s["hist"] = [[d, past[d][s["sym"]]] for d in days[-30:] if s["sym"] in past[d]]
        s["prev"] = s["hist"][-1][1] if s["hist"] else None
    if after_close:
        save(f"data/us_history/{date}.json", {s["sym"]: snap(s) for s in stocks})

    sig = load("data/us_signals.json", [])
    by = {s["sym"]: s for s in stocks}
    alld = hist_days()
    for x in sig:   # 回填訊號之後 5／20／60 個交易日的漲跌
        if x["date"] in alld:
            i = alld.index(x["date"])
            for n in (5, 20, 60):
                if x.get(f"d{n}") is None and i + n < len(alld):
                    px = load(f"data/us_history/{alld[i + n]}.json", {}).get(x["sym"], {}).get("p")
                    if px:
                        x[f"d{n}"] = round((px / x["price"] - 1) * 100, 1)
        if x["sym"] in by:
            x["now"] = round((by[x["sym"]]["price"] / x["price"] - 1) * 100, 1)

    new = []
    if after_close:
        recent = {(x["sym"], x["type"]) for x in sig if x["date"] >= (dt.date.fromisoformat(date) - dt.timedelta(days=20)).isoformat()}
        for s in stocks:
            o, h = s.get("opt") or {}, s["hist"]
            f = s["floors"][0] if s["floors"] else None
            if not f or not o or len(h) < 2:
                continue   # 至少要有兩天的紀錄才比較得出變化
            prev, prev2 = h[-1][1], h[-2][1]
            sk, sk1, sk2 = o.get("skew"), prev.get("skew"), prev2.get("skew")
            pc, pc1 = o.get("pc_vol"), prev.get("pc_vol")
            kind, why = None, []
            # 守住地板：碰到（或很接近）一個兩星以上的地板、收盤還在上面，而且恐慌連續兩天退燒
            touched = (s.get("low") or s["price"]) <= f["hi"] * 1.01 and s["price"] >= f["lo"]
            if (touched and f["stars"] >= 2 and None not in (sk, sk1, sk2) and sk < sk1 < sk2 and sk2 - sk >= 1.5
                    and pc is not None and pc1 is not None and pc < pc1):
                kind = "守住地板"
                why = [f"盤中碰到地板 ${f['lo']:,.0f}–{f['hi']:,.0f}（{'★' * f['stars']}：{'、'.join(f['src'])}），收盤守住",
                       f"偏斜連兩天下降：{sk2:.1f} → {sk1:.1f} → {sk:.1f}（買保險的人變少）",
                       f"賣權/買權成交量 {pc1}% → {pc}%"]
            # 跌破地板：連續兩天收在兩星以上地板下方 2%，而且偏斜上升、賣權未平倉增加
            pf = prev.get("floor") or [None, None, 0]
            if (not kind and f["stars"] >= 2 and s["price"] < f["lo"] * 0.98 and pf[0] and prev["p"] < pf[0] * 0.98
                    and None not in (sk, sk1) and sk > sk1 and o.get("oi_p", 0) > (prev.get("oi_p") or 1e18)):
                kind = "跌破地板"
                why = [f"連續兩天收在地板 ${f['lo']:,.0f} 下方 2% 以上",
                       f"偏斜上升 {sk1:.1f} → {sk:.1f}，賣權未平倉增加 {o['oi_p'] - prev['oi_p']:+,} 口",
                       (f"下一道地板 ${s['floors'][1]['lo']:,.0f}–{s['floors'][1]['hi']:,.0f}" if len(s["floors"]) > 1 else "下方暫時沒有明顯的成交密集區")]
            if not kind or (s["sym"], kind) in recent:
                continue
            x = {"id": f"{date}-{s['sym']}-{kind}", "date": date, "sym": s["sym"], "name": s["zh"], "type": kind,
                 "price": s["price"], "floor": [f["lo"], f["hi"]], "stars": f["stars"], "skew": sk, "iv30": o.get("iv30"),
                 "pc_vol": pc, "oi": o.get("oi"), "reasons": why}
            sig.append(x)
            new.append(x)
    save("data/us_signals.json", sig)
    return sig, new


def send_telegram(text):
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not chat:
        p = os.path.join(ROOT, "data", "chat_id")
        chat = open(p).read().strip() if os.path.exists(p) else ""
    if not token or not chat:
        print("telegram not configured")
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": text, "parse_mode": "HTML",
                                   "disable_web_page_preview": "true"}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=20)
    except Exception as e:
        print("telegram error:", e, file=sys.stderr)


def push(new, date, url):
    e = html.escape
    for x in new:
        icon = "🟢 守住地板" if x["type"] == "守住地板" else "🔻 跌破地板"
        lines = [f"<b>{icon}｜{e(x['name'])} {x['sym']}</b>", f"{date[5:].replace('-', '/')} 美股收盤 ${x['price']:,.2f}", ""]
        lines += [f"・{e(t)}" for t in x["reasons"]]
        lines += ["", f'<a href="{url}usstocks.html#{x["sym"].replace("/", "")}">看完整分析</a>　（分析結果，不是投資建議）']
        send_telegram("\n".join(lines))
        time.sleep(1)


# ───────────────────────── 網頁 ─────────────────────────

E = html.escape


def money(v):
    return "—" if v is None else f"${v:,.2f}" if v < 1000 else f"${v:,.0f}"


def big(v):
    if v is None:
        return "—"
    for n, u in ((1e12, " 兆"), (1e8, " 億"), (1e4, " 萬")):
        if abs(v) >= n:
            x = v / n
            return f"{x:,.1f}{u}" if abs(x) < 1000 else f"{x:,.0f}{u}"
    return f"{v:,.0f}"


def tone(v):
    return "" if v is None else "up" if v > 0 else "down" if v < 0 else ""


def spark(vals, w=110, h=26, cls="spark"):
    vals = [v for v in vals if v is not None]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    rg = hi - lo or 1
    pts = " ".join(f"{i / (len(vals) - 1) * w:.1f},{h - 2 - (v - lo) / rg * (h - 4):.1f}" for i, v in enumerate(vals))
    x, y = pts.split(" ")[-1].split(",")
    return (f'<svg class="{cls}" viewBox="0 0 {w} {h}" preserveAspectRatio="none"><polyline points="{pts}" fill="none" '
            f'stroke="currentColor" stroke-width="1.4" vector-effect="non-scaling-stroke"/><circle class="hi" cx="{x}" cy="{y}" r="2.6"/></svg>')


def price_svg(daily, floors_, o):
    """近 6 個月股價線，加上地板區（銅色帶）與上方買權牆（虛線）"""
    d = daily[-126:]
    if len(d) < 10:
        return ""
    W, H, L, R = 340, 150, 4, 46
    cs = [b["c"] for b in d]
    ys = cs + [f["lo"] for f in floors_[:2]] + ([o["call_wall"]] if o and o.get("call_wall") and o["call_wall"] < max(cs) * 1.15 else [])
    lo, hi = min(ys) * 0.985, max(ys) * 1.015
    Y = lambda v: H - 14 - (v - lo) / (hi - lo) * (H - 22)
    X = lambda i: L + i / (len(d) - 1) * (W - L - R)
    out = f'<svg class="pchart" viewBox="0 0 {W} {H}" role="img" aria-label="近六個月股價與地板">'
    for j, f in enumerate(floors_[:2]):
        y1, y2 = Y(f["hi"]), Y(f["lo"])
        out += (f'<rect class="fz f{j}" x="{L}" y="{y1:.1f}" width="{W - L - R}" height="{max(2, y2 - y1):.1f}"/>'
                f'<text class="fl" x="{W - R + 4}" y="{(y1 + y2) / 2 + 3:.1f}">{"地板" if j == 0 else "地板②"}</text>')
    if o and o.get("call_wall") and lo < o["call_wall"] < hi:
        y = Y(o["call_wall"])
        out += f'<line class="cw" x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}"/><text class="fl cwl" x="{W - R + 4}" y="{y + 3:.1f}">壓力</text>'
    pts = " ".join(f"{X(i):.1f},{Y(c):.1f}" for i, c in enumerate(cs))
    out += f'<polyline class="pl" points="{pts}"/>'
    out += f'<circle class="pd" cx="{X(len(cs) - 1):.1f}" cy="{Y(cs[-1]):.1f}" r="3.2"/>'
    for i in (0, len(d) // 2, len(d) - 1):
        out += f'<text class="ax" x="{X(i):.1f}" y="{H - 2}" text-anchor="{"start" if i == 0 else "end" if i == len(d) - 1 else "middle"}">{d[i]["d"][5:].replace("-", "/")}</text>'
    for v in (min(cs), max(cs)):
        out += f'<text class="ax" x="{W - R + 4}" y="{Y(v) + 3:.1f}">{v:,.0f}</text>' if not any(abs(Y(v) - Y((f["lo"] + f["hi"]) / 2)) < 12 for f in floors_[:2]) else ""
    return out + "</svg>"


def vp_html(s):
    """左：3 個月成交量分佈；右：同一價格區間的選擇權未平倉（霧藍賣權、銅色買權）"""
    vp, o, px = s.get("vp"), s.get("opt") or {}, s["price"]
    if not vp:
        return ""
    step, n = vp["step"], len(vp["vol"])
    # 只顯示現價上下各 ~25% 範圍，避免太長
    rows_idx = [i for i in range(n) if vp["lo"] + step * (i + 1) >= px * 0.78 and vp["lo"] + step * i <= px * 1.2]
    if not rows_idx:
        return ""
    mx = max(vp["vol"][i] for i in rows_idx) or 1
    oi = [[0, 0] for _ in range(n)]
    for k, coi, poi, _, _ in o.get("strikes") or []:
        i = int((k - vp["lo"]) // step)
        if 0 <= i < n:
            oi[i][0] += coi
            oi[i][1] += poi
    omx = max((max(oi[i]) for i in rows_idx), default=0) or 1
    fl = s["floors"][0] if s["floors"] else None
    fl2 = s["floors"][1] if len(s["floors"]) > 1 else None
    rows = ""
    for i in reversed(rows_idx):
        lo, hi = vp["lo"] + step * i, vp["lo"] + step * (i + 1)
        here = lo <= px < hi
        ispoc = lo <= vp["poc"] < hi
        inva = vp["val"] <= lo + 1e-6 and hi <= vp["vah"] + 1e-6
        isf = fl and lo < fl["hi"] and hi > fl["lo"]
        isf2 = fl2 and lo < fl2["hi"] and hi > fl2["lo"]
        c, p = oi[i]
        tag = "◀ 現在" if here else "地板" if isf else "地板②" if isf2 else "最密集" if ispoc else ""
        rows += (f'<div class="vp-r two{" here" if here else ""}{" fl" if isf or isf2 else ""}"><span>{lo:,.0f}</span>'
                 f'<div><i class="{"poc" if ispoc else "va" if inva else ""}" style="width:{vp["vol"][i] / mx * 100:.0f}%"></i></div>'
                 f'<div class="ob"><i class="p" style="width:{p / omx * 100:.0f}%"></i><i class="c" style="width:{c / omx * 100:.0f}%"></i></div>'
                 f'<em>{tag}</em></div>')
    return (f'<h4>成交量分佈 × 選擇權未平倉</h4>'
            f'<p class="note2">每一列是一個價格區間（每格 ${step:g}，左邊是區間起點）。左欄是<b>近 3 個月在這個價位成交了多少股</b>，越長代表越多人在這裡買賣、成本在這裡；'
            f'<b style="color:var(--copper)">銅色</b>是最密集的價位 ${vp["poc"]:,.0f}，<b>深霧藍</b>是 70% 成交量的區間 ${vp["val"]:,.0f}–{vp["vah"]:,.0f}。'
            f'右欄是同一價位的<b>選擇權未平倉</b>（<b style="color:var(--fogdeep)">霧藍</b>賣權、<b style="color:var(--copper)">銅色</b>買權，所有到期日加總）。'
            f'現價下方「成交很多」又「賣權堆很多」的地方，就是最可靠的地板。</p>'
            f'<div class="vp-h"><span></span><span>股票成交量</span><span>選擇權未平倉</span><span></span></div><div class="vp">{rows}</div>')


def strike_vol_html(s):
    o = s.get("opt") or {}
    st = [x for x in o.get("strikes") or [] if x[3] or x[4]]
    if not st:
        return ""
    px = s["price"]
    st = sorted(st, key=lambda x: abs(x[0] - px))[:16]
    st.sort(key=lambda x: -x[0])
    mx = max(max(x[3], x[4]) for x in st) or 1
    rows, placed = "", False
    for k, coi, poi, cv, pv in st:
        if not placed and k < px:
            rows += f'<div class="oi-now"><span>現價 {money(px)}</span></div>'
            placed = True
        hot = "熱" if k in (o.get("put_hot"), o.get("call_hot")) else ""
        rows += (f'<div class="oi-r"><div class="l"><i style="width:{pv / mx * 100:.0f}%"></i></div>'
                 f'<div class="k">{k:g}{f"<em>{hot}</em>" if hot else ""}</div>'
                 f'<div class="r"><i style="width:{cv / mx * 100:.0f}%"></i></div></div>')
    if not placed:
        rows += f'<div class="oi-now"><span>現價 {money(px)}</span></div>'
    return (f'<h4>今天各履約價的成交量</h4><p class="note2">靠近現價的 16 個履約價、所有到期日加總。'
            f'左邊霧藍是<b>賣權</b>（押跌或買保險），右邊銅色是<b>買權</b>（押漲）。今天賣權成交 {o.get("vol_p", 0):,} 口、買權 {o.get("vol_c", 0):,} 口。</p>'
            f'<div class="oi-h"><span>← 賣權成交量</span><span>買權成交量 →</span></div><div class="oi">{rows}</div>')


def term_svg(s):
    o = s.get("opt") or {}
    t = [x for x in o.get("term") or [] if x[0] <= 400]
    if len(t) < 3:
        return ""
    prev = (s.get("prev") or {}).get("term") or []
    W, H, L, B = 320, 120, 30, 18
    allv = [v for _, v in t] + [v for _, v in prev]
    lo, hi = min(allv) - 1, max(allv) + 1
    mxd = max(d for d, _ in t)
    X = lambda d: L + math.sqrt(d) / math.sqrt(mxd) * (W - L - 8)
    Y = lambda v: 6 + (hi - v) / (hi - lo) * (H - B - 6)
    out = f'<svg class="term" viewBox="0 0 {W} {H}" role="img" aria-label="隱含波動度期限結構">'
    for v in (lo + 1, (lo + hi) / 2, hi - 1):
        out += f'<line class="g" x1="{L}" x2="{W - 4}" y1="{Y(v):.1f}" y2="{Y(v):.1f}"/><text class="ax" x="{L - 4}" y="{Y(v) + 3:.1f}" text-anchor="end">{v:.0f}%</text>'
    for d in (7, 30, 90, 180, 365):
        if d <= mxd:
            out += f'<text class="ax" x="{X(d):.1f}" y="{H - 4}" text-anchor="middle">{d}天</text>'
    if len(prev) >= 3:
        pp = " ".join(f"{X(d):.1f},{Y(v):.1f}" for d, v in prev if d <= mxd)
        out += f'<polyline class="tp" points="{pp}"/>'
    pts = " ".join(f"{X(d):.1f},{Y(v):.1f}" for d, v in t)
    out += f'<polyline class="tl" points="{pts}"/>' + "".join(f'<circle class="td" cx="{X(d):.1f}" cy="{Y(v):.1f}" r="2.2"/>' for d, v in t)
    return out + "</svg>"


def how_box(paras):
    return '<details class="how2"><summary>怎麼算的</summary>' + "".join(f"<p>{p}</p>" for p in paras) + "</details>"


def how_oi(o):
    return [f"把這檔<b>所有到期日、所有履約價</b>還沒結清的選擇權口數加起來：買權 {o.get('oi_c', 0):,} 口＋賣權 {o.get('oi_p', 0):,} 口。",
            "未平倉是交易所每天結算後公布的，所以永遠是<b>前一個交易日收盤</b>的數字。一口選擇權通常代表 100 股。",
            "增加＝有新的人進場押注或避險；減少＝有人平倉或合約到期。看變化主要來自買權還是賣權，就知道新增的是押漲還是買保險。"]


def how_iv(o, s):
    src = o.get("src")
    out = ["<b>隱含波動度（IV）</b>是從選擇權的價格反推出來的：市場願意付多少錢買選擇權，就代表它覺得股價未來會晃多大。"
           f"{o['iv30']:.1f}% 的意思是：市場預期未來一年股價的波動幅度（一個標準差）大約是 ±{o['iv30']:.0f}%，換算成一個月大約 ±{o['iv30'] / math.sqrt(12):.1f}%。"]
    if src == "Cboe":
        out.append("這個數字直接用 Cboe 公布的 IV30（他們用所有到期日內插出剛好 30 天的值）。")
    else:
        out.append("步驟：① 每個到期日，找最接近現價的<b>兩個</b>履約價（價平），每個履約價的買權和賣權都用，共四個合約；價格用買賣價中間值（買賣價差太大時用最後成交價）。"
                   f"② 用 Black-Scholes 公式（現價 ${s['price']:,.2f}、履約價、剩餘天數、利率 4%）反推出讓公式算出來的價格剛好等於市價的波動度，四個 IV 取平均，就是那個到期日的價平 IV（理論上同一個履約價的買權、賣權 IV 應該一樣，平均起來比較穩）。")
        hw = o.get("iv30_how")
        if hw and hw[0][0] != hw[1][0]:
            (d1, v1), (d2, v2) = hw
            out.append(f"③ 剛好 30 天沒有合約，所以用前後兩個到期日內插：{d1} 天 {v1:.1f}%、{d2} 天 {v2:.1f}%，"
                       f"按天數比例 {v1:.1f} + ({v2:.1f} − {v1:.1f}) × (30 − {d1}) ÷ ({d2} − {d1}) = <b>{o['iv30']:.1f}%</b>。")
        elif hw:
            out.append(f"③ 剛好有約 {hw[0][0]} 天到期的合約，直接用它的 {hw[0][1]:.1f}%。")
    return out


def how_skew(o, s):
    d = o.get("e30_days")
    out = [f"<b>偏斜</b>比較的是「下跌保險」和「上漲押注」哪個比較貴。用最接近 30 天的到期日（{(o.get('e30') or '')[5:].replace('-', '/')}，剩 {d} 天）。",
           "<b>Delta</b> 可以想成「這個選擇權到期時變成有價值的大概機率」。Delta −0.25 的賣權＝跌到那裡的機率約 25% 的下跌保險；Delta +0.25 的買權＝漲到那裡機率約 25% 的上漲押注。兩邊離現價的「距離」差不多，所以可以公平比較。"]

    def legs(h, name):
        if not h:
            return ""
        if len(h) == 2:
            (k1, d1, v1), (k2, d2, v2) = h
            return f"{name}：履約價 ${k1:g}（Delta {d1:+.2f}，IV {v1:.1f}%）和 ${k2:g}（Delta {d2:+.2f}，IV {v2:.1f}%）之間按 Delta 內插"
        k1, d1, v1 = h[0]
        return f"{name}：履約價 ${k1:g}（Delta {d1:+.2f}），IV {v1:.1f}%"
    out.append(legs(o.get("p25_how"), "Delta −0.25 的賣權") + f" → <b>{o['p25']:.1f}%</b>。")
    out.append(legs(o.get("c25_how"), "Delta +0.25 的買權") + f" → <b>{o['c25']:.1f}%</b>。")
    out.append(f"偏斜 = {o['p25']:.1f} − {o['c25']:.1f} = <b>{o['skew']:.1f}</b>（單位是波動度百分點）。"
               "越大代表大家越願意花大錢買下跌保險（怕跌）。絕對值每檔不同，所以<b>每天的變化</b>比絕對值重要：連續上升＝恐慌升溫，連續下降＝恐慌退燒。")
    return out


def how_rv(o, s):
    h = s.get("rv_how") or {}
    out = ["<b>實際波動</b>是股價過去真的晃了多大：取近 20 個交易日每天的漲跌幅，算它們的標準差，再乘以 √252（一年大約 252 個交易日）換成跟 IV 一樣的年化單位。"]
    if h:
        out.append(f"這檔近 20 天：每天漲跌的標準差 {h['sd']:.2f}%，× √252（≈15.9）= <b>{h['rv']:.1f}%</b>。"
                   f"期間單日最大漲 {h['max']:+.1f}%、最大跌 {h['min']:+.1f}%，有 {h['big']} 天漲跌超過 3%。")
    gap = o["iv30"] - s["rv20"]
    out.append(f"隱含 − 實際 = {o['iv30']:.1f} − {s['rv20']:.1f} = <b>{gap:+.1f}</b>。"
               + ("正的＝選擇權比最近實際的晃動還貴，市場預期接下來會更動盪（常見於財報、重大事件前）。" if gap > 0 else
                  "負的＝最近股價晃很大，但選擇權沒有跟著變貴，市場覺得接下來會比較平靜。"))
    return out


def detail(s):
    o = s.get("opt") or {}
    h = s.get("hist") or []
    rs = "".join(f'<li class="{k}"><i>{"▲" if k == "good" else "▼" if k == "bad" else "•"}</i><span>{E(t)}</span></li>' for k, t in s["read"])
    fl = ""
    for j, f in enumerate(s["floors"][:3]):
        fl += (f'<div class="fr{" first" if j == 0 else ""}"><b>{"第一道" if j == 0 else "第二道" if j == 1 else "第三道"}</b>'
               f'<span class="rg">${f["lo"]:,.0f}–{f["hi"]:,.0f}</span><span class="st">{"★" * f["stars"]}{"☆" * (3 - f["stars"])}</span>'
               f'<span class="ds">{"已跌破" if s["price"] < f["lo"] else f"距 {f["dist"]:.1f}%"}</span>'
               f'<small>{E("、".join(f["src"]))}</small></div>')
    iv_hist = [x[1].get("iv30") for x in h] + [o.get("iv30")]
    sk_hist = [x[1].get("skew") for x in h] + [o.get("skew")]
    oi_hist = [x[1].get("oi") for x in h] + [o.get("oi")]
    prev = s.get("prev") or {}

    def chg(cur, old, fmt="{:+.1f}", pct=False):
        if cur is None or old is None:
            return '<small>明天起顯示變化</small>' if not h else ""
        v = (cur / old - 1) * 100 if pct else cur - old
        return f'<small class="{tone(v)}">比前一天 {fmt.format(v)}{"%" if pct else ""}</small>'

    stats = (f'<div><span class="lbl">選擇權總未平倉</span><b>{o.get("oi", 0):,}</b><small>口</small> {chg(o.get("oi"), prev.get("oi"), "{:+.1f}", True)}{spark(oi_hist)}'
             f'{how_box(how_oi(o))}</div>')
    if o.get("iv30") is not None:
        stats += (f'<div><span class="lbl">30 天隱含波動度</span><b>{o["iv30"]:.1f}%</b>'
                  + (f'<small class="{tone(o.get("iv30_chg"))}">比前一天 {o["iv30_chg"]:+.1f}</small>' if o.get("iv30_chg") is not None else "")
                  + f'{spark(iv_hist)}{how_box(how_iv(o, s))}</div>')
    if o.get("skew") is not None:
        stats += (f'<div><span class="lbl">偏斜（25Δ 賣權−買權）</span><b>{o["skew"]:.1f}</b> {chg(o.get("skew"), prev.get("skew"))}{spark(sk_hist)}'
                  f'{how_box(how_skew(o, s))}</div>')
    if o.get("iv30") and s.get("rv20"):
        stats += (f'<div><span class="lbl">隱含 − 實際波動</span><b>{o["iv30"] - s["rv20"]:+.1f}</b><small>IV {o["iv30"]:.0f}% ／ 近 20 日實際 {s["rv20"]:.0f}%</small>'
                  f'{how_box(how_rv(o, s))}</div>')
    return f'''<details class="full"><summary>完整分析</summary><div class="dt">
<h4>地板在哪裡</h4><div class="floors">{fl or "<p class=note2>近期成交量分佈裡找不到明顯的支撐。</p>"}</div>
{price_svg(s["daily"], s["floors"], o)}
<p class="note2">銅色帶是地板區，虛線是上方買權未平倉最多的價位（常是壓力）。★ 越多代表越多證據重疊：成交量山峰、賣權未平倉堆積、今天賣權最熱的履約價。</p>
<h4>白話解讀</h4><ul class="why">{rs}</ul>
<h4>選擇權數字</h4><div class="ostats">{stats}</div>
{vp_html(s)}
{strike_vol_html(s)}
<h4>隱含波動度期限結構</h4>
<p class="note2">橫軸是到期天數，縱軸是價平隱含波動度。正常是往右上升（越久越不確定）；<b>左邊翹高（倒掛）</b>代表市場在為最近的下跌或事件付高價。'''\
        + (f'30 天 {o["atm30"]:.1f}%、90 天 {o["atm90"]:.1f}%。' if o.get("atm30") and o.get("atm90") else "") \
        + ('灰色虛線是前一天。' if prev.get("term") else "") + f'''</p>{term_svg(s)}
<p class="src">選擇權資料時間：{E(o.get("asof") or "—")}・來源 {E(o.get("src", ""))}・未平倉是前一個交易日結算後的數字</p>
</div></details>'''


def card(s, i):
    o = s.get("opt") or {}
    stt, cls = status(s)
    f = s["floors"][0] if s["floors"] else None
    chips = ""
    if f:
        chips += f'<div class="chip"><span>第一道地板</span><b>${f["lo"]:,.0f}–{f["hi"]:,.0f}</b><small> {"★" * f["stars"]}</small></div>'
    if o.get("put_wall"):
        chips += f'<div class="chip"><span>賣權未平倉最多</span><b>${o["put_wall"]:g}</b></div>'
    if o.get("call_wall"):
        chips += f'<div class="chip"><span>買權未平倉最多</span><b>${o["call_wall"]:g}</b></div>'
    if o.get("maxpain"):
        chips += f'<div class="chip"><span>最大痛點 {o["maxpain_exp"][5:].replace("-", "/")}</span><b>${o["maxpain"]:g}</b></div>'
    if o.get("pc_vol") is not None:
        chips += f'<div class="chip"><span>賣權/買權成交</span><b>{o["pc_vol"]}%</b></div>'
    if o.get("move30"):
        chips += f'<div class="chip"><span>{o.get("move30_days", 30)} 天預期波動</span><b>±{o["move30"]:.1f}%</b></div>'
    no_opt = "" if o else '<p class="note2" style="padding:0 18px">今天選擇權報價來源（Cboe、Nasdaq）都沒有回應，這檔先只用成交量分佈判斷，下一次更新會再抓。</p>'
    return f'''<article class="card stock {cls}" id="{s["sym"].replace("/", "")}" data-code="{E(s["sym"])}" data-name="{E(s["zh"] + " " + s["name"])}">
<div class="head"><div class="no">{i}</div><div class="nm"><h3>{E(s["zh"])} <em>{E(s["sym"])}</em></h3>
<div class="ind"><span class="state {cls}">{stt}</span><span>市值 {big(s["cap"])} 美元・{E(s["en"])}</span></div></div>
<div class="px"><div class="dd">{money(s["price"])}</div><div class="p {tone(s["chg"])}">{s["chg"]:+.2f}%・距一年高點 {s["dd"]:+.1f}%</div></div></div>
<div class="chips">{chips}</div>{no_opt}
{detail(s)}</article>'''


def overview(stocks, mkt, sig):
    rows = ""
    for i, s in enumerate(stocks):
        o = s.get("opt") or {}
        stt, cls = status(s)
        f = s["floors"][0] if s["floors"] else None
        prev = s.get("prev") or {}
        dsk = (o["skew"] - prev["skew"]) if o.get("skew") is not None and prev.get("skew") is not None else None
        doi = (o["oi"] / prev["oi"] - 1) * 100 if o.get("oi") and prev.get("oi") else None
        rows += (f'<tr><th><a href="#{s["sym"].replace("/", "")}" data-go="stocks" data-to="{s["sym"].replace("/", "")}">{E(s["zh"])} <em>{E(s["sym"])}</em></a>'
                 f'<small>{money(s["price"])} <span class="{tone(s["chg"])}">{s["chg"]:+.1f}%</span></small></th>'
                 f'<td class="{cls}">{("−" if s["price"] < f["lo"] else "") + (f"{abs(f["dist"]):.1f}%" if f else "—") if f else "—"}<small>{stt}</small></td>'
                 f'<td>{(o.get("iv30") or 0):.0f}%<small>{"" if o.get("iv30_chg") is None else f"{o["iv30_chg"]:+.1f}"}</small></td>'
                 f'<td>{"—" if o.get("skew") is None else f"{o["skew"]:.1f}"}<small class="{tone(-dsk) if dsk is not None else ""}">{"" if dsk is None else f"{dsk:+.1f}"}</small></td>'
                 f'<td>{"—" if not o.get("oi") else big(o["oi"])}<small>{"" if doi is None else f"{doi:+.1f}%"}</small></td></tr>')
    # 地板距離圖：每檔一條，地板在左、現價點、上方壓力在右
    bars = ""
    for s in stocks:
        o = s.get("opt") or {}
        f = s["floors"][0] if s["floors"] else None
        if not f:
            continue
        lo = min(f["lo"], s["price"]) * 0.97
        hi = max(o.get("call_wall") or s["price"] * 1.08, s["price"] * 1.04) * 1.03
        P = lambda v: max(0, min(100, (v - lo) / (hi - lo) * 100))
        bars += (f'<div class="gb"><a href="#{s["sym"].replace("/", "")}" data-go="stocks" data-to="{s["sym"].replace("/", "")}">{E(s["sym"])}</a><div class="tr">'
                 f'<i class="fz" style="left:{P(f["lo"]):.1f}%;width:{max(1.5, P(f["hi"]) - P(f["lo"])):.1f}%"></i>'
                 + (f'<i class="cw" style="left:{P(o["call_wall"]):.1f}%"></i>' if o.get("call_wall") else "")
                 + f'<i class="pt {status(s)[1]}" style="left:{P(s["price"]):.1f}%"></i></div></div>')
    n_test = sum(1 for s in stocks if status(s)[0] == "測試地板")
    n_break = sum(1 for s in stocks if status(s)[0] == "跌破地板")
    sk_up = [s["zh"] for s in stocks if (s.get("opt") or {}).get("skew") is not None and (s.get("prev") or {}).get("skew") is not None and s["opt"]["skew"] - s["prev"]["skew"] >= 1]
    inv = [s["zh"] for s in stocks if ((s.get("opt") or {}).get("term_slope") or 0) < -1]
    lines = [f"10 檔裡 <b>{n_test}</b> 檔正在測試地板、<b>{n_break}</b> 檔已跌破第一道地板。"]
    if sk_up:
        lines.append(f"偏斜明顯升高（保險變貴）：{E('、'.join(sk_up))}。")
    if inv:
        lines.append(f"短天期隱含波動度倒掛（市場在為近期風險付高價）：{E('、'.join(inv))}。")
    if not any(s.get("prev") for s in stocks):
        lines.append("今天是第一天紀錄，未平倉、偏斜的「變化」從下一個交易日開始會出現。")
    return f'''<section id="overview">
<div class="card ov"><h2>今天重點</h2><p class="note2">{"<br>".join(lines)}</p>
<h3 class="sub3">現價離地板多遠</h3><p class="note2">每一條是一檔股票：<b style="color:var(--copper)">銅色帶</b>是第一道地板，圓點是現在的價格，細直線是上方買權未平倉最多的價位（壓力）。點代號看完整分析。</p>
<div class="gauge">{bars}</div></div>
<div class="card ov"><h2>十檔一覽</h2><div class="tw"><table class="ind-t us"><tr><th>股票・收盤</th><th>距地板</th><th>IV30<small>變化</small></th><th>偏斜<small>變化</small></th><th>未平倉<small>變化</small></th></tr>{rows}</table></div>
<p class="note2" style="margin-top:10px">IV30＝30 天隱含波動度（選擇權認為未來一個月會晃多大）。偏斜＝下跌保險比上漲押注貴多少，<b>升高代表大家在怕跌</b>。未平倉＝還沒結清的選擇權口數。</p></div>
</section>'''


def signals_html(sig):
    if not sig:
        body = '<p class="empty">還沒有訊號。<br>這裡只放「把握比較高」的時刻（守住地板、跌破地板），不是每天都有；需要至少兩天的紀錄才會開始判斷。</p>'
    else:
        body = ""
        for x in sorted(sig, key=lambda x: x["date"], reverse=True)[:40]:
            perf = "・".join(f"{n} 日後 {x[f'd{n}']:+.1f}%" for n in (5, 20, 60) if x.get(f"d{n}") is not None)
            body += (f'<article class="card sig {"good" if x["type"] == "守住地板" else "bad"}"><div class="head"><div class="nm">'
                     f'<h3>{E(x["name"])} <em>{E(x["sym"])}</em></h3><div class="ind"><span class="tag">{x["type"]}</span><span>{x["date"]}・${x["price"]:,.2f}</span>'
                     + (f'<span class="{tone(x.get("now"))}">至今 {x["now"]:+.1f}%</span>' if x.get("now") is not None else "")
                     + f'</div></div></div><ul class="why">{"".join(f"<li class=mid><i>•</i><span>{E(t)}</span></li>" for t in x["reasons"])}</ul>'
                     + (f'<p class="note2" style="padding:0 18px 12px">{perf}</p>' if perf else "") + '</article>')
    return f'<section id="signals"><p class="lead">把握比較高的訊號才會記在這裡、推播到 Telegram，並寫進 Notion，之後追蹤 5／20／60 個交易日的表現。</p>{body}</section>'


HOW = '''<section id="how"><div class="card how">
<h2>看哪些股票</h2><p>每天抓在美國掛牌的股票市值前 10 大（Nasdaq 股票篩選器），台積電 TSM 這類外國公司的 ADR 也算。同一家公司有兩種股票時只看一檔：Alphabet 看 <b>GOOG</b>（不看 GOOGL）。</p>
<h2>地板怎麼找</h2><ol>
<li><b>成交量分佈（Volume Profile）</b>：把近 3 個月每一小時的成交量，平均分到那一小時的最低價到最高價之間，累加起來。成交特別多的價位（山峰）代表很多人的成本在這裡，跌到這裡常會有人接。也另外看一年的日線分佈。</li>
<li><b>賣權未平倉</b>：所有到期日加總，看哪個履約價的賣權（下跌保險）堆最多。很多人在這個價位買保險或賣保險，造市商避險的買盤也常在這裡出現。</li>
<li><b>今天賣權成交最熱的履約價</b>：當天大家押注的價位。</li>
</ol><p>這三個在同一個價位重疊越多，★ 越多，地板越可靠。</p>
<h2>選擇權的情緒指標</h2><ul>
<li><b>總未平倉＋變化</b>：一個總數和比前一天的變化，並看變化主要來自買權還是賣權。</li>
<li><b>30 天隱含波動度（IV30）</b>：選擇權價格反推出來、市場預期未來一個月的波動。</li>
<li><b>偏斜</b>：約 30 天到期、Delta 25 的賣權隱含波動度減掉 Delta 25 的買權。越大代表下跌保險越貴、越多人怕跌；<b>連續下降＝恐慌退燒</b>。</li>
<li><b>期限結構</b>：不同到期日的價平隱含波動度。短天期比長天期貴（倒掛）通常是市場緊張或有財報等事件。</li>
<li><b>隱含 − 實際波動</b>：隱含波動度減掉近 20 天實際波動。差很多代表選擇權偏貴。</li>
<li><b>賣權/買權成交量、最大痛點、預期波動</b>：最大痛點是月結算日讓選擇權買方整體賠最多的價格；預期波動用 30 天價平跨式價格估算。</li></ul>
<h2>什麼時候會通知</h2><ul>
<li><b>🟢 守住地板</b>：盤中碰到兩星以上的地板、收盤守住，而且偏斜連續兩天下降（合計至少 1.5）、賣權/買權成交量也下降。</li>
<li><b>🔻 跌破地板</b>：連續兩天收在兩星以上地板下方 2% 以上，而且偏斜上升、賣權未平倉增加。</li></ul>
<p>同一檔同一種訊號 20 天內只通知一次。每個訊號會記下來，之後追蹤 5／20／60 個交易日的漲跌，看這套方法準不準。</p>
<p class="src">資料：Nasdaq（市值）、Cboe 延遲報價（選擇權）、Yahoo Finance（股價）。每個美股交易日收盤後（台北時間早上）更新。</p>
<p class="disc">這是用公開數字整理出來的分析，不是投資建議。</p></div></section>'''


def render(stocks, mkt, updated, sig):
    nav = ('<a class="navlink back" href="./">← 新聞</a><button data-tab="overview">總覽</button>'
           f'<button data-tab="stocks">個股<b>{len(stocks)}</b></button>'
           f'<button data-tab="signals">訊號紀錄<b>{len(sig)}</b></button><button data-tab="how">怎麼判斷</button>')
    body = overview(stocks, mkt, sig)
    body += '<section id="stocks"><p class="lead">依市值排序。每檔先看「地板」和現在的狀態，點「完整分析」看成交量分佈、選擇權各履約價、隱含波動度。</p>' + "".join(card(s, i + 1) for i, s in enumerate(stocks)) + "</section>"
    body += signals_html(sig) + HOW
    summary = '<div class="mk">' + "".join(
        f'<div><span class="lbl">{n}</span><b>{v["last"]:,.{0 if v["last"] > 100 else 2}f}</b><small class="{tone(v["chg"]) if k != "vix" else tone(-v["chg"])}">{v["chg"]:+.2f}%</small></div>'
        for k, n, v in (("spx", "S&P 500", mkt.get("spx")), ("ndx", "那斯達克", mkt.get("ixic")), ("vix", "VIX 恐慌指數", mkt.get("vix"))) if v) + "</div>"
    tpl = open(os.path.join(ROOT, "us_template.html"), encoding="utf-8").read()
    out = tpl.replace("{{UPDATED}}", updated).replace("{{NAV}}", nav).replace("{{BODY}}", body).replace("{{SUMMARY}}", summary)
    with open(os.path.join(ROOT, "usstocks.html"), "w", encoding="utf-8") as f:
        f.write(out)


# ───────────────────────── main ─────────────────────────

def main():
    now = dt.datetime.now(TW)
    uni = universe()
    if not uni:
        sys.exit("拿不到股票清單")
    print("universe", [u["sym"] for u in uni])

    mkt = {}
    for k, sym in (("spx", "^GSPC"), ("ixic", "^IXIC"), ("vix", "^VIX")):
        d, _ = yahoo(sym, "1mo")
        if len(d) >= 2:
            mkt[k] = {"last": d[-1]["c"], "chg": round((d[-1]["c"] / d[-2]["c"] - 1) * 100, 2)}

    stocks = []
    last_date = None
    for u in uni:
        daily, meta = yahoo(ysym(u["sym"]), "1y", "1d")
        if len(daily) < 30:
            print("no price", u["sym"], file=sys.stderr)
            continue
        hourly, _ = yahoo(ysym(u["sym"]), "3mo", "1h")
        last_date = max(last_date or "", daily[-1]["d"])
        cs = [b["c"] for b in daily]
        s = dict(u, zh=ZH.get(u["sym"], u["name"].split()[0]), en=u["name"], daily=daily, price=round(cs[-1], 2),
                 low=daily[-1]["l"], chg=round((cs[-1] / cs[-2] - 1) * 100, 2),
                 dd=round((cs[-1] / max(cs[-252:]) - 1) * 100, 1), rv20=rv(cs), rv_how=rv(cs, detail=True))
        s["vp"] = vprofile(hourly if len(hourly) > 100 else daily[-63:])
        s["vp_y"] = vprofile(daily, 30)
        try:
            s["opt"] = options(u["sym"], s["price"], dt.date.fromisoformat(daily[-1]["d"]))
        except Exception as ex:
            print("options error", u["sym"], ex, file=sys.stderr)
            s["opt"] = None
        s["floors"] = floors(s["price"], s["vp"], s["vp_y"], s["opt"])
        stocks.append(s)
        print(u["sym"], s["price"], "floors", [(f["lo"], f["hi"], f["stars"]) for f in s["floors"]],
              "opt", s["opt"] and {k: s["opt"].get(k) for k in ("oi", "iv30", "skew", "pc_vol", "put_wall", "call_wall")})
        time.sleep(0.5)

    # 收盤後才存每日紀錄：美東 16:15 以後（台北時間大約早上 5:15／夏令 4:15）
    et = now.astimezone(dt.timezone(dt.timedelta(hours=-5)))
    after_close = bool(last_date) and (et.date().isoformat() > last_date or et.hour * 100 + et.minute >= 1615) and not FIXTURE
    sig, new = track(stocks, last_date, after_close)
    for s in stocks:
        o, pv = s.get("opt") or {}, s.get("prev") or {}
        if o and o.get("iv30_chg") is None and o.get("iv30") is not None and pv.get("iv30") is not None:
            o["iv30_chg"] = round(o["iv30"] - pv["iv30"], 2)
        s["read"] = read(s, s.get("prev"), s.get("hist"))
    print("after_close", after_close, "new signals", [(x["sym"], x["type"]) for x in new])

    render(stocks, mkt, now.strftime("%m/%d %H:%M"), sig)
    keep = ("sym", "zh", "name", "cap", "sector", "industry", "price", "chg", "dd", "rv20", "floors", "opt")
    save("data/us_stocks.json", {"updated": now.isoformat(), "date": last_date, "market": mkt,
                                 "stocks": [{k: s.get(k) for k in keep} for s in stocks]})
    if new and os.environ.get("PUSH") == "1":
        push(new, last_date, os.environ.get("PAGE_URL", "https://wu-ting-yen.github.io/daily-news/"))


if __name__ == "__main__":
    main()
