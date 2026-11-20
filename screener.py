#!/usr/bin/env python3
"""台股分析（錯殺股篩選）：每天收盤後，把市值前 150 大上市公司＋前 150 大上櫃公司（共 300 檔）
分成「可能被錯殺」「基本面轉弱」「其他」，輸出 stocks.html 並用 Telegram 推播重點。

只用 Python 內建套件。資料來源（全部免費）：
- 證交所 OpenAPI：上市公司基本資料（股數、產業）、每日收盤、月營收（公開資訊觀測站）
- 證交所 T86：三大法人買賣超
- FinMind：每季財報（EPS、毛利率）與歷史本益比 / 股價淨值比
- Yahoo 股市：近半年每日收盤價、加權指數
"""
import datetime as dt
import html
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
TZ = dt.timezone(dt.timedelta(hours=8))
UA = {"User-Agent": "Mozilla/5.0 (daily-news screener)"}
UNIVERSE = 150          # 上市、上櫃各取市值前 150 大
DROP_LINE = -15.0       # 跌幅門檻（距近三個月高點）
BULL_LINE = -5.0        # 大盤距高點不到 5%＝多頭模式
REL_LINE = -10.0        # 多頭模式：比大盤多跌 10%（而且自己至少跌 8%）也算跌深
CACHE_DAYS = 6          # 財報、歷史本益比多久重抓一次
FINMIND_BUDGET = 290    # 每次最多打幾次 FinMind（免費版每小時 300 次）


def get(url, tries=3, timeout=30):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            err = e
            time.sleep(2 + i * 3)
    print("fetch failed:", url[:100], err, file=sys.stderr)
    return None


def num(s):
    try:
        return float(str(s).replace(",", "").strip())
    except Exception:
        return None


def load(path, default):
    try:
        with open(os.path.join(ROOT, path), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save(path, obj):
    with open(os.path.join(ROOT, path), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


# ───────────────────────── 資料抓取 ─────────────────────────

def pick(d, *keys):
    """不同 API 欄位名稱不一樣：依序找第一個有的欄位"""
    for k in keys:
        if k in d and str(d[k]).strip() not in ("", "-", "--"):
            return d[k]
    return None


def universe():
    out = []
    # 上市
    basic = get("https://openapi.twse.com.tw/v1/opendata/t187ap03_L") or []
    day = get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL") or []
    close = {d["Code"]: num(d["ClosingPrice"]) for d in day}
    out += top(basic, close, "TW")
    # 上櫃
    basic = get("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O") or []
    day = get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes") or []
    if basic:
        print("tpex basic keys", list(basic[0].keys())[:20])
    if day:
        print("tpex close keys", list(day[0].keys())[:12])
    close = {str(pick(d, "SecuritiesCompanyCode", "Code", "代號") or "").strip(): num(pick(d, "Close", "ClosingPrice", "收盤"))
             for d in day}
    out += top(basic, close, "TWO")
    return out


def top(basic, close, mkt):
    rows = []
    for b in basic:
        code = str(pick(b, "公司代號", "SecuritiesCompanyCode") or "").strip()
        if not (len(code) == 4 and code.isdigit()) or code.startswith(("0", "91")):
            continue
        shares = num(pick(b, "已發行普通股數或TDR原股發行股數", "已發行普通股數或TDR原發行股數", "IssueShares"))
        if not shares and pick(b, "Paidin.Capital.NTDollars"):   # 櫃買英文欄位：實收資本額 ÷ 每股面額
            cap = num(pick(b, "Paidin.Capital.NTDollars"))
            par = num("".join(ch for ch in str(pick(b, "ParValueOfCommonStock") or "10") if ch.isdigit() or ch == ".") or 10) or 10
            pref = num(pick(b, "PreferredStock.shares")) or 0
            shares = cap / par - pref if cap else None
        px = close.get(code)
        if shares and px:
            rows.append({"code": code, "name": str(pick(b, "公司簡稱", "CompanyAbbreviation", "公司名稱", "CompanyName") or code).strip(), "mcap": shares * px, "mkt": mkt})
    rows.sort(key=lambda r: -r["mcap"])
    print("universe", mkt, len(rows), [r["name"] for r in rows[:5]])
    return rows[:UNIVERSE]


def yahoo(symbol, rng="6mo", full=False):
    j = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?range={rng}&interval=1d")
    try:
        r = j["chart"]["result"][0]
        q = r["indicators"]["quote"][0]
        out, hlv = [], []
        for i, t in enumerate(r["timestamp"]):
            c = q["close"][i]
            if c is not None:
                out.append((dt.datetime.fromtimestamp(t, TZ).strftime("%Y-%m-%d"), round(c, 2)))
                hlv.append((q["high"][i], q["low"][i], q["volume"][i]))
        return (out, hlv) if full else out
    except Exception:
        return ([], []) if full else []


class FinMind:
    def __init__(self):
        self.left = FINMIND_BUDGET
        self.token = (os.environ.get("FINMIND_TOKEN") or "").strip()
        self.dead = False

    def __call__(self, dataset, code, start):
        if self.dead or self.left <= 0:
            return None
        self.left -= 1
        q = {"dataset": dataset, "data_id": code, "start_date": start}
        if self.token:
            q["token"] = self.token
        j = get("https://api.finmindtrade.com/api/v4/data?" + urllib.parse.urlencode(q), tries=2)
        time.sleep(0.4)
        if not j or j.get("status") != 200:
            print("FinMind stop:", j and j.get("msg"), file=sys.stderr)
            self.dead = True
            return None
        return j["data"]


def quarterly(rows):
    """FinMind 綜合損益表 → {季底日期: {EPS, Revenue, GrossProfit}}"""
    q = {}
    for r in rows:
        if r["type"] in ("EPS", "Revenue", "GrossProfit"):
            q.setdefault(r["date"], {})[r["type"]] = r["value"]
    return dict(sorted(q.items()))


def pctiles(vals):
    v = sorted(x for x in vals if x and x > 0)
    if len(v) < 60:
        return None
    return [round(v[min(len(v) - 1, int(len(v) * i / 20))], 2) for i in range(21)]


def pct_rank(p, x):
    """x 在歷史分位表 p 中的位置（0=史上最便宜，1=史上最貴）"""
    if not p or not x:
        return None
    below = sum(1 for v in p if v < x)
    return min(1.0, max(0.0, (below - 0.5) / 20))


def refresh_cache(codes, cache, fm, today):
    start_fin = (today - dt.timedelta(days=800)).strftime("%Y-%m-%d")
    start_per = (today - dt.timedelta(days=365 * 3)).strftime("%Y-%m-%d")
    # 最久沒更新的先抓，額度用完就下次再抓
    order = sorted(codes, key=lambda c: cache.get(c, {}).get("at", ""))
    for code in order:
        c = cache.get(code, {})
        if c.get("at", "") >= (today - dt.timedelta(days=CACHE_DAYS)).isoformat() and "mr" in c:
            continue
        fin = fm("TaiwanStockFinancialStatements", code, start_fin)
        per = fm("TaiwanStockPER", code, start_per) if fin is not None else None
        mr = fm("TaiwanStockMonthRevenue", code, start_fin) if per is not None else None
        if fin is None or per is None or mr is None:
            break
        c = {"at": today.isoformat(), "q": quarterly(fin),
             "mr": {f'{r["revenue_year"]}-{r["revenue_month"]:02d}': r["revenue"] for r in mr}}
        if per:
            c["pe_p"] = pctiles([r["PER"] for r in per])
            c["pb_p"] = pctiles([r["PBR"] for r in per])
            last = per[-1]
            c["per_last"] = {"date": last["date"], "PER": last["PER"], "PBR": last["PBR"]}
        cache[code] = c
    return cache


def refresh_bs(codes, cache, fm, today):
    """資產負債表（合約負債、存貨）：一季才變一次，30 天重抓；用剩下的 FinMind 額度"""
    start = (today - dt.timedelta(days=800)).strftime("%Y-%m-%d")
    order = sorted(codes, key=lambda c: cache.get(c, {}).get("bs_at", ""))
    n = 0
    for code in order:
        c = cache.get(code)
        if not c or c.get("bs_at", "") >= (today - dt.timedelta(days=30)).isoformat():
            continue
        rows = fm("TaiwanStockBalanceSheet", code, start)
        if rows is None:
            break
        bs = {}
        for r in rows:
            t, name = str(r.get("type", "")), str(r.get("origin_name", ""))
            if t.endswith("_per"):
                continue
            d = bs.setdefault(r["date"], [0.0, None])
            if t.startswith("ContractLiabilit") or "合約負債" in name:
                d[0] += r["value"] or 0
            elif t == "Inventories" or name == "存貨":
                d[1] = r["value"]
        c["bs"] = dict(sorted(bs.items())[-6:])
        c["bs_at"] = today.isoformat()
        n += 1
    print("balance sheets fetched", n)
    return cache


def institutional(dates):
    """最近幾個交易日的外資＋投信買賣超（股）"""
    tot = {}
    used = []
    for d in dates:
        j = get(f"https://www.twse.com.tw/rwd/zh/fund/T86?date={d.replace('-', '')}&selectType=ALLBUT0999&response=json")
        time.sleep(3)
        if not j or j.get("stat") != "OK":
            continue
        used.append(d)
        f = j["fields"]
        ifor = [i for i, n in enumerate(f) if "外陸資買賣超" in n or n == "外資自營商買賣超股數"]
        itru = f.index("投信買賣超股數")
        for row in j["data"]:
            code = row[0].strip()
            t = tot.setdefault(code, [0, 0])
            t[0] += sum(num(row[i]) or 0 for i in ifor)
            t[1] += num(row[itru]) or 0
    return tot, used


def post_json(url, form):
    try:
        req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(), headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        print("post failed:", url[:80], e, file=sys.stderr)
        return None


def tpex_institutional(dates, tot):
    """上櫃三大法人買賣超（股），加到 tot；回傳成功的日期"""
    used = []
    for d in dates:
        y, m, dd = d.split("-")
        rows, fi, ti = None, None, None
        j = post_json("https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade",
                      {"type": "Daily", "sect": "EW", "date": f"{y}/{m}/{dd}", "id": "", "response": "json"})
        try:
            t = (j or {}).get("tables") or []
            if t and t[0].get("data"):
                f = [str(x) for x in t[0].get("fields") or []]
                if d == dates[-1]:
                    print("tpex insti fields", f)
                rows = t[0]["data"]
                # 「外資及陸資合計」或「外資及陸資(不含外資自營商)」＋「外資自營商」的買賣超
                fi = [i for i, n in enumerate(f) if "外資" in n and "買賣超" in n and "合計" in n] or \
                     [i for i, n in enumerate(f) if "外資" in n and "買賣超" in n]
                ti = [i for i, n in enumerate(f) if "投信" in n and "買賣超" in n][:1]
                if (not fi or not ti) and len(f) >= 14:
                    # 欄位名稱只寫「買賣超股數」：固定順序是 外資(不含自營)、外資自營商、外資合計、投信、自營商…
                    fi, ti = [10], [13]
        except Exception as e:
            print("tpex insti parse", e, file=sys.stderr)
        if not rows:
            roc = f"{int(y) - 1911}/{m}/{dd}"
            j = get(f"https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&o=json&se=EW&t=D&d={roc}", tries=2)
            rows = (j or {}).get("aaData") or []
            fi, ti = [10], [13]   # 舊版固定欄位：外資及陸資合計買賣超、投信買賣超
        time.sleep(2)
        if not rows or not fi or not ti:
            print("tpex insti none", d, str(j)[:300], file=sys.stderr)
            continue
        used.append(d)
        for row in rows:
            code = str(row[0]).strip()
            if len(code) != 4:
                continue
            t_ = tot.setdefault(code, [0, 0])
            t_[0] += num(row[fi[0]]) or 0
            t_[1] += num(row[ti[0]]) or 0
    print("tpex inst days", used)
    return used


def taifex_csv(url, form):
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(), headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
            text = raw.decode("big5", "replace") if not raw.startswith(b"\xef\xbb\xbf") else raw.decode("utf-8-sig")
            rows = [ln.split(",") for ln in text.strip().splitlines()]
            return rows if rows and len(rows[0]) > 3 else []
        except Exception as e:
            print("taifex error", attempt, url, e, file=sys.stderr)
            time.sleep(5)
    return []


def vprofile(bars, nbin=24, unit=None):
    """成交量分佈（Volume Profile）：把每天的成交量平均分到當天最低到最高價之間，累加成各價位的量。
    unit：價格刻度要對齊的單位（台指選履約價 50 點），每格會是它的整數倍"""
    bars = [(h, l, v) for h, l, v in bars if h and l and v and h >= l]
    if len(bars) < 10:
        return None
    lo, hi = min(l for _, l, _ in bars), max(h for h, _, _ in bars)
    if unit:
        raw = (hi - lo) / nbin
        step = min((x for x in (50, 100, 200, 250, 500, 1000, 2000) if x >= unit), key=lambda x: abs(x - raw))
        lo = math.floor(lo / step) * step
        nbin = max(1, math.ceil((hi - lo) / step))
    else:
        step = (hi - lo) / nbin or 1
    vol = [0.0] * nbin
    for h, l, v in bars:
        a, b = int((l - lo) / step), min(nbin - 1, int((h - lo) / step))
        a = min(a, nbin - 1)
        for i in range(a, b + 1):
            vol[i] += v / (b - a + 1)
    poc = max(range(nbin), key=lambda i: vol[i])
    # 價值區：從最密集處往外擴，直到涵蓋 70% 成交量
    tot, inc, i0, i1 = sum(vol), vol[poc], poc, poc
    while inc < tot * 0.7 and (i0 > 0 or i1 < nbin - 1):
        up = vol[i1 + 1] if i1 < nbin - 1 else -1
        dn = vol[i0 - 1] if i0 > 0 else -1
        if up >= dn:
            i1 += 1; inc += up
        else:
            i0 -= 1; inc += dn
    p = lambda i: round(lo + step * i, 2)
    return {"lo": round(lo, 2), "step": round(step, 4), "vol": [round(x) for x in vol],
            "poc": p(poc + 0.5), "val": p(i0), "vah": p(i1 + 1)}


def futures_bars(date):
    """台指期近月（每天成交量最大的月合約、一般時段）近 3 個月的高、低、量"""
    end = dt.date.fromisoformat(date)
    bars = {}
    for k in range(3):
        e = end - dt.timedelta(days=30 * k)
        st = e - dt.timedelta(days=29)
        rows = taifex_csv("https://www.taifex.com.tw/cht/3/futDataDown",
                          {"down_type": "1", "commodity_id": "TX", "commodity2_id": "",
                           "queryStartDate": st.strftime("%Y/%m/%d"), "queryEndDate": e.strftime("%Y/%m/%d")})
        time.sleep(2)
        if len(rows) < 2:
            continue
        h = rows[0]
        try:
            ix = {n: h.index(n) for n in ("交易日期", "到期月份(週別)", "最高價", "最低價", "成交量", "交易時段", "收盤價")}
        except ValueError:
            print("futures header", h[:12], file=sys.stderr)
            continue
        for r in rows[1:]:
            if len(r) < len(h) - 2 or r[ix["交易時段"]].strip() != "一般":
                continue
            m = r[ix["到期月份(週別)"]].strip()
            if not (len(m) == 6 and m.isdigit()):
                continue
            d0, v = r[ix["交易日期"]].strip(), num(r[ix["成交量"]]) or 0
            if v > bars.get(d0, (0, 0, 0, 0))[2]:
                bars[d0] = (num(r[ix["最高價"]]), num(r[ix["最低價"]]), v, num(r[ix["收盤價"]]))
    return [bars[d] for d in sorted(bars)][-60:]


def option_strike_volume(date, days=28):
    """台指選擇權近一個月各履約價的成交量（買權、賣權分開，所有到期合約、日盤＋夜盤）"""
    e = dt.date.fromisoformat(date)
    rows = taifex_csv("https://www.taifex.com.tw/cht/3/optDataDown",
                      {"down_type": "1", "commodity_id": "TXO", "commodity_id2": "",
                       "queryStartDate": (e - dt.timedelta(days=days)).strftime("%Y/%m/%d"), "queryEndDate": e.strftime("%Y/%m/%d")})
    if len(rows) < 2:
        return {}
    h = rows[0]
    try:
        ik, icp, iv, idt = h.index("履約價"), h.index("買賣權"), h.index("成交量"), h.index("交易日期")
    except ValueError:
        return {}
    out, dates = {}, set()
    for r in rows[1:]:
        if len(r) <= iv:
            continue
        k = num(r[ik])
        if not k:
            continue
        t = out.setdefault(int(k), [0, 0])
        t[0 if r[icp].strip() == "買權" else 1] += int(num(r[iv]) or 0)
        dates.add(r[idt])
    return {"by": out, "days": len(dates)}


def options(date, spot):
    """台指選擇權：總未平倉與變化、Put/Call 比、外資部位；台指期成交量分佈"""
    d = date.replace("-", "/")
    out = {"date": date}
    start = (dt.date.fromisoformat(date) - dt.timedelta(days=30)).strftime("%Y/%m/%d")
    pc = taifex_csv("https://www.taifex.com.tw/cht/3/pcRatioDown", {"queryStartDate": start, "queryEndDate": d})
    rows = [r for r in pc[1:] if len(r) > 6 and num(r[3]) is not None]
    rows.sort(key=lambda r: r[0])
    if rows:
        out["pc"] = [[r[0].replace("/", "-"), num(r[3]), num(r[6]), int((num(r[4]) or 0) + (num(r[5]) or 0))] for r in rows][-20:]
        if out["pc"][-1][0] != date:
            return {}  # 當天資料還沒公布，讓呼叫端改用前一個交易日
        out["oi"] = out["pc"][-1][3]
        out["oi_put"], out["oi_call"] = int(num(rows[-1][4]) or 0), int(num(rows[-1][5]) or 0)
    fi = taifex_csv("https://www.taifex.com.tw/cht/3/callsAndPutsDateDown", {"queryStartDate": d, "queryEndDate": d, "commodityId": "TXO"})
    for r in fi[1:]:
        if len(r) > 15 and "外資" in r[3]:
            out["foreign_" + ("call" if r[2].strip().upper() == "CALL" else "put")] = round((num(r[15]) or 0) / 1e5, 2)  # 千元 → 億
    fb = futures_bars(date)
    if fb:
        out["vp"] = vprofile([(h, l, v) for h, l, v, _ in fb], 24, unit=50)
        out["fut_close"] = fb[-1][3]
        out["fut_days"] = len(fb)
        sv = option_strike_volume(date)
        vp = out["vp"]
        if sv.get("by") and vp:
            n = len(vp["vol"])
            ob = [[0, 0] for _ in range(n)]
            for k, (c, p_) in sv["by"].items():
                i = int((k - vp["lo"]) / vp["step"])
                if 0 <= i < n:
                    ob[i][0] += c; ob[i][1] += p_
            vp["opt"] = ob
            out["opt_days"] = sv["days"]
            # 選擇權成交最集中的買權／賣權履約價（在期貨價上下方）
            px = fb[-1][3]
            up = {k: v[0] for k, v in sv["by"].items() if px <= k <= px * 1.12}
            dn = {k: v[1] for k, v in sv["by"].items() if px * 0.88 <= k < px}
            if up:
                out["call_hot"] = max(up, key=up.get)
            if dn:
                out["put_hot"] = max(dn, key=dn.get)
    return out


def hedge(o, mkt, idx_closes):
    """避險燈號：幾個警訊同時出現就建議避險"""
    why, n = [], 0
    vp, px = o.get("vp"), o.get("fut_close")
    if vp and px:
        if px < vp["val"]:
            n += 1; why.append(f"台指期 {px:,.0f} 跌破近 {o.get('fut_days', 60)} 日成交密集區下緣 {vp['val']:,.0f}，下方買盤支撐變少")
        elif px > vp["vah"]:
            why.append(f"台指期 {px:,.0f} 在成交密集區上方，多數人有賺，支撐還在")
        else:
            why.append(f"台指期 {px:,.0f} 在成交密集區（{vp['val']:,.0f}–{vp['vah']:,.0f}）裡，多空拉鋸")
    pc = o.get("pc") or []
    if len(pc) >= 6:
        v = [x[1] for x in pc]
        avg = sum(v[:-1]) / (len(v) - 1)
        if v[-1] >= max(110, avg * 1.15):
            n += 1; why.append(f"Put/Call 成交比 {v[-1]:.0f}% 高於平常（{avg:.0f}%），買保險的人變多")
        oi5 = pc[-6][3]
        if oi5 and o.get("oi"):
            ch = (o["oi"] / oi5 - 1) * 100
            if ch >= 20 and v[-1] >= avg:
                n += 1; why.append(f"選擇權總未平倉 5 日增加 {ch:.0f}%，而且偏向賣權，避險部位在堆積")
    fc, fp = o.get("foreign_call"), o.get("foreign_put")
    if fc is not None and fp is not None and fp - fc > 0.5:
        n += 1; why.append(f"外資在選擇權偏空（賣權淨部位比買權多 {fp - fc:.1f} 億）")
    if len(idx_closes) >= 20 and idx_closes[-1] < sum(idx_closes[-20:]) / 20:
        n += 1; why.append("加權指數在 20 日均線下方，短線趨勢向下")
    level = "建議避險" if n >= 3 else "留意" if n == 2 else "不用避險"
    return {"level": level, "n": n, "why": why}


def warrants(date, name2code):
    """每檔標的當天認購 / 認售權證成交金額（元），上市＋上櫃權證合計"""
    tot = {}
    for typ, side in (("0999", 0), ("0999P", 1)):
        j = get(f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={date.replace('-', '')}&type={typ}&response=json")
        time.sleep(3)
        for t in (j or {}).get("tables", []):
            f = t.get("fields") or []
            if "標的代號" not in f or "成交金額" not in f:
                continue
            iu, ia = f.index("標的代號"), f.index("成交金額")
            for r in t.get("data", []):
                code = r[iu].strip()
                tot.setdefault(code, [0, 0])[side] += num(r[ia]) or 0
    # 上櫃權證：成交檔沒有標的代號，要用基本資料的「標的名稱」對回股票代號
    basic = get("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap37_O") or []
    meta = {}
    for r in basic if isinstance(basic, list) else []:
        code = name2code.get(str(r.get("標的證券/指數", "")).strip())
        if code:
            meta[str(r.get("權證代號", "")).strip()] = (code, 1 if "售" in str(r.get("權證類型", "")) else 0)
    trades = get("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap42_O") or []
    roc = f"{int(date[:4]) - 1911}{date[5:7]}{date[8:]}"
    for r in trades if isinstance(trades, list) else []:
        m = meta.get(str(r.get("權證代號", "")).strip())
        if m and str(r.get("交易日期", roc)) == roc:
            tot.setdefault(m[0], [0, 0])[m[1]] += num(r.get("成交金額")) or 0
    return tot


# ───────────────────────── 判斷邏輯 ─────────────────────────

def analyse(s, rev, cache, inst, mkt_dd):
    reasons = []          # (好/壞/中, 白話說明)
    score = 0
    flags = []
    hist = s["hist"]
    closes = [c for _, c in hist]
    last = closes[-1]
    win = closes[-63:]   # 近三個月
    hi = max(win)
    dd = (last / hi - 1) * 100
    s.update(price=last, high=hi, dd=round(dd, 1), chg1=round((closes[-1] / closes[-2] - 1) * 100, 2) if len(closes) > 1 else 0,
             spark=[round(x, 2) for x in closes[-63:]])

    # 1) 月營收
    r = rev.get(s["code"])
    if r:
        yoy, ytd, ym = num(r["營業收入-去年同月增減(%)"]), num(r["累計營業收入-前期比較增減(%)"]), r["資料年月"]
        mon = int(ym[-2:])
        s.update(industry=r["產業別"], rev_yoy=yoy and round(yoy, 1), rev_ytd=ytd and round(ytd, 1), rev_month=mon)
        if "金融" in r["產業別"] or "保險" in r["產業別"] or s["code"].startswith("28"):
            # 金融股的「營收」含投資損益，單月起伏很大，不拿來評分
            if yoy is not None:
                reasons.append(("mid", f"{mon} 月營收年增 {yoy:+.1f}%（金融股營收含投資損益，波動大，不計分）"))
            yoy = ytd = None
        if yoy is not None:
            pts = 2 if yoy >= 20 else 1 if yoy >= 5 else -2 if yoy <= -15 else -1 if yoy <= -5 else 0
            score += pts
            reasons.append(("good" if pts > 0 else "bad" if pts < 0 else "mid", f"{mon} 月營收比去年同月 {yoy:+.1f}%"))
            if yoy <= -20:
                flags.append("營收大幅衰退")
        if ytd is not None:
            pts = 1 if ytd >= 10 else -1 if ytd <= -5 else 0
            score += pts
            reasons.append(("good" if pts > 0 else "bad" if pts < 0 else "mid", f"今年累計營收 {ytd:+.1f}%"))

    # 2) 每季 EPS、毛利率
    c = cache.get(s["code"], {})
    q = c.get("q") or {}
    ks = list(q.keys())
    if ks:
        k = ks[-1]
        y, m = int(k[:4]), int(k[5:7])
        qn = (m - 1) // 3 + 1
        ly = f"{y - 1}{k[4:]}"
        eps, eps_ly = q[k].get("EPS"), q.get(ly, {}).get("EPS")
        s.update(q_label=f"{y}Q{qn}", eps=eps, eps_ly=eps_ly)
        ttm = [q[x].get("EPS") for x in ks[-4:]]
        if len(ttm) == 4 and all(v is not None for v in ttm):
            s["eps_ttm"] = round(sum(ttm), 2)
        if eps is not None:
            if eps <= 0:
                score -= 2
                flags.append("最新一季虧損")
                reasons.append(("bad", f"{y}Q{qn} 每股虧損 {eps:.2f} 元"))
            elif eps_ly is not None:
                if eps_ly <= 0:
                    score += 1
                    reasons.append(("good", f"{y}Q{qn} 轉虧為盈，EPS {eps:.2f} 元"))
                else:
                    g = (eps / eps_ly - 1) * 100
                    s["eps_yoy"] = round(g, 1)
                    pts = 2 if g >= 20 else 1 if g >= 5 else -2 if g <= -20 else -1 if g <= -5 else 0
                    score += pts
                    if g <= -30:
                        flags.append("獲利大減")
                    reasons.append(("good" if pts > 0 else "bad" if pts < 0 else "mid",
                                    f"{y}Q{qn} EPS {eps:.2f} 元，比去年同季 {g:+.0f}%"))

        def gm(key):
            d = q.get(key, {})
            if d.get("GrossProfit") is not None and d.get("Revenue"):
                return d["GrossProfit"] / d["Revenue"] * 100
        g0, g1, gq = gm(k), gm(ly), gm(ks[-2]) if len(ks) > 1 else None
        if g0 is not None and abs(g0) < 100:
            s["gm"] = round(g0, 1)
            if g1 is not None:
                diff = g0 - g1
                s["gm_yoy"] = round(diff, 1)
                pts = 1 if diff >= 1 else -1 if diff <= -2 else 0
                score += pts
                word = "比去年同季多" if diff >= 0 else "比去年同季少"
                reasons.append(("good" if pts > 0 else "bad" if pts < 0 else "mid",
                                f"毛利率 {g0:.1f}%，{word} {abs(diff):.1f} 個百分點"))
            if gq is not None and g0 - gq <= -2:
                score -= 1
                reasons.append(("bad", f"毛利率比上一季掉了 {gq - g0:.1f} 個百分點"))

    # 詳細資料：近 6 個月營收年增、近 5 季 EPS / 毛利率
    mrv = c.get("mr") or {}
    if r and r.get("資料年月"):   # 用證交所最新一個月補上
        ym = r["資料年月"]
        cur, ly = num(r.get("營業收入-當月營收")), num(r.get("營業收入-去年當月營收"))
        y0 = int(ym[:-2]) + 1911
        if cur:
            mrv = dict(mrv)
            mrv[f"{y0}-{int(ym[-2:]):02d}"] = cur * 1000
            if ly:
                mrv[f"{y0 - 1}-{int(ym[-2:]):02d}"] = ly * 1000
    series = []
    upto = f"{int(r['資料年月'][:-2]) + 1911}-{int(r['資料年月'][-2:]):02d}" if r and r.get("資料年月") else "9999"
    for k in [k for k in sorted(mrv) if k <= upto][-6:]:
        prev = mrv.get(f"{int(k[:4]) - 1}{k[4:]}")
        if prev:
            series.append([int(k[5:]), round((mrv[k] / prev - 1) * 100, 1)])
    s["rev6"] = series
    if len(series) >= 3:
        neg = sum(1 for _, v in series[-3:] if v < 0)
        if neg == 3:
            reasons.append(("bad", "營收已經連續 3 個月比去年少"))
        elif neg == 0 and all(v >= 10 for _, v in series[-3:]):
            reasons.append(("good", "近 3 個月營收都比去年成長 10% 以上"))
    qt = []
    for k in list(q.keys())[-5:]:
        d = q[k]
        g = d["GrossProfit"] / d["Revenue"] * 100 if d.get("GrossProfit") is not None and d.get("Revenue") else None
        qt.append([f"{k[2:4]}Q{(int(k[5:7]) - 1) // 3 + 1}", d.get("EPS"), g and round(g, 1)])
    s["q5"] = qt

    # 2.5) 未來需求的線索：合約負債（客戶預付款，常代表訂單）、存貨 vs 營收
    bs = c.get("bs") or {}
    if bs:
        k0 = sorted(bs)[-1]
        k1 = f"{int(k0[:4]) - 1}{k0[4:]}"
        cl0, inv0 = bs[k0]
        cl1, inv1 = (bs.get(k1) or [None, None])
        qr0, qr1 = (q.get(k0) or {}).get("Revenue"), (q.get(k1) or {}).get("Revenue")
        dm = {"q": f"{k0[2:4]}Q{(int(k0[5:7]) - 1) // 3 + 1}"}
        if cl0 and cl1 and cl1 > 0 and (qr0 is None or cl0 >= qr0 * 0.02):   # 合約負債太小的公司不看
            dm["cl_yoy"] = round((cl0 / cl1 - 1) * 100)
            dm["cl"] = round(cl0 / 1e8, 1)
        if inv0 and inv1 and inv1 > 0:
            dm["inv_yoy"] = round((inv0 / inv1 - 1) * 100)
        if qr0 and qr1 and qr1 > 0:
            dm["qrev_yoy"] = round((qr0 / qr1 - 1) * 100)
        s["demand"] = dm
        if dm.get("cl_yoy") is not None:
            if dm["cl_yoy"] >= 30:
                score += 1
                reasons.append(("good", f"客戶預付款（合約負債）{dm['cl']:.1f} 億，比去年同期多 {dm['cl_yoy']}%：客戶先付錢卡位，通常代表接下來的訂單變多"))
            elif dm["cl_yoy"] <= -30:
                score -= 1
                reasons.append(("bad", f"客戶預付款（合約負債）比去年同期少 {-dm['cl_yoy']}%：預先下的訂單變少，未來營收可能放緩"))
        if dm.get("inv_yoy") is not None and dm.get("qrev_yoy") is not None:
            gap = dm["inv_yoy"] - dm["qrev_yoy"]
            if gap >= 25 and dm["inv_yoy"] >= 20:
                score -= 1
                reasons.append(("bad", f"存貨比去年多 {dm['inv_yoy']}%，但營收只成長 {dm['qrev_yoy']}%：東西做出來賣得沒那麼快，可能是需求轉弱的早期訊號"))
            elif gap <= -25 and dm["qrev_yoy"] >= 10:
                reasons.append(("good", f"營收成長 {dm['qrev_yoy']}%，存貨只變化 {dm['inv_yoy']:+d}%：貨很快被買走，需求健康"))

    # 3) 估值：跟自己過去三年比
    pl = c.get("per_last")
    base = dict(s.get("hist_all") or hist).get(pl["date"]) if pl else None   # 補算舊日期時，用完整股價找本益比的基準日
    cheap = False
    if pl and base:
        ratio = last / base
        pe = pl["PER"] * ratio if pl["PER"] and pl["PER"] > 0 else None
        pb = pl["PBR"] * ratio if pl["PBR"] else None
        s.update(pe=pe and round(pe, 1), pb=pb and round(pb, 2))
        pr = pct_rank(c.get("pe_p"), pe)
        br = pct_rank(c.get("pb_p"), pb)
        s.update(pe_rank=pr, pb_rank=br)
        rank = pr if pr is not None else br
        what = "本益比" if pr is not None else "股價淨值比"
        val = pe if pr is not None else pb
        if rank is not None:
            cheaper = round((1 - rank) * 100)
            if rank <= 0.3:
                cheap = True
                reasons.append(("good", f"{what} {val:.1f} 倍，是過去三年最便宜的時候" if cheaper >= 97
                                else f"{what} {val:.1f} 倍，比過去三年 {cheaper}% 的時間都便宜"))
            elif rank >= 0.8:
                reasons.append(("bad", f"{what} {val:.1f} 倍，跌完仍在過去三年偏貴的位置"))
            else:
                reasons.append(("mid", f"{what} {val:.1f} 倍，在過去三年的中間位置"))

    # 個股成交量分佈（近 60 日）
    vp = s.get("vp")
    if vp:
        if last < vp["val"]:
            reasons.append(("mid", f"股價跌破近 3 個月成交密集區（{vp['val']:,.1f}–{vp['vah']:,.1f}），上面有不少套牢賣壓"))
        elif last <= vp["vah"]:
            reasons.append(("mid", f"股價在近 3 個月成交密集區（{vp['val']:,.1f}–{vp['vah']:,.1f}）裡，這一帶買的人多，有支撐"))

    # 權證（散戶情緒，只當參考）
    w = WAR.get(s["code"])
    if w and sum(w["today"]) >= 5e6:
        c_amt, p_amt = w["today"]
        share = p_amt / (c_amt + p_amt) * 100
        s.update(w_call=round(c_amt / 1e8, 2), w_put=round(p_amt / 1e8, 2), w_put_share=round(share), w_put_avg=w.get("avg"))
        avg = w.get("avg")
        if avg is not None and share >= max(30, avg * 1.8):
            reasons.append(("good" if score >= 2 else "mid",
                            f"權證：認售佔 {share:.0f}%（平常約 {avg:.0f}%），散戶突然很悲觀"))
        elif avg is not None and share <= avg * 0.5 and c_amt >= 3e7:
            reasons.append(("mid", f"權證：認購成交 {c_amt / 1e8:.1f} 億，散戶在搶反彈"))

    # 4) 法人（只當參考，不計入基本面）
    t = inst.get(s["code"])
    smart = False
    if t:
        f_amt, t_amt = t[0] * last / 1e8, t[1] * last / 1e8
        s.update(foreign=round(f_amt, 1), trust=round(t_amt, 1))
        smart = t_amt > 0.5 or f_amt > 2
        bits = []
        if abs(f_amt) >= 0.1:
            bits.append(f"外資{'買' if f_amt > 0 else '賣'}超 {abs(f_amt):.1f} 億")
        if abs(t_amt) >= 0.1:
            bits.append(f"投信{'買' if t_amt > 0 else '賣'}超 {abs(t_amt):.1f} 億")
        if bits:
            reasons.append(("good" if smart else "mid", "近 5 日" + "、".join(bits)))

    # 分類
    s.update(fund=score, flags=flags, cheap=cheap, reasons=reasons)
    bull = mkt_dd > BULL_LINE
    rel_deep = bull and dd - mkt_dd <= REL_LINE and dd <= -8
    deep = dd <= DROP_LINE or rel_deep
    if rel_deep and dd > DROP_LINE:
        s["deep_by"] = round(dd - mkt_dd, 1)
    if flags or score <= -2:
        s["cat"] = "weak"
    elif deep and score >= 3 and cheap:
        s["cat"] = "oversold"   # 跌深、基本面好、而且跌到比過去便宜
    elif deep and score >= 2:
        s["cat"] = "pricey"     # 基本面沒壞，但估值還不算低（可能是之前漲太多在修正）
    elif score >= 2:
        s["cat"] = "strong"     # 基本面好、跌得不多
    else:
        s["cat"] = "mixed"
    s["rank"] = round(score + (2 if cheap else 0) + (1 if smart else 0) + min(-dd, 40) / 10, 2)
    s["vs_mkt"] = round(dd - mkt_dd, 1)
    return s


# ───────────────────────── 同業比較、需求展望、文字說明 ─────────────────────────

def _avg(v):
    v = [x for x in v if x is not None]
    return sum(v) / len(v) if v else None


def _med(v):
    v = sorted(x for x in v if x is not None and x > 0)
    return v[len(v) // 2] if v else None


def peer_compare(stocks):
    """同產業、市值最接近的 4 家：比成長、毛利率、估值、跌幅"""
    by_ind = {}
    for s in stocks:
        if s.get("industry"):
            by_ind.setdefault(s["industry"], []).append(s)
    for s in stocks:
        same = [x for x in by_ind.get(s.get("industry"), []) if x is not s and x.get("mcap")]
        if len(same) < 2 or not s.get("mcap"):
            continue
        same.sort(key=lambda x: abs(math.log(x["mcap"] / s["mcap"])))
        ps = same[:4]
        ra, ea = _avg([x.get("rev_yoy") for x in ps]), _avg([x.get("eps_yoy") for x in ps])
        ga, pm, da = _avg([x.get("gm") for x in ps]), _med([x.get("pe") for x in ps]), _avg([x["dd"] for x in ps])
        better = worse = 0
        bits = []
        if s.get("rev_yoy") is not None and ra is not None:
            d = s["rev_yoy"] - ra
            better += d >= 5; worse += d <= -5
            bits.append(f"營收年增 {s['rev_yoy']:+.0f}%，同業平均 {ra:+.0f}%")
        if s.get("eps_yoy") is not None and ea is not None:
            d = s["eps_yoy"] - ea
            better += d >= 10; worse += d <= -10
            bits.append(f"獲利年增 {s['eps_yoy']:+.0f}%，同業平均 {ea:+.0f}%")
        if s.get("gm") is not None and ga is not None:
            d = s["gm"] - ga
            better += d >= 3; worse += d <= -3
            bits.append(f"毛利率 {s['gm']:.0f}%，同業平均 {ga:.0f}%")
        rel = "優於同業" if better >= 2 and worse == 0 else "落後同業" if worse >= 2 and better == 0 else "跟同業差不多"
        cheaper = s.get("pe") and pm and s["pe"] < pm * 0.85
        pricier = s.get("pe") and pm and s["pe"] > pm * 1.15
        fell_more = da is not None and s["dd"] < da - 5
        if s.get("pe") and pm:
            bits.append(f"本益比 {s['pe']:.0f} 倍，同業中位數 {pm:.0f} 倍")
        if da is not None:
            bits.append(f"股價距高點 {s['dd']:+.0f}%，同業平均 {da:+.0f}%")
        if rel == "優於同業" and fell_more:
            concl = "成長和獲利都比規模相近的同業好，股價卻跌得更多，相對同業來看更像是被錯殺。"
        elif rel == "優於同業" and cheaper:
            concl = "成長比同業好，本益比還比同業低，相對價值比較明顯。"
        elif rel == "優於同業":
            concl = "基本面比同業強，市場也已經給它比較高的評價。"
        elif rel == "落後同業" and pricier:
            concl = "成長落後同業、估值卻比同業貴，就算過去財報不錯，相對來說吸引力不高，要小心。"
        elif rel == "落後同業":
            concl = "成長落後規模相近的同業，就算自己跟去年比還在成長，資金比較可能流向同業裡更強的公司。"
        elif cheaper:
            concl = "基本面跟同業差不多，但本益比比同業便宜。"
        elif pricier:
            concl = "基本面跟同業差不多，本益比卻比同業貴。"
        else:
            concl = "基本面和估值都跟同業差不多，沒有特別突出。"
        names = "、".join(x["name"] for x in ps)
        s["peer"] = {"rel": rel, "names": names, "text": "；".join(bits) + "。" + concl,
                     "rows": [{k: x.get(k) for k in ("code", "name", "rev_yoy", "eps_yoy", "gm", "pe", "dd")} | {"mcap": round(x["mcap"] / 1e8)}
                              for x in [s] + ps]}
        s["reasons"].append(("good" if rel == "優於同業" else "bad" if rel == "落後同業" else "mid",
                             f"跟同業（{names}）比：{rel}。{concl}"))


def apply_outlook(stocks, outlook):
    """每日排程用新聞整理的「未來需求」重點（data/outlook.json）"""
    for s in stocks:
        o = outlook.get(s["code"])
        if not o:
            continue
        s["outlook"] = o
        v = o.get("view", "")
        kind = "good" if "增溫" in v else "bad" if "轉弱" in v else "mid"
        s["reasons"].append((kind, f"新聞面（{o.get('date', '')[5:].replace('-', '/')} 整理）：未來需求{v}。{o.get('summary', '')}"))


def _phr(s, good=True):
    out = []
    ry, ey, gy = s.get("rev_yoy"), s.get("eps_yoy"), s.get("gm_yoy")
    if good:
        if ry is not None and ry >= 5: out.append(f"營收比去年多 {ry:.0f}%")
        if ey is not None and ey >= 5: out.append(f"獲利成長 {ey:.0f}%")
        if gy is not None and gy >= 1: out.append("毛利率變高")
        if (s.get("demand") or {}).get("cl_yoy", 0) >= 30: out.append("客戶預付款大增")
    else:
        if ry is not None and ry <= -5: out.append(f"營收比去年少 {-ry:.0f}%")
        if ey is not None and ey <= -5: out.append(f"獲利衰退 {-ey:.0f}%")
        if gy is not None and gy <= -2: out.append("毛利率下滑")
        out += list(s.get("flags") or [])
    return "、".join(dict.fromkeys(out))


def story(s, hedge_level=None):
    dd, cat = -s["dd"], s["cat"]
    g, b = _phr(s, True), _phr(s, False)
    rank = s.get("pe_rank") if s.get("pe_rank") is not None else s.get("pb_rank")
    val = f"本益比在過去三年最便宜的 {max(1, round(rank * 100))}% 區間" if rank is not None and rank <= 0.3 else \
          "本益比在過去三年的中間位置" if rank is not None and rank < 0.6 else \
          "本益比在過去三年偏貴的位置" if rank is not None else "估值資料還不夠"
    rel = f"（大盤在漲，它卻比大盤多跌 {-s['deep_by']:.0f}%，所以多頭模式下也算跌深）" if s.get("deep_by") is not None else ""
    if cat == "oversold":
        t = f"股價離三個月高點跌了 {dd:.0f}%{rel}，但公司本身還在變好（{g or '基本面穩定'}），{val}。所以判斷這次下跌主要是被大盤和資金撤出拖累，不是公司出了問題。"
    elif cat == "pricey":
        t = f"股價跌了 {dd:.0f}%{rel}，公司本身沒變差（{g or '基本面穩定'}），但{val}。代表之前漲得多、市場期待高，現在比較像在修正過高的期待，還稱不上便宜。"
    elif cat == "weak":
        t = f"判斷基本面轉弱，因為{b or '整體分數偏低'}。這種跌是有理由的，看起來便宜可能是因為未來獲利會更差。"
    elif cat == "strong":
        t = f"基本面不錯（{g or '穩定'}），股價只離高點 {dd:.0f}%，比大盤抗跌，代表市場仍然認同。"
    else:
        t = f"有好有壞（好：{g or '—'}；壞：{b or '—'}），目前看不出明確方向，股價離高點 {dd:.0f}%。"
    p = s.get("peer")
    if p:
        t += f"和規模相近的同業比，{p['rel']}：" + p["text"].split("。")[-2] + "。"
    dm = s.get("demand") or {}
    if dm.get("cl_yoy") is not None or dm.get("inv_yoy") is not None:
        parts = []
        if dm.get("cl_yoy") is not None:
            parts.append(f"客戶預付款比去年{'多' if dm['cl_yoy'] >= 0 else '少'} {abs(dm['cl_yoy'])}%")
        if dm.get("inv_yoy") is not None and dm.get("qrev_yoy") is not None:
            parts.append(f"存貨變化 {dm['inv_yoy']:+d}%、營收 {dm['qrev_yoy']:+d}%")
        t += f"未來需求的線索（{dm.get('q', '')} 財報）：" + "，".join(parts) + "。"
    o = s.get("outlook")
    if o:
        t += f"新聞面看未來需求{o.get('view', '')}：{o.get('summary', '')}"
        if not t.endswith("。"):
            t += "。"
    if hedge_level == "建議避險" and cat in ("oversold", "pricey"):
        t += "不過大盤避險燈號亮著，短線就算個股便宜，也可能跟著大盤再跌。"
    s["story"] = t


def buy_score(s):
    """入手分數 0–100：把五個面向加起來，越高越值得優先研究"""
    parts = []
    f = s.get("fund", 0)
    pts = round(max(0, min(f, 7)) / 7 * 30)
    parts.append(("基本面", pts, 30, f"營收、獲利、毛利率等 {f:+d} 分（滿分 7）"))
    r = s.get("pe_rank") if s.get("pe_rank") is not None else s.get("pb_rank")
    if r is None:
        parts.append(("便宜程度", 8, 25, "估值資料不夠，給中間分"))
    else:
        nm = "本益比" if s.get("pe_rank") is not None else "股價淨值比"
        parts.append(("便宜程度", round((1 - r) * 25), 25, f"{nm}比過去三年 {round((1 - r) * 100)}% 的時候便宜"))
    rel = (s.get("peer") or {}).get("rel")
    parts.append(("同業比較", {"優於同業": 15, "跟同業差不多": 8, "落後同業": 0}.get(rel, 7), 15, rel or "找不到夠接近的同業"))
    dm, o = s.get("demand") or {}, s.get("outlook") or {}
    d, why = 7, []
    cl, inv, qr = dm.get("cl_yoy"), dm.get("inv_yoy"), dm.get("qrev_yoy")
    if cl is not None and cl >= 30: d += 4; why.append("客戶預付款大增")
    if cl is not None and cl <= -30: d -= 4; why.append("客戶預付款大減")
    if inv is not None and qr is not None:
        if inv - qr >= 25 and inv >= 20: d -= 4; why.append("存貨比營收長得快")
        elif inv - qr <= -25 and qr >= 10: d += 3; why.append("貨賣得比進得快")
    v = o.get("view", "")
    if "增溫" in v: d += 4; why.append("新聞面需求增溫")
    elif "轉弱" in v: d -= 7; why.append("新聞面需求轉弱")
    elif v: why.append(f"新聞面{v}")
    parts.append(("未來需求", max(0, min(15, d)), 15, "、".join(why) or "沒有明顯訊號，給中間分"))
    fo, tr = s.get("foreign"), s.get("trust")
    if fo is None and tr is None:
        parts.append(("法人動向", 5, 15, "沒有法人資料"))
    else:
        fo, tr = fo or 0, tr or 0
        pts = 15 if fo > 0 and tr > 0 else 10 if fo + tr > 0 else 5 if fo > 0 or tr > 0 else 0
        parts.append(("法人動向", pts, 15, f"近 5 日外資{'買' if fo >= 0 else '賣'} {abs(fo):.1f} 億、投信{'買' if tr >= 0 else '賣'} {abs(tr):.1f} 億"))
    sc = sum(x[1] for x in parts)
    s["score"], s["score_parts"] = sc, parts
    s["grade"] = "A 優先研究" if sc >= 85 else "B 值得觀察" if sc >= 70 else "C 普通" if sc >= 55 else "D 先別碰"


# ───────────────────────── 網頁 ─────────────────────────

def spark_svg(vals, w=120, h=36):
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    pts = " ".join(f"{i * w / (len(vals) - 1):.1f},{h - 3 - (v - lo) / span * (h - 6):.1f}" for i, v in enumerate(vals))
    hx = vals.index(hi) * w / (len(vals) - 1)
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" preserveAspectRatio="none" aria-hidden="true">'
            f'<polyline points="{pts}" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/>'
            f'<circle cx="{hx:.1f}" cy="3" r="2.2" class="hi"/></svg>')


def fmt(v, suf="%", sign=True):
    if v is None:
        return "—"
    return (f"{v:+.1f}" if sign else f"{v:.1f}") + suf


def tone(v, good=0):
    if v is None:
        return ""
    return "up" if v > good else "down" if v < good else ""


def where(r):
    if r is None:
        return ""
    w = "近3年最低" if r <= 0.05 else "近3年偏低" if r <= 0.3 else "近3年偏高" if r >= 0.7 else "近3年中間"
    return f" <small>{w}</small>"


def card(s, i=None):
    e = html.escape
    chips = []
    if s.get("rev_yoy") is not None:
        chips.append((f"{s['rev_month']}月營收年增", fmt(s["rev_yoy"]), tone(s["rev_yoy"])))
    if s.get("eps_yoy") is not None:
        chips.append(("EPS 年增", fmt(s["eps_yoy"], "%"), tone(s["eps_yoy"])))
    elif s.get("eps") is not None:
        chips.append(("最新季 EPS", f"{s['eps']:.2f}", tone(s["eps"])))
    if s.get("gm") is not None:
        chips.append(("毛利率", f"{s['gm']:.1f}%" + (f" <small>{s['gm_yoy']:+.1f}</small>" if s.get("gm_yoy") is not None else ""),
                      tone(s.get("gm_yoy"))))
    if s.get("pe"):
        r = s.get("pe_rank")
        chips.append(("本益比", f"{s['pe']:.1f}" + where(r),
                      "up" if r is not None and r <= 0.3 else "down" if r is not None and r >= 0.8 else ""))
    elif s.get("pb"):
        r = s.get("pb_rank")
        chips.append(("股價淨值比", f"{s['pb']:.2f}" + where(r),
                      "up" if r is not None and r <= 0.3 else ""))
    if s.get("w_put_share") is not None:
        avg = s.get("w_put_avg")
        chips.append(("權證認售佔比", f"{s['w_put_share']}%" + (f" <small>平常 {avg}%</small>" if avg is not None else ""),
                      "down" if avg is not None and s["w_put_share"] >= max(30, avg * 1.8) else ""))
    chip_html = "".join(f'<div class="chip {t}"><span>{a}</span><b>{b}</b></div>' for a, b, t in chips)
    marks = {"good": "○", "bad": "×", "mid": "·"}
    rs = "".join(f'<li class="{k}"><i>{marks[k]}</i>{e(t)}</li>' for k, t in s["reasons"])
    flag = "".join(f'<span class="flag">{e(f)}</span>' for f in s["flags"])
    if s.get("streak", 0) >= 2:
        flag += f'<span class="streak">連續 {s["streak"]} 天入選</span>'
    detail = ""
    if s.get("rev6"):
        mx = max(abs(v) for _, v in s["rev6"]) or 1
        bars = "".join(f'<div class="bar"><i class="{"pos" if v >= 0 else "neg"}" style="height:{max(3, abs(v) / mx * 34):.0f}px"></i>'
                       f'<b>{v:+.0f}%</b><span>{m}月</span></div>' for m, v in s["rev6"])
        detail += f'<div class="dt"><h4>近 {len(s["rev6"])} 個月營收年增</h4><div class="bars">{bars}</div></div>'
    if s.get("vp"):
        vp = s["vp"]
        mx = max(vp["vol"]) or 1
        rows = ""
        for i in reversed(range(len(vp["vol"]))):
            lo = vp["lo"] + vp["step"] * i
            hi = lo + vp["step"]
            inva = vp["val"] <= lo + 1e-6 and hi <= vp["vah"] + 1e-6
            ispoc = lo <= vp["poc"] < hi
            here = lo <= s["price"] < hi or (i == 0 and s["price"] < lo) or (i == len(vp["vol"]) - 1 and s["price"] >= hi)
            rows += (f'<div class="vp-r sm{" here" if here else ""}"><span>{lo:,.1f}</span>'
                     f'<div><i class="{"poc" if ispoc else "va" if inva else ""}" style="width:{vp["vol"][i] / mx * 100:.0f}%"></i></div>'
                     f'<em>{"◀ 現價" if here else ""}</em></div>')
        detail += f'<div class="dt"><h4>近 3 個月成交量分佈</h4><div class="vp">{rows}</div></div>'
    if s.get("q5"):
        head = "".join(f"<th>{k}</th>" for k, _, _ in s["q5"])
        eps = "".join(f"<td>{v:.2f}</td>" if v is not None else "<td>—</td>" for _, v, _ in s["q5"])
        gm = "".join(f"<td>{g:.1f}%</td>" if g is not None else "<td>—</td>" for _, _, g in s["q5"])
        detail += (f'<div class="dt"><h4>近 5 季</h4><table><tr><th></th>{head}</tr><tr><th>EPS</th>{eps}</tr>'
                   + (f'<tr><th>毛利率</th>{gm}</tr>' if any(g is not None for _, _, g in s["q5"]) else "") + '</table></div>')
    pr = s.get("peer")
    if pr:
        def f_(v, sfx="%", sign=True):
            return "—" if v is None else (f"{v:+.0f}{sfx}" if sign else f"{v:.0f}{sfx}")
        trs = "".join(f'<tr class="{"me" if i == 0 else ""}"><th>{e(r["name"])}</th><td>{r["mcap"]:,}</td><td>{f_(r.get("rev_yoy"))}</td>'
                      f'<td>{f_(r.get("eps_yoy"))}</td><td>{f_(r.get("gm"), "%", False)}</td><td>{f_(r.get("pe"), "", False)}</td><td>{f_(r.get("dd"))}</td></tr>'
                      for i, r in enumerate(pr["rows"]))
        detail += (f'<div class="dt"><h4>同業比較・{e(pr["rel"])}</h4><div class="tw"><table class="peer"><tr><th></th><th>市值(億)</th><th>營收年增</th>'
                   f'<th>獲利年增</th><th>毛利率</th><th>本益比</th><th>距高點</th></tr>{trs}</table></div>'
                   f'<p class="dnote">同產業（{e(s.get("industry", ""))}）裡市值最接近的 {len(pr["rows"]) - 1} 家。</p></div>')
    o = s.get("outlook")
    if o:
        src = "".join(f'<li><a href="{e(x.get("url", ""))}" target="_blank" rel="noopener">{e(x.get("title", ""))}</a></li>' for x in (o.get("sources") or [])[:4])
        detail += (f'<div class="dt"><h4>未來需求・新聞重點（{e(o.get("date", ""))}）</h4><p class="olk"><b>{e(o.get("view", ""))}</b>　{e(o.get("summary", ""))}</p>'
                   + (f'<ul class="src">{src}</ul>' if src else "") + '</div>')
    badge, sbox = "", ""
    if s.get("score") is not None:
        g = s["grade"][0]
        badge = f'<span class="score g{g}" title="入手分數"><b>{s["score"]}</b><small>分・{e(s["grade"])}</small></span>'
        rows = "".join(f'<li><span>{e(n)}</span><i><u style="width:{p / m * 100:.0f}%"></u></i><b>{p}<small>/{m}</small></b><em>{e(w)}</em></li>'
                       for n, p, m, w in s["score_parts"])
        sbox = f'<div class="dt"><h4>入手分數 {s["score"]} / 100・{e(s["grade"])}</h4><ul class="sparts">{rows}</ul></div>'
    rank = f'<span class="no">{i:02d}</span>' if i else ""
    return f'''<article class="card stock {s["cat"]}" data-code="{s["code"]}" data-name="{e(s["name"])}">
  <a class="head" href="https://tw.stock.yahoo.com/quote/{s["code"]}.{s.get("mkt", "TW")}" target="_blank" rel="noopener">
    {rank}<div class="nm"><h3>{e(s["name"])} <em>{s["code"]}</em></h3><div class="ind">{e(s.get("industry", ""))}{flag}</div></div>
    <div class="px"><div class="dd">{s["dd"]:+.1f}<small>%</small></div><div class="p">{s["price"]:,.2f}・距三個月高點</div></div>
  </a>
  <div class="sk">{spark_svg(s["spark"])}{badge}</div>
  <div class="chips">{chip_html}</div>
  {f'<p class="story">{e(s["story"])}</p>' if s.get("story") else ""}
  <details><summary>細節：每一項的判斷、同業、需求</summary>{sbox}<ul class="why">{rs}</ul>{detail}</details>
</article>'''


def render(stocks, mkt, updated, inst_days, sig=(), opt=None):
    groups = {
        "oversold": sorted([s for s in stocks if s["cat"] == "oversold"], key=lambda s: (-s.get("score", 0), -s["rank"])),
        "pricey": sorted([s for s in stocks if s["cat"] == "pricey"], key=lambda s: (-s.get("score", 0), -s["rank"])),
        "weak": sorted([s for s in stocks if s["cat"] == "weak"], key=lambda s: s["fund"] * 10 + s["dd"]),
        "others": sorted([s for s in stocks if s["cat"] in ("strong", "mixed")], key=lambda s: (s["cat"] != "strong", -s["fund"], s["dd"])),
    }
    intro = {
        "oversold": "跌超過 15%，但營收、獲利、毛利率大多還在成長，而且現在的本益比比過去三年多數時候都低。股價跌可能是被大盤拖累，不是公司本身變差。依「入手分數」由高到低排（滿分 100：基本面 30、便宜程度 25、同業比較 15、未來需求 15、法人動向 15），點「細節」看每一項拿幾分、為什麼。",
        "pricey": "基本面沒變差，也跌超過 15%，但本益比還在過去三年的中間或偏高位置。比較像之前漲太多在修正，不一定算便宜。",
        "weak": "營收或獲利已經在衰退、毛利率變差，或最新一季虧損。這些跌比較「有理由」，抄底要更小心。",
        "others": "基本面好壞參半，或基本面不錯但跌得還不多（抗跌）。",
    }
    names = {"oversold": "可能錯殺", "pricey": "跌深・不便宜", "weak": "基本面轉弱", "others": "其他"}
    nav = '<a class="navlink back" href="./">← 新聞</a><button data-tab="overview">總覽</button>'
    nav += "".join(f'<button data-tab="{k}">{names[k]}<b>{len(v)}</b></button>' for k, v in groups.items())
    nav += f'<button data-tab="signals">訊號紀錄<b>{len(sig)}</b></button><button data-tab="how">怎麼判斷</button>'
    body = overview(stocks, groups, mkt, opt)
    if mkt["dd"] > BULL_LINE:
        intro = dict(intro, oversold="【多頭模式】大盤離高點不到 5%，好股票通常回檔不深。所以除了跌超過 15% 的，<b>比大盤多跌 10% 以上</b>（自己至少跌 8%）、基本面好又便宜的也列進來，這是漲勢中的回檔買點。" + intro["oversold"])
    for k, v in groups.items():
        cs = [card(s, i + 1 if k == "oversold" else None) for i, s in enumerate(v)]
        cards = "".join(cs[:15])
        if len(cs) > 15:
            cards += f'<details class="more"><summary>再看其餘 {len(cs) - 15} 檔</summary>{"".join(cs[15:])}</details>'
        body += f'<section id="{k}"><p class="lead">{intro[k]}</p>{cards or "<p class=empty>今天沒有符合的股票</p>"}</section>'
    body += signals_html(sig)
    body += HOW.replace("{{DROP}}", str(int(-DROP_LINE))).replace("{{INST}}", "、".join(d[5:].replace("-", "/") for d in inst_days) or "—")
    n_drop = sum(1 for s in stocks if s["dd"] <= DROP_LINE)
    summary = (f'<div class="mk"><div><span class="lbl">加權指數</span><b>{mkt["last"]:,.0f}</b>'
               f'<small class="{tone(mkt["chg1"])}">{mkt["chg1"]:+.2f}%</small></div>'
               f'<div><span class="lbl">距三個月高點</span><b>{mkt["dd"]:+.1f}%</b><small>高點 {mkt["high"]:,.0f}</small></div>'
               f'<div><span class="lbl">跌超過 {int(-DROP_LINE)}%</span><b>{n_drop}</b><small>/ {len(stocks)} 檔</small></div>'
               f'<div><span class="lbl">目前模式</span><b>{"多頭" if mkt["dd"] > BULL_LINE else "修正"}</b><small>{"看比大盤多跌 10%" if mkt["dd"] > BULL_LINE else f"看跌超過 {int(-DROP_LINE)}%"}</small></div></div>')
    tpl = open(os.path.join(ROOT, "stocks_template.html"), encoding="utf-8").read()
    out = tpl.replace("{{UPDATED}}", updated).replace("{{NAV}}", nav).replace("{{BODY}}", body).replace("{{SUMMARY}}", summary)
    with open(os.path.join(ROOT, "stocks.html"), "w", encoding="utf-8") as f:
        f.write(out)
    return groups


CAT = {"oversold": ("可能錯殺", "var(--copper)"), "pricey": ("跌深・不便宜", "var(--fog)"),
       "weak": ("基本面轉弱", "#a5574a"), "strong": ("其他", "#b9b4a8"), "mixed": ("其他", "#b9b4a8")}


def options_card(o, mkt):
    if not o:
        return ""
    hg = o.get("hedge") or {}
    lv = hg.get("level", "—")
    cls = {"建議避險": "red", "留意": "amber", "不用避險": "calm"}.get(lv, "")
    why = "".join(f"<li>{html.escape(w)}</li>" for w in hg.get("why", []))
    vp_html = ""
    vp, px = o.get("vp"), o.get("fut_close")
    if vp:
        mx = max(vp["vol"]) or 1
        omx = max((max(a, b) for a, b in vp.get("opt") or [[0, 0]]), default=0) or 1
        rows = ""
        n = len(vp["vol"])
        for i in reversed(range(n)):
            lo = vp["lo"] + vp["step"] * i
            hi = lo + vp["step"]
            inva = vp["val"] <= lo + 1e-6 and hi <= vp["vah"] + 1e-6
            ispoc = lo <= vp["poc"] < hi
            here = px is not None and lo <= px < hi
            ob = ""
            if vp.get("opt"):
                c, p_ = vp["opt"][i]
                ob = (f'<div class="ob"><i class="p" style="width:{p_ / omx * 100:.0f}%"></i>'
                      f'<i class="c" style="width:{c / omx * 100:.0f}%"></i></div>')
            rows += (f'<div class="vp-r{" here" if here else ""}{" two" if ob else ""}"><span>{lo:,.0f}</span>'
                     f'<div><i class="{"poc" if ispoc else "va" if inva else ""}" style="width:{vp["vol"][i] / mx * 100:.0f}%"></i></div>'
                     f'{ob}<em>{"◀ 現在" if here else "最密集" if ispoc else ""}</em></div>')
        vp_html = (f'<h3 class="sub3">台指期成交量分佈（近 {o.get("fut_days", 60)} 個交易日）</h3>'
                   f'<p class="note2">每一列是一個價格區間（每格 {vp["step"]:,.0f} 點，等於 {vp["step"] / 50:.0f} 個相鄰履約價，左邊數字是區間起點），長條越長代表在那裡成交越多。<b>銅色</b>是成交最密集的價位（{vp["poc"]:,.0f}），'
                   f'<b>深霧藍</b>是 70% 成交量集中的區間（{vp["val"]:,.0f}–{vp["vah"]:,.0f}）。跌破這個區間，代表很多人買在上面、正在虧損，賣壓容易變大。</p>'
                   + (f'<p class="note2">右欄是<b>台指選擇權</b>近 {o.get("opt_days", 20)} 個交易日在各履約價的成交量（<b style="color:var(--copper)">銅色</b>買權、<b style="color:var(--fogdeep)">霧藍</b>賣權），代表交易員押注的價位。'
                      + (f'上方買權最熱的是 {o["call_hot"]:,}，' if o.get("call_hot") else "")
                      + (f'下方賣權最熱的是 {o["put_hot"]:,}。' if o.get("put_hot") else "")
                      + '期貨和選擇權都很密集的價位，是最重要的支撐或壓力。</p>' if vp.get("opt") else "")
                   + (f'<div class="vp-h"><span></span><span>台指期成交量</span><span>選擇權成交量</span><span></span></div>' if vp.get("opt") else "")
                   + f'<div class="vp">{rows}</div>')
    pc = o.get("pc") or []
    stats = ""
    if o.get("oi"):
        ch1 = (o["oi"] / pc[-2][3] - 1) * 100 if len(pc) > 1 and pc[-2][3] else None
        ch5 = (o["oi"] / pc[-6][3] - 1) * 100 if len(pc) > 5 and pc[-6][3] else None
        stats += (f'<div><span class="lbl">選擇權總未平倉</span><b>{o["oi"]:,}</b><small>口・'
                  + (f"比前一天 {ch1:+.0f}%" if ch1 is not None else "") + (f"、5 天 {ch5:+.0f}%" if ch5 is not None else "")
                  + ("（週三是結算日，到期合約退場，未平倉減少屬正常）" if ch1 is not None and ch1 < -15 and dt.date.fromisoformat(pc[-1][0]).weekday() == 2 else "")
                  + '</small></div>')
    if pc:
        vals = [x[1] for x in pc]
        stats += (f'<div><span class="lbl">Put/Call 成交比</span><b>{vals[-1]:.0f}%</b>'
                  f'<small>近 {len(vals)} 日平均 {sum(vals) / len(vals):.0f}%</small>{spark_svg(vals, 110, 26)}</div>')
    fo = ""
    if o.get("foreign_call") is not None and o.get("foreign_put") is not None:
        fc, fp = o["foreign_call"], o["foreign_put"]
        fo = (f'<p class="note2">外資台指選擇權未平倉：買權淨{"買" if fc >= 0 else "賣"} {abs(fc):.1f} 億、'
              f'賣權淨{"買" if fp >= 0 else "賣"} {abs(fp):.1f} 億。</p>')
    return f'''<div class="card ov"><h2>大盤・要不要避險</h2>
<div class="hedge {cls}"><b>{lv}</b><span>{hg.get("n", 0)} / 5 個警訊</span></div>
<ul class="hwhy">{why}</ul>
<div class="ostats">{stats}</div>{fo}
{vp_html}</div>'''


def overview(stocks, groups, mkt, opt=None):
    e = html.escape
    n = len(stocks) or 1
    # 1) 分類比例條
    seg = ""
    for k in ("oversold", "pricey", "weak", "others"):
        c = len(groups[k])
        if c:
            col = CAT["strong" if k == "others" else k][1]
            seg += f'<a href="#{k}" data-go="{k}" style="flex:{c};background:{col}" title="{c} 檔"></a>'
    legend = "".join(f'<span><i style="background:{CAT["strong" if k == "others" else k][1]}"></i>{CAT["strong" if k == "others" else k][0]} {len(groups[k])}</span>'
                     for k in ("oversold", "pricey", "weak", "others"))

    # 2) 錯殺地圖：橫軸跌幅、縱軸基本面分數
    W, H, L, B = 340, 250, 30, 26
    xmin = min(-60, min(s["dd"] for s in stocks) - 2)
    fmin, fmax = -7, 7

    def X(dd):
        return L + (dd - xmin) / (0 - xmin) * (W - L - 8)

    def Y(f):
        return 8 + (fmax - f) / (fmax - fmin) * (H - B - 8)
    pts = ""
    top = {s["code"] for s in groups["oversold"][:6]} | {s["code"] for s in groups["weak"][:3]}
    labels = ""
    placed = []
    for s in sorted(stocks, key=lambda s: s["cat"] == "oversold"):
        jitter = (int(s["code"]) % 7 - 3) * 0.09
        cx, cy = X(s["dd"]), Y(max(fmin, min(fmax, s["fund"] + jitter)))
        info = f'{s["name"]} {s["code"]}｜跌 {s["dd"]:.1f}%｜基本面 {s["fund"]:+d} 分｜{CAT[s["cat"]][0]}'
        pts += f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{4.2 if s["code"] in top else 3.3}" fill="{CAT[s["cat"]][1]}" data-i="{e(info)}"/>'
        if s["code"] in top and all(abs(cx - px) > 34 or abs(cy - py) > 12 for px, py in placed):
            placed.append((cx, cy))
            labels += f'<text x="{cx + 5:.1f}" y="{cy - 5:.1f}">{e(s["name"])}</text>'
    x15, y2 = X(DROP_LINE), Y(2)
    ticks = "".join(f'<text class="ax" x="{X(v):.1f}" y="{H - 8}" text-anchor="middle">{v}%</text>' for v in range(0, int(xmin) - 1, -15))
    yt = "".join(f'<text class="ax" x="{L - 6}" y="{Y(v) + 4:.1f}" text-anchor="end">{v:+d}</text>' for v in (-6, -3, 0, 3, 6))
    svg = (f'<svg class="map" viewBox="0 0 {W} {H}" role="img" aria-label="錯殺地圖">'
           f'<rect x="{L}" y="8" width="{x15 - L:.1f}" height="{y2 - 8:.1f}" class="zone good"/>'
           f'<rect x="{L}" y="{Y(-2):.1f}" width="{x15 - L:.1f}" height="{H - B - Y(-2):.1f}" class="zone bad"/>'
           f'<line x1="{x15:.1f}" y1="8" x2="{x15:.1f}" y2="{H - B}" class="rule"/><line x1="{L}" y1="{Y(0):.1f}" x2="{W - 8}" y2="{Y(0):.1f}" class="rule"/>'
           f'<text class="zl" x="{L + 6}" y="22">錯殺區</text><text class="zl bad" x="{L + 6}" y="{H - B - 8}">有理由的跌</text>'
           f'<text class="zl mute" x="{W - 12}" y="22" text-anchor="end">抗跌</text>'
           f'{ticks}{yt}{pts}<g class="lb">{labels}</g></svg>')

    # 3) 產業：每個產業平均跌幅、錯殺 / 轉弱各幾檔
    ind = {}
    for s in stocks:
        ind.setdefault(s.get("industry") or "其他", []).append(s)
    rows = ""
    for name, v in sorted(ind.items(), key=lambda kv: sum(x["dd"] for x in kv[1]) / len(kv[1])):
        if len(v) < 2:
            continue
        avg = sum(x["dd"] for x in v) / len(v)
        ov = sum(1 for x in v if x["cat"] == "oversold")
        wk = sum(1 for x in v if x["cat"] == "weak")
        rows += (f'<tr><th>{e(name)}<small>{len(v)} 檔</small></th>'
                 f'<td><div class="hb"><i style="width:{min(100, -avg / 50 * 100):.0f}%"></i></div><b>{avg:.1f}%</b></td>'
                 f'<td class="c">{ov or "·"}</td><td class="w">{wk or "·"}</td></tr>')
    table = f'<table class="ind-t"><tr><th>產業</th><th>平均跌幅</th><th>錯殺</th><th>轉弱</th></tr>{rows}</table>'

    def mini(lst, k):
        return "".join(f'<li><a href="#{k}" data-go="{k}"><b>{e(s["name"])}</b><em>{s["code"]}</em>'
                       f'<span>{s["dd"]:+.0f}%</span></a></li>' for s in lst[:5])
    return f'''<section id="overview">
{options_card(opt, mkt)}
<div class="card ov"><h2>今天 {len(stocks)} 檔的樣子</h2>
<p class="note2">加權指數距三個月高點 {mkt["dd"]:+.1f}%。{len(stocks)} 檔裡，各類別各有幾檔：</p>
<div class="seg">{seg}</div><div class="legend">{legend}</div></div>
<div class="card ov"><h2>錯殺地圖</h2>
<p class="note2">每個點是一檔股票。越往左跌越多，越往上基本面越好。<b>左上角</b>＝跌很多、公司卻還在成長，最可能是錯殺；<b>左下角</b>＝跌有理由。點一下圓點看是哪一檔。</p>
{svg}<p class="tip" id="tip">點圓點看名稱</p></div>
<div class="duo">
<div class="card ov"><h2>錯殺前五名</h2><ol class="mini">{mini(groups["oversold"], "oversold")}</ol></div>
<div class="card ov"><h2>轉弱前五名</h2><ol class="mini weak">{mini(groups["weak"], "weak")}</ol></div>
</div>
<div class="card ov"><h2>各產業跌多少</h2>
<p class="note2">只列追蹤的股票裡至少有 2 檔的產業，跌最多的在上面。同一個產業如果「錯殺」多、「轉弱」少，代表是整個族群被一起賣，不是基本面出問題。</p>
{table}</div>
</section>'''


def signals_html(sig):
    e = html.escape
    rows = ""
    for x in sorted(sig, key=lambda x: x["date"], reverse=True):
        perf = "".join(f'<div class="chip {tone(x.get(k))}"><span>{lab}</span><b>{fmt(x.get(k))}</b></div>'
                       for k, lab in (("d5", "5 天後"), ("d20", "20 天後"), ("d60", "60 天後"), ("now", "到今天")))
        rs = "".join(f"<li class='mid'><i>·</i>{e(t)}</li>" for t in x["reasons"])
        rows += f'''<article class="card stock sig {'oversold' if x['type'] == '錯殺' else 'weak'}">
  <div class="head"><div class="nm"><h3>{e(x["name"])} <em>{x["code"]}</em></h3>
  <div class="ind"><span class="tag">{x["type"]}{x.get("stage", "") if x["type"] == "錯殺" else ""}・強度{x["strength"]}</span>{x["date"][5:].replace("-", "/")} 收盤 {x["price"]:,.2f}</div></div>
  <div class="px"><div class="dd">{x["dd"]:+.1f}<small>%</small></div><div class="p">當時距高點</div></div></div>
  <div class="chips">{perf}</div>
  <details><summary>當時的理由</summary><ul class="why">{rs}</ul></details>
</article>'''
    lead = ("只有訊號特別明確時才會記錄在這裡，並傳 Telegram 通知：<br>"
            "<b>錯殺初步</b>＝第一天符合：在錯殺名單、基本面分數 ≥ 5、估值在近三年最便宜的 10%、外資或投信近 5 日買超（特別強的當天就通知）。<br>"
            "<b>錯殺確認</b>＝連續 3 天都符合，一定通知。兩種都記錄之後的漲跌，用來比較快進和等確認哪個好。<br>"
            "<b>轉弱</b>＝前一天還不是轉弱，新公布的營收或財報讓它亮紅燈。<br>之後 5、20、60 個交易日的漲跌會自動補上，用來檢驗這套方法準不準。")
    return f'<section id="signals"><p class="lead">{lead}</p>{rows or "<p class=empty>還沒有明確訊號。每天收盤後檢查，出現時會通知你。</p>"}</section>'


HOW = '''<section id="how"><div class="card how">
<h2>這個頁面在做什麼</h2>
<p>大盤急跌時，好公司和壞公司常常一起被賣。這裡每天收盤後，把<b>市值前 150 大的上市公司</b>（大約就是台灣 50 加中型 100）和<b>市值前 150 大的上櫃公司</b>，共 300 檔逐一檢查，看股價跌了多少、公司本身有沒有變差，分成三類。</p>
<h2>看哪些東西</h2>
<ol>
<li><b>跌幅</b>：現在股價距離近三個月最高點跌了多少。跌超過 {{DROP}}% 才算「跌很多」。</li>
<li><b>月營收年增</b>：最新一個月的營收跟去年同月比。公司生意有沒有變好，這是最快看到的數字（每月 10 號前公布）。</li>
<li><b>今年累計營收</b>：今年到目前為止的營收跟去年同期比，避免單月起伏太大。</li>
<li><b>EPS 年增</b>：最新一季每股賺多少，跟去年同一季比。季對季比較會受淡旺季影響，所以跟去年同季比。</li>
<li><b>毛利率</b>：每賣 100 元賺多少毛利。毛利率往下掉，常代表產品賣不到好價錢或成本變高。</li>
<li><b>本益比位置</b>：現在的本益比，放在這家公司過去三年的本益比裡排第幾。「20 分位」代表過去三年只有 20% 的時間比現在便宜。金融股等獲利波動大的公司改用股價淨值比。</li>
<li><b>大盤要不要避險</b>：看五個警訊——台指期是否跌破近 60 日「成交量分佈」的密集區、Put/Call 成交比是否比平常高、選擇權總未平倉是否快速增加且偏向賣權、外資選擇權是否偏空、加權指數是否在 20 日均線下。2 個亮起是「留意」，3 個以上「建議避險」。</li>
<li><b>成交量分佈</b>：把一段時間每天的成交量，平均分到當天最低到最高價之間，累加起來看哪個價位成交最多。沒有免費的內外盤資料，所以只看總量。股價跌破密集區，代表很多人買在上面、正在虧損，賣壓容易變大。</li>
<li><b>權證（散戶情緒）</b>：每檔股票當天認購、認售權證的成交金額（上市＋上櫃）。認售佔比突然比平常高很多，代表散戶很悲觀；基本面好卻被這樣看壞，常是錯殺的特徵。只當參考，不計分。</li>
<li><b>法人動向</b>：近 5 個交易日（{{INST}}）外資和投信買超或賣超多少錢。不影響分類，但會算進入手分數。</li>
</ol>
<h2>入手分數（0–100）</h2>
<p>同一類裡面誰比較值得先研究？每檔都會算一個入手分數，五個面向加起來：</p>
<ul>
<li><b>基本面 30 分</b>：上面的基本面分數，7 分以上拿滿。</li>
<li><b>便宜程度 25 分</b>：本益比（或股價淨值比）在過去三年越便宜，分數越高。</li>
<li><b>同業比較 15 分</b>：優於同業 15、跟同業差不多 8、落後同業 0。</li>
<li><b>未來需求 15 分</b>：從 7 分開始，客戶預付款大增、貨賣得比進得快、新聞面需求增溫會加分；存貨堆積、預付款大減、新聞面需求轉弱會扣分。</li>
<li><b>法人動向 15 分</b>：外資和投信都買 15、合計買超 10、只有一方買 5、都在賣 0。</li>
</ul>
<p>85 分以上是 <b>A 優先研究</b>、70–84 <b>B 值得觀察</b>、55–69 <b>C 普通</b>、55 以下 <b>D 先別碰</b>。「可能錯殺」和「跌深・不便宜」都照分數由高到低排。分數是幫你排研究順序，不是買進建議；大盤避險燈號亮時，分數高的也可能跟著大盤跌。</p>
<h2>怎麼分類</h2>
<p>營收、EPS、毛利率每一項做得好加分、變差扣分，加總成「基本面分數」。</p>
<ul>
<li><b>可能錯殺</b>：跌超過 {{DROP}}%，基本面分數 ≥ 3，沒有紅燈，而且本益比（或股價淨值比）落在過去三年最便宜的 30% 區間。</li>
<li><b>跌深・不便宜</b>：跌超過 {{DROP}}%，基本面也還好，但估值不算低。</li>
<li><b>基本面轉弱</b>：基本面分數 ≤ −2，或亮紅燈（最新一季虧損、單月營收衰退超過 20%、EPS 年減超過 30%）。</li>
<li><b>其他</b>：好壞參半，或基本面好但還沒跌多少。</li>
<li><b>多頭模式</b>：大盤離三個月高點不到 5% 時自動切換。好股票在漲勢中通常只回檔 8～10%，等不到 15%，所以改成「比大盤多跌 10% 以上、自己至少跌 8%」也算跌深。大盤跌超過 5% 時自動切回「修正模式」，只看跌超過 {{DROP}}%。</li>
</ul>
<p>「可能錯殺」的排序，綜合了基本面分數、有多便宜、法人有沒有買、跌了多深。</p>
<h2>要注意</h2>
<p>金融股的月營收含投資損益，起伏很大，所以不拿來評分，改看 EPS 和股價淨值比。</p>
<p>景氣循環股（記憶體、面板、航運、塑化）在獲利最好的時候，本益比會看起來特別低，但那可能是景氣高點，不一定真的便宜。</p>
<p>財報是落後資料，股價可能已經在反映「下一季會變差」，這是篩選工具看不到的。產業的利空（例如關稅、需求轉弱）也不會出現在過去的數字裡。點進每檔股票，再看新聞和公司說明，才做決定。</p>
<p class="src">資料來源：臺灣證券交易所 OpenAPI（公開資訊觀測站月營收、收盤、三大法人）、臺灣期貨交易所（台指選擇權）、FinMind（每季財報、歷史本益比）、Yahoo 股市（每日股價）。</p>
<p class="disc">這只是用公開數字做的篩選，不是投資建議。</p>
</div></section>'''


# ───────────────────────── 長期追蹤 ─────────────────────────

def history_days():
    d = os.path.join(ROOT, "data", "history")
    os.makedirs(d, exist_ok=True)
    return sorted(f[:-5] for f in os.listdir(d) if f.endswith(".json"))


def track(stocks, date, after_close):
    """存每日快照、算連續入選天數、產生明確訊號、回填訊號之後的漲跌"""
    days = [d for d in history_days() if d < date]
    past = {d: load(f"data/history/{d}.json", {}) for d in days[-30:]}
    prev = past[days[-1]] if days else {}
    for s in stocks:
        n = 1 if s["cat"] == "oversold" else 0
        if n:
            for d in reversed(days[-30:]):
                if past[d].get(s["code"], {}).get("c") == "oversold":
                    n += 1
                else:
                    break
        s["streak"] = n
    if after_close:
        snap = {s["code"]: {"c": s["cat"], "p": s["price"], "f": s["fund"]} for s in stocks}
        snap["_w"] = {s["code"]: WAR[s["code"]]["today"] for s in stocks if s["code"] in WAR}
        save(f"data/history/{date}.json", snap)

    sig = load("data/signals.json", [])
    by_code = {x["code"]: x for x in stocks}
    # 回填之後的表現（用每天的快照價格）
    alld = history_days()
    for x in sig:
        if x["date"] not in alld:
            continue
        i = alld.index(x["date"])
        for n in (5, 20, 60):
            k = f"d{n}"
            if x.get(k) is None and i + n < len(alld):
                px = load(f"data/history/{alld[i + n]}.json", {}).get(x["code"], {}).get("p")
                if px:
                    x[k] = round((px / x["price"] - 1) * 100, 1)
        cur = by_code.get(x["code"])
        if cur:
            x["now"] = round((cur["price"] / x["price"] - 1) * 100, 1)

    new = []
    if after_close and prev:
        recent = {(x["code"], x["type"], x.get("stage", "確認")) for x in sig if x["date"] >= (dt.date.fromisoformat(date) - dt.timedelta(days=30)).isoformat()}
        for s in stocks:
            rank = s.get("pe_rank") if s.get("pe_rank") is not None else s.get("pb_rank")
            buying = (s.get("foreign") or 0) + (s.get("trust") or 0) > 0   # 外資＋投信近 5 日合計買超
            kind, stage = None, "確認"
            lag = (s.get("peer") or {}).get("rel") == "落後同業" or "轉弱" in (s.get("outlook") or {}).get("view", "")
            good = s["cat"] == "oversold" and s["fund"] >= 5 and rank is not None and rank <= 0.1 and buying and not lag
            if good and s["streak"] >= 3:
                kind = "錯殺"
            elif good:
                kind, stage = "錯殺", "初步"
            was = prev.get(s["code"], {}).get("c")
            if s["cat"] == "weak" and was and was != "weak" and s["flags"]:
                kind, stage = "轉弱", "確認"
            if not kind or (s["code"], kind, stage) in recent:
                continue
            x = {"id": f'{date}-{s["code"]}-{kind}{stage}', "date": date, "stage": stage, "code": s["code"], "name": s["name"], "type": kind,
                 "industry": s.get("industry"), "price": s["price"], "dd": s["dd"], "fund": s["fund"], "streak": s["streak"],
                 "rev_yoy": s.get("rev_yoy"), "eps_yoy": s.get("eps_yoy"), "gm": s.get("gm"), "gm_yoy": s.get("gm_yoy"),
                 "pe": s.get("pe"), "pb": s.get("pb"), "val_rank": rank, "foreign": s.get("foreign"), "trust": s.get("trust"),
                 "flags": s["flags"], "reasons": [t for _, t in s["reasons"]], "rev6": s.get("rev6"), "q5": s.get("q5"),
                 "story": s.get("story"), "peer": (s.get("peer") or {}).get("text"), "peer_rel": (s.get("peer") or {}).get("rel"),
                 "demand": s.get("demand"), "outlook": s.get("outlook"), "score": s.get("score"), "grade": s.get("grade"),
                 "score_parts": s.get("score_parts")}
            x["strength"] = "強" if kind == "轉弱" or (s["fund"] >= 6 and rank <= 0.05) else "中"
            if OPT.get("hedge"):
                pcv = (OPT.get("pc") or [[0, None]])[-1][1]
                vpm = OPT.get("vp") or {}
                x["opt_note"] = (f"避險燈號：{OPT['hedge']['level']}" + (f"・台指期密集區 {vpm['val']:,.0f}–{vpm['vah']:,.0f}" if vpm else "")
                                 + (f"・P/C 成交比 {pcv:.0f}%" if pcv else ""))
            # 初步訊號只有「強」才當天推播；確認訊號一律推播
            x["notify"] = stage == "確認" or x["strength"] == "強"
            sig.append(x)
            new.append(x)
    save("data/signals.json", sig)
    return sig, new


# ───────────────────────── Telegram ─────────────────────────

def send_telegram(text):
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not chat:
        p = os.path.join(ROOT, "data", "chat_id")
        chat = open(p).read().strip() if os.path.exists(p) else ""
    if not token or not chat:
        print("telegram not configured")
        return False
    data = urllib.parse.urlencode({"chat_id": chat, "text": text, "parse_mode": "HTML",
                                   "disable_web_page_preview": "true"}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=20)
        return True
    except Exception as e:
        print("telegram error:", e, file=sys.stderr)
        return False


OPT = {}
WAR = {}


def push(new, mkt, date, url):
    e = html.escape
    new = [x for x in new if x.get("notify", True)]
    if len(new) > 5:   # 一次太多就合成一則
        lines = [f"<b>📌 台股分析 {date[5:].replace('-', '/')}：{len(new)} 個新訊號</b>", ""]
        for x in new:
            lines.append(f"・{'🟢' if x['type'] == '錯殺' else '🔻'}{x['type']}{x.get('stage', '')}｜{e(x['name'])} {x['code']}　跌{x['dd']:.0f}%")
        lines += ["", f'<a href="{url}stocks.html#signals">看每檔的理由</a>　（篩選結果，不是投資建議）']
        send_telegram("\n".join(lines))
        return
    for x in new:
        icon = (f"🟢 可能錯殺・{x.get('stage', '確認')}訊號") if x["type"] == "錯殺" else "🔻 基本面轉弱"
        lines = [f"<b>{icon}｜{e(x['name'])} {x['code']}</b>（訊號強度：{x['strength']}）",
                 f"{date[5:].replace('-', '/')} 收盤 {x['price']:,.2f}，距三個月高點 {x['dd']:+.1f}%"
                 + (f"，連續 {x['streak']} 天入選" if x["type"] == "錯殺" else ""), ""]
        lines += [f"・{e(t)}" for t in x["reasons"]]
        if x.get("rev6"):
            lines.append("・近月營收年增：" + "、".join(f"{m}月{v:+.0f}%" for m, v in x["rev6"]))
        lines += ["", f"大盤：加權 {mkt['last']:,.0f}，距高點 {mkt['dd']:+.1f}%" + (
                  f"；大盤避險燈號：{OPT['hedge']['level']}" if OPT.get("hedge") else ""),
                  f'<a href="{url}stocks.html#signals">看訊號紀錄</a>　（篩選結果，不是投資建議）']
        send_telegram("\n".join(lines))
        time.sleep(1)


# ───────────────────────── main ─────────────────────────

def main():
    now = dt.datetime.now(TZ)
    today = now.date()
    uni = universe()
    if not uni:
        sys.exit("拿不到股票清單")
    print("universe", len(uni))

    idx = yahoo("^TWII")
    ic = [c for _, c in idx]
    mkt = {"last": ic[-1], "high": max(ic[-63:]), "chg1": round((ic[-1] / ic[-2] - 1) * 100, 2)}
    mkt["dd"] = round((mkt["last"] / mkt["high"] - 1) * 100, 1)
    last_date = idx[-1][0]

    rev = {r["公司代號"].strip(): r for r in (get("https://openapi.twse.com.tw/v1/opendata/t187ap05_L") or [])}
    rev_o = get("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O") or []
    if rev_o:
        print("tpex revenue keys", list(rev_o[0].keys())[:20])
    for r in rev_o:
        if r.get("公司代號"):
            rev.setdefault(str(r["公司代號"]).strip(), r)

    cache = load("data/stock_cache.json", {})
    fm = FinMind()
    cache = refresh_cache([u["code"] for u in uni], cache, fm, today)
    cache = refresh_bs([u["code"] for u in uni], cache, fm, today)
    save("data/stock_cache.json", cache)

    inst, inst_days = institutional([d for d, _ in idx[-5:]])
    tpex_institutional([d for d, _ in idx[-5:]], inst)
    print("inst days", inst_days)

    name2code = {u["name"]: u["code"] for u in uni}
    war = warrants(last_date, name2code)
    if not war:
        war = warrants(idx[-2][0], name2code)
    wdays = [d for d in history_days() if d < last_date][-5:]
    past = [load(f"data/history/{d}.json", {}).get("_w", {}) for d in wdays]
    for code, v in war.items():
        shares = [p[code][1] / sum(p[code]) * 100 for p in past if code in p and sum(p[code]) > 0]
        WAR[code] = {"today": v, "avg": round(sum(shares) / len(shares)) if shares else None}
    print("warrant underlyings", len(war))

    # 補算：還沒有收盤紀錄的前幾個交易日，用當天以前的股價重新分類（基本面、估值資料相同）
    back = [d for d, _ in idx[-11:] if d < last_date]
    backfill = {d: load(f"data/history/{d}.json", {"_bf": 1}) for d in back}
    n_before = {d: len(v) for d, v in backfill.items()}

    stocks = []
    for u in uni:
        h, hlv = yahoo(u["code"] + "." + u.get("mkt", "TW"), full=True)
        u["vp"] = vprofile(hlv[-60:], 16)
        time.sleep(0.3)
        if len(h) < 20:
            print("no price", u["code"], file=sys.stderr)
            continue
        u["hist"] = h
        try:
            s = analyse(dict(u), rev, cache, inst, mkt["dd"])
        except Exception as ex:
            print("analyse error", u["code"], ex, file=sys.stderr)
            continue
        for d in back:
            if u["code"] in backfill[d] or "per_last" not in cache.get(u["code"], {}):
                continue   # 已經有紀錄，或財報還沒抓到（等抓到再補，免得補成錯的分類）
            hh = [x for x in h if x[0] <= d]
            if len(hh) < 20 or hh[-1][0] != d:
                continue
            try:
                u2 = dict(u, hist=hh, hist_all=h, vp=None)
                b_ = analyse(u2, rev, cache, {}, 0)
                backfill[d][u["code"]] = {"c": b_["cat"], "p": b_["price"], "f": b_["fund"]}
            except Exception as ex:
                print("backfill error", d, u["code"], ex, file=sys.stderr)
        s.pop("hist", None)
        s.pop("hlv", None)
        stocks.append(s)
    for d, snap in backfill.items():
        if len(snap) > n_before[d] and len(snap) > 50:
            save(f"data/history/{d}.json", snap)
    print("backfilled", {d: len(v) - n_before[d] for d, v in backfill.items()})

    opt = {}
    for d in [x for x, _ in idx[-3:]][::-1]:   # 當天資料還沒出來就用前一個交易日
        opt = options(d, mkt["last"])
        if opt.get("oi"):
            break
    opt["hedge"] = hedge(opt, mkt, ic)
    OPT.update(opt)
    print("options", {k: v for k, v in opt.items() if k not in ("bins", "pc")})
    save("data/options.json", opt)


    after_close = last_date == today.isoformat() and now.hour * 100 + now.minute >= 1430
    peer_compare(stocks)
    apply_outlook(stocks, load("data/outlook.json", {}))
    for s in stocks:
        story(s, (opt.get("hedge") or {}).get("level")); buy_score(s)
    sig, new = track(stocks, last_date, after_close)
    print("signals new", [(x["name"], x["type"]) for x in new])

    updated = now.strftime("%m/%d %H:%M")
    groups = render(stocks, mkt, updated, inst_days, sig, opt)
    save("data/stocks.json", {"updated": now.isoformat(), "market": mkt, "stocks": stocks})
    print({k: len(v) for k, v in groups.items()})

    if new and os.environ.get("PUSH") == "1":
        push(new, mkt, last_date, os.environ.get("PAGE_URL", "https://wu-ting-yen.github.io/daily-news/"))


if __name__ == "__main__":
    main()
