#!/usr/bin/env python3
"""錯殺股雷達：每天收盤後，把台灣市值前 150 大上市公司（約等於台灣50＋中型100）
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
import os
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
TZ = dt.timezone(dt.timedelta(hours=8))
UA = {"User-Agent": "Mozilla/5.0 (daily-news screener)"}
UNIVERSE = 150          # 市值前 150 大
DROP_LINE = -15.0       # 跌幅門檻（距近三個月高點）
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

def universe():
    basic = get("https://openapi.twse.com.tw/v1/opendata/t187ap03_L") or []
    day = get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL") or []
    close = {d["Code"]: num(d["ClosingPrice"]) for d in day}
    rows = []
    for b in basic:
        code = b["公司代號"].strip()
        if not (len(code) == 4 and code.isdigit()) or code.startswith(("0", "91")):
            continue
        shares, px = num(b.get("已發行普通股數或TDR原股發行股數")), close.get(code)
        if shares and px:
            rows.append({"code": code, "name": b["公司簡稱"].strip(), "mcap": shares * px})
    rows.sort(key=lambda r: -r["mcap"])
    return rows[:UNIVERSE]


def yahoo(symbol, rng="6mo"):
    j = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?range={rng}&interval=1d")
    try:
        r = j["chart"]["result"][0]
        out = []
        for t, c in zip(r["timestamp"], r["indicators"]["quote"][0]["close"]):
            if c is not None:
                out.append((dt.datetime.fromtimestamp(t, TZ).strftime("%Y-%m-%d"), round(c, 2)))
        return out
    except Exception:
        return []


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
        if c.get("at", "") >= (today - dt.timedelta(days=CACHE_DAYS)).isoformat():
            continue
        fin = fm("TaiwanStockFinancialStatements", code, start_fin)
        per = fm("TaiwanStockPER", code, start_per) if fin is not None else None
        if fin is None or per is None:
            break
        c = {"at": today.isoformat(), "q": quarterly(fin)}
        if per:
            c["pe_p"] = pctiles([r["PER"] for r in per])
            c["pb_p"] = pctiles([r["PBR"] for r in per])
            last = per[-1]
            c["per_last"] = {"date": last["date"], "PER": last["PER"], "PBR": last["PBR"]}
        cache[code] = c
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

    # 3) 估值：跟自己過去三年比
    pl = c.get("per_last")
    base = dict(hist).get(pl["date"]) if pl else None
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
                reasons.append(("good", f"{what} {val:.1f} 倍，比過去三年 {cheaper}% 的時間都便宜"))
            elif rank >= 0.8:
                reasons.append(("bad", f"{what} {val:.1f} 倍，跌完仍在過去三年偏貴的位置"))
            else:
                reasons.append(("mid", f"{what} {val:.1f} 倍，在過去三年的中間位置"))

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
    if flags or score <= -2:
        s["cat"] = "weak"
    elif dd <= DROP_LINE and score >= 2:
        s["cat"] = "oversold"
    elif score >= 2:
        s["cat"] = "strong"     # 基本面好、跌得不多
    else:
        s["cat"] = "mixed"
    s["rank"] = round(score + (2 if cheap else 0) + (1 if smart else 0) + min(-dd, 40) / 10, 2)
    s["vs_mkt"] = round(dd - mkt_dd, 1)
    return s


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
        chips.append(("本益比", f"{s['pe']:.1f}" + (f" <small>近3年{round(r * 100)}分位</small>" if r is not None else ""),
                      "up" if r is not None and r <= 0.3 else "down" if r is not None and r >= 0.8 else ""))
    elif s.get("pb"):
        r = s.get("pb_rank")
        chips.append(("股價淨值比", f"{s['pb']:.2f}" + (f" <small>近3年{round(r * 100)}分位</small>" if r is not None else ""),
                      "up" if r is not None and r <= 0.3 else ""))
    chip_html = "".join(f'<div class="chip {t}"><span>{a}</span><b>{b}</b></div>' for a, b, t in chips)
    marks = {"good": "○", "bad": "×", "mid": "·"}
    rs = "".join(f'<li class="{k}"><i>{marks[k]}</i>{e(t)}</li>' for k, t in s["reasons"])
    flag = "".join(f'<span class="flag">{e(f)}</span>' for f in s["flags"])
    badge = ""
    if s["cat"] == "oversold":
        n = 1 + (1 if s["cheap"] else 0) + (1 if (s.get("trust") or 0) > 0.5 or (s.get("foreign") or 0) > 2 else 0)
        badge = f'<span class="gems" title="錯殺指數">{"◆" * n}{"◇" * (3 - n)}</span>'
    rank = f'<span class="no">{i:02d}</span>' if i else ""
    return f'''<article class="card stock {s["cat"]}">
  <a class="head" href="https://tw.stock.yahoo.com/quote/{s["code"]}.TW" target="_blank" rel="noopener">
    {rank}<div class="nm"><h3>{e(s["name"])} <em>{s["code"]}</em></h3><div class="ind">{e(s.get("industry", ""))}{flag}</div></div>
    <div class="px"><div class="dd">{s["dd"]:+.1f}<small>%</small></div><div class="p">{s["price"]:,.2f}・距三個月高點</div></div>
  </a>
  <div class="mid">{spark_svg(s["spark"])}{badge}</div>
  <div class="chips">{chip_html}</div>
  <details><summary>為什麼這樣判斷</summary><ul class="why">{rs}</ul></details>
</article>'''


def render(stocks, mkt, updated, inst_days):
    groups = {
        "oversold": sorted([s for s in stocks if s["cat"] == "oversold"], key=lambda s: -s["rank"]),
        "weak": sorted([s for s in stocks if s["cat"] == "weak"], key=lambda s: s["fund"] * 10 + s["dd"]),
        "others": sorted([s for s in stocks if s["cat"] in ("strong", "mixed")], key=lambda s: (s["cat"] != "strong", -s["fund"], s["dd"])),
    }
    intro = {
        "oversold": "跌超過 15%，但營收、獲利、毛利率大多還在成長。股價跌可能是被大盤拖累，不是公司本身變差。◆ 越多代表越便宜、法人也在買。",
        "weak": "營收或獲利已經在衰退、毛利率變差，或最新一季虧損。這些跌比較「有理由」，抄底要更小心。",
        "others": "基本面好壞參半，或基本面不錯但跌得還不多（抗跌）。",
    }
    names = {"oversold": "可能錯殺", "weak": "基本面轉弱", "others": "其他"}
    nav = "".join(f'<button data-tab="{k}">{names[k]}<b>{len(v)}</b></button>' for k, v in groups.items())
    nav += '<button data-tab="how">怎麼判斷</button>'
    body = ""
    for k, v in groups.items():
        cards = "".join(card(s, i + 1 if k == "oversold" else None) for i, s in enumerate(v))
        body += f'<section id="{k}"><p class="lead">{intro[k]}</p>{cards or "<p class=empty>今天沒有符合的股票</p>"}</section>'
    body += HOW.replace("{{DROP}}", str(int(-DROP_LINE))).replace("{{INST}}", "、".join(d[5:].replace("-", "/") for d in inst_days) or "—")
    n_drop = sum(1 for s in stocks if s["dd"] <= DROP_LINE)
    summary = (f'<div class="mk"><div><span class="lbl">加權指數</span><b>{mkt["last"]:,.0f}</b>'
               f'<small class="{tone(mkt["chg1"])}">{mkt["chg1"]:+.2f}%</small></div>'
               f'<div><span class="lbl">距三個月高點</span><b>{mkt["dd"]:+.1f}%</b><small>高點 {mkt["high"]:,.0f}</small></div>'
               f'<div><span class="lbl">跌超過 {int(-DROP_LINE)}%</span><b>{n_drop}</b><small>/ {len(stocks)} 檔</small></div></div>')
    tpl = open(os.path.join(ROOT, "stocks_template.html"), encoding="utf-8").read()
    out = tpl.replace("{{UPDATED}}", updated).replace("{{NAV}}", nav).replace("{{BODY}}", body).replace("{{SUMMARY}}", summary)
    with open(os.path.join(ROOT, "stocks.html"), "w", encoding="utf-8") as f:
        f.write(out)
    return groups


HOW = '''<section id="how"><div class="card how">
<h2>這個頁面在做什麼</h2>
<p>大盤急跌時，好公司和壞公司常常一起被賣。這裡每天收盤後，把<b>市值前 150 大的上市公司</b>（大約就是台灣 50 加中型 100）逐一檢查，看股價跌了多少、公司本身有沒有變差，分成三類。</p>
<h2>看哪些東西</h2>
<ol>
<li><b>跌幅</b>：現在股價距離近三個月最高點跌了多少。跌超過 {{DROP}}% 才算「跌很多」。</li>
<li><b>月營收年增</b>：最新一個月的營收跟去年同月比。公司生意有沒有變好，這是最快看到的數字（每月 10 號前公布）。</li>
<li><b>今年累計營收</b>：今年到目前為止的營收跟去年同期比，避免單月起伏太大。</li>
<li><b>EPS 年增</b>：最新一季每股賺多少，跟去年同一季比。季對季比較會受淡旺季影響，所以跟去年同季比。</li>
<li><b>毛利率</b>：每賣 100 元賺多少毛利。毛利率往下掉，常代表產品賣不到好價錢或成本變高。</li>
<li><b>本益比位置</b>：現在的本益比，放在這家公司過去三年的本益比裡排第幾。「20 分位」代表過去三年只有 20% 的時間比現在便宜。金融股等獲利波動大的公司改用股價淨值比。</li>
<li><b>法人動向</b>：近 5 個交易日（{{INST}}）外資和投信買超或賣超多少錢。只當參考，不計分。</li>
</ol>
<h2>怎麼分類</h2>
<p>營收、EPS、毛利率每一項做得好加分、變差扣分，加總成「基本面分數」。</p>
<ul>
<li><b>可能錯殺</b>：跌超過 {{DROP}}%，基本面分數 ≥ 2，而且沒有紅燈。</li>
<li><b>基本面轉弱</b>：基本面分數 ≤ −2，或亮紅燈（最新一季虧損、單月營收衰退超過 20%、EPS 年減超過 30%）。</li>
<li><b>其他</b>：好壞參半，或基本面好但還沒跌多少。</li>
</ul>
<p>「可能錯殺」的排序，綜合了基本面分數、有多便宜、法人有沒有買、跌了多深。</p>
<h2>要注意</h2>
<p>財報是落後資料，股價可能已經在反映「下一季會變差」，這是篩選工具看不到的。產業的利空（例如關稅、需求轉弱）也不會出現在過去的數字裡。點進每檔股票，再看新聞和公司說明，才做決定。</p>
<p class="src">資料來源：臺灣證券交易所 OpenAPI（公開資訊觀測站月營收、收盤、三大法人）、FinMind（每季財報、歷史本益比）、Yahoo 股市（每日股價）。</p>
<p class="disc">這只是用公開數字做的篩選，不是投資建議。</p>
</div></section>'''


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


def push(groups, mkt, date, url):
    sent = load("data/stocks_pushed.json", {})
    if sent.get("date") == date:
        return
    e = html.escape
    lines = [f"<b>📉 錯殺股雷達 {date[5:].replace('-', '/')}</b>",
             f"加權 {mkt['last']:,.0f}（{mkt['chg1']:+.2f}%），距高點 {mkt['dd']:+.1f}%", ""]
    top = groups["oversold"][:6]
    if top:
        lines.append("<b>可能錯殺</b>")
        for s in top:
            why = []
            if s.get("rev_yoy") is not None:
                why.append(f"營收{s['rev_yoy']:+.0f}%")
            if s.get("eps_yoy") is not None:
                why.append(f"EPS{s['eps_yoy']:+.0f}%")
            if s.get("pe_rank") is not None and s["cheap"]:
                why.append("本益比偏低")
            lines.append(f"・{e(s['name'])} {s['code']}　跌{s['dd']:.0f}%｜" + "、".join(why))
    weak = groups["weak"][:4]
    if weak:
        lines += ["", "<b>基本面轉弱</b>"]
        for s in weak:
            lines.append(f"・{e(s['name'])} {s['code']}　跌{s['dd']:.0f}%｜" + ("、".join(s["flags"]) or "營收或獲利衰退"))
    lines += ["", f'<a href="{url}stocks.html">看完整清單與理由</a>', "（篩選結果，不是投資建議）"]
    if send_telegram("\n".join(lines)):
        save("data/stocks_pushed.json", {"date": date})


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

    cache = load("data/stock_cache.json", {})
    cache = refresh_cache([u["code"] for u in uni], cache, FinMind(), today)
    save("data/stock_cache.json", cache)

    inst, inst_days = institutional([d for d, _ in idx[-5:]])
    print("inst days", inst_days)

    stocks = []
    for u in uni:
        h = yahoo(u["code"] + ".TW")
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
        s.pop("hist", None)
        stocks.append(s)

    updated = now.strftime("%m/%d %H:%M")
    groups = render(stocks, mkt, updated, inst_days)
    save("data/stocks.json", {"updated": now.isoformat(), "market": mkt, "stocks": stocks})
    print({k: len(v) for k, v in groups.items()})

    if os.environ.get("PUSH") == "1" and last_date == today.isoformat():
        push(groups, mkt, last_date, os.environ.get("PAGE_URL", "https://wu-ting-yen.github.io/daily-news/"))


if __name__ == "__main__":
    main()
