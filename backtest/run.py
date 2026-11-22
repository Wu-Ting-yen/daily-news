#!/usr/bin/env python3
"""融資流動性進出場回測（2001 至今）→ data/bt/result.json

需要：data/bt/margin/*.json、bt_raw/px.json.gz、bt_raw/meta.json、bt_raw/fund_*.json、
      bt_raw/taiex.json、bt_raw/inst.json（fetch_market.py）
規則都寫在 RULES，頁面會照這裡的數字說明。
"""
import bisect, glob, gzip, json, os, statistics as st, datetime as dt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "bt_raw")

RULES = dict(
    dd_line=-10.0,        # 指數從 60 日高點跌超過 10%
    margin_drop=-8.0,     # 上市融資金額 10 日減少超過 8%
    mr_line=145.0,        # 估算維持率低於 145%
    calm_3d=-1.0,         # 進場：融資 3 日減幅縮小到 1% 以內
    mr_rebound=5.0,       # 進場：維持率比低點回升 5 個百分點
    stress_wait=60,       # 風險出現後最多等 60 個交易日
    picks=10,             # 選 10 檔，每檔 10%
    first=0.5,            # 第一批 50%
    second_days=40,       # 40 個交易日內站回 20 日線再投第二批
    fell_more=5.0,        # 個股比大盤多跌 5 個百分點以上
    cheap=0.85,           # 預估本益比 ≤ 自己 5 年中位數 × 0.85 才買
    fair=1.0,             # 回到 5 年中位數 = 合理價 → 出場
    heat_mr=170.0,        # 維持率 > 170% …
    stop=-10.0,           # 指數比恐慌低點再跌 10% → 停損
    max_days=500,         # 最多持有約 2 年
    cost=0.585,           # 手續費＋證交稅 來回 %
)
R = RULES
D0 = dt.date(1970, 1, 1)


def day(s):
    return (dt.date.fromisoformat(s) - D0).days


def iso(n):
    return (D0 + dt.timedelta(days=n)).isoformat()


# ---------- 讀資料 ----------
px = json.load(gzip.open(os.path.join(RAW, "px.json.gz"), "rt"))
meta = json.load(open(os.path.join(RAW, "meta.json"), encoding="utf-8"))
fund = {}
for p in glob.glob(os.path.join(RAW, "fund_*.json")):
    fund.update(json.load(open(p)))
taiex = {day(k): v for k, v in json.load(open(os.path.join(RAW, "taiex.json"))).items()}
inst = {day(k): v for k, v in json.load(open(os.path.join(RAW, "inst.json"))).items()}
margin = {}
for p in sorted(glob.glob(os.path.join(ROOT, "data", "bt", "margin", "*.json"))):
    for k, v in json.load(open(p)).items():
        margin[day(k)] = v


def close_on(c, d, adj=False):
    D = px[c][0]
    k = bisect.bisect_right(D, d)
    if k == 0 or D[k - 1] < d - 10:
        return None
    return px[c][1 if adj else 2][k - 1]


# 2004/2 以前沒有全部收盤價：用 Yahoo 收盤補擔保品市值
for d, v in margin.items():
    tw = v.get("tw") or {}
    per = tw.get("per")
    if per and tw.get("money"):
        coll = 0.0
        for c, n in per.items():
            p = close_on(c, d) if c in px else None
            if p:
                coll += n * 1000 * p
        tw["coll"] = coll

days = sorted(d for d in margin if d in taiex or (margin[d].get("tw") or {}).get("taiex"))
S = []  # 每日市場序列
for d in days:
    tw = margin[d]["tw"]
    otc = margin[d].get("otc") or {}
    ix = taiex.get(d) or tw.get("taiex")
    mr = tw["coll"] / tw["money"] * 100 if tw.get("money") and tw.get("coll") else None
    S.append(dict(d=d, ix=ix, money=tw.get("money"), sh=tw.get("sh"), otc=otc.get("sh"), mr=mr, fi=inst.get(d)))
for i, s in enumerate(S):
    w60 = [x["ix"] for x in S[max(0, i - 59):i + 1]]
    s["dd"] = (s["ix"] / max(w60) - 1) * 100
    s["ma5"] = sum(x["ix"] for x in S[max(0, i - 4):i + 1]) / min(5, i + 1)
    s["ma20"] = sum(x["ix"] for x in S[max(0, i - 19):i + 1]) / min(20, i + 1)
    s["m10"] = (s["money"] / S[i - 10]["money"] - 1) * 100 if i >= 10 and S[i - 10]["money"] else None
    s["m3"] = (s["money"] / S[i - 3]["money"] - 1) * 100 if i >= 3 and S[i - 3]["money"] else None
    s["o10"] = (s["otc"] / S[i - 10]["otc"] - 1) * 100 if i >= 10 and s["otc"] and S[i - 10]["otc"] else None
    f3 = [x["fi"] for x in S[max(0, i - 2):i + 1] if x["fi"] is not None]
    s["f3"] = sum(f3) if len(f3) == 3 else None


# ---------- 個股：預估本益比 ----------
def known_q(c, d):
    """d 這天之前已公布的季報（Q1–Q3 季末後 45 天、Q4 後 90 天）"""
    out = []
    for q, v in (fund.get(c) or {}).get("fs") or []:
        lag = 90 if q[5:7] == "12" else 45
        if day(q) + lag <= d:
            out.append(v)
    return out


def rev_parts(c, d):
    """近 12 個月營收、近 3 個月營收年增率（月營收次月 10 日公布）"""
    rv = (fund.get(c) or {}).get("rev") or []
    t = D0 + dt.timedelta(days=d)
    m = {}
    for y, mo, v in rv:
        if dt.date(y + (mo == 12), mo % 12 + 1, 10) <= t:
            m[(y, mo)] = v
    if len(m) < 15:
        return None, None
    ks = sorted(m)
    r12 = sum(m[k] for k in ks[-12:])
    now = sum(m[k] for k in ks[-3:])
    prev = sum(m.get((k[0] - 1, k[1]), 0) for k in ks[-3:])
    return r12, (now / prev - 1 if prev > 0 else None)


def fwd_eps(c, d):
    """照 sky 的拆解：
    1. 預估未來 4 季營收 = 近 12 個月營收 ×（1＋近 3 個月營收年增率，限 −30%～+50%）
    2. × 近 4 季毛利率 − 近 4 季營業費用率 → 預估營業利益；＋近 4 季業外損益；×（1−近 4 季有效稅率）→ 預估稅後淨利（只算歸屬母公司的比例）
    3. ÷ 最新一季流通股數（母公司淨利 ÷ EPS）→ 預估 EPS"""
    q = known_q(c, d)[-4:]
    if len(q) < 4:
        return None
    r12, g = rev_parts(c, d)
    if not r12 or g is None:
        return None
    def S(k):
        v = [x.get(k) for x in q]
        return sum(v) if all(x is not None for x in v) else None
    rev, gp, opx, oi = S("Revenue"), S("GrossProfit"), S("OperatingExpenses"), S("OperatingIncome")
    nonop, pre = S("TotalNonoperatingIncomeAndExpense"), S("PreTaxIncome")
    tax = S("IncomeTaxExpense") if S("IncomeTaxExpense") is not None else S("TAX")
    ni, par = S("IncomeAfterTaxes"), S("EquityAttributableToOwnersOfParent")
    last = q[-1]
    if not rev or rev <= 0 or not last.get("EPS") or not last.get("EquityAttributableToOwnersOfParent"):
        return None
    shares = last["EquityAttributableToOwnersOfParent"] / last["EPS"]
    if shares <= 0:
        return None
    gm = gp / rev if gp is not None else None
    opr = opx / rev if opx is not None else (gm - oi / rev if gm is not None and oi is not None else None)
    if gm is None or opr is None:
        if oi is None:
            return None
        gm, opr = oi / rev, 0.0
    if nonop is None:
        nonop = (pre - oi) if pre is not None and oi is not None else 0.0
    trate = min(max(abs(tax) / pre, 0.0), 0.4) if tax is not None and pre and pre > 0 else 0.2
    own = min(max(par / ni, 0.0), 1.0) if par is not None and ni and ni > 0 else 1.0
    frev = r12 * (1 + min(max(g, -0.3), 0.5))
    fop = frev * (gm - opr)
    fni = (fop + nonop) * (1 - trate) * own
    return dict(eps=fni / shares, g=g, gm=gm, opr=opr, trate=trate, frev=frev)


def fwd_pe(c, d):
    p = close_on(c, d)
    f = fwd_eps(c, d)
    if not p or not f or f["eps"] <= 0:
        return None, (f["eps"] if f else None), (f["g"] if f else None)
    return p / f["eps"], f["eps"], f["g"]


_med = {}


def pe_median(c, d):
    """過去 5 年每月底的預估本益比中位數"""
    key = (c, d // 30)
    if key in _med:
        return _med[key]
    vals = []
    for k in range(1, 61):
        x = fwd_pe(c, d - 30 * k)[0]
        if x and 0 < x < 200:
            vals.append(x)
    _med[key] = st.median(vals) if len(vals) >= 24 else None
    return _med[key]


def liquid(d, n=300):
    sc = []
    for c, (D, A, C, V) in px.items():
        k = bisect.bisect_right(D, d)
        if k < 60 or D[k - 1] < d - 10:
            continue
        sc.append((sum(V[k - 60:k]) / 60, c))
    sc.sort(reverse=True)
    return [c for _, c in sc[:n]]


def pick(d, mkt_dd):
    out = []
    for c in liquid(d):
        if c not in fund:
            continue
        D, A, C, V = px[c]
        k = bisect.bisect_right(D, d)
        hi = max(C[max(0, k - 60):k])
        sdd = (C[k - 1] / hi - 1) * 100
        if sdd > mkt_dd - R["fell_more"]:
            continue
        pe, e, g = fwd_pe(c, d)
        if not pe or g is None or g <= 0 or sum(x.get("EPS") or 0 for x in known_q(c, d)[-4:]) <= 0:
            continue
        med = pe_median(c, d)
        if not med:
            continue
        ratio = pe / med
        if ratio > R["cheap"]:
            continue
        out.append(dict(c=c, name=meta[c][1], ind=meta[c][2], dd=round(sdd, 1), pe=round(pe, 1), med=round(med, 1),
                        ratio=round(ratio, 2), eps=round(e, 2), g=round(g * 100, 1), p=C[k - 1],
                        gm=round(fwd_eps(c, d)["gm"] * 100, 1), opm=round((fwd_eps(c, d)["gm"] - fwd_eps(c, d)["opr"]) * 100, 1)))
    out.sort(key=lambda x: x["ratio"])
    return out[:R["picks"]]


# ---------- 回測 ----------
episodes = []
i, N = 0, len(S)
while i < N:
    s = S[i]
    if not (s["dd"] <= R["dd_line"] and s["m10"] is not None and s["m10"] <= R["margin_drop"] and s["mr"] and s["mr"] <= R["mr_line"]):
        i += 1
        continue
    st0 = i
    pre_money = S[max(0, i - 20)]["money"]
    low_mr, low_ix = s["mr"], s["ix"]
    entry = None
    j = i
    last_stress = i
    while j < N and j - last_stress <= R["stress_wait"]:
        x = S[j]
        if x["mr"]:
            low_mr = min(low_mr, x["mr"])
        low_ix = min(low_ix, x["ix"])
        if x["dd"] <= R["dd_line"] and x["m10"] is not None and x["m10"] <= R["margin_drop"] and x["mr"] and x["mr"] <= R["mr_line"]:
            last_stress = j
        ok = (x["m3"] is not None and x["m3"] > R["calm_3d"] and x["mr"] and x["mr"] >= low_mr + R["mr_rebound"]
              and x["ix"] > x["ma5"] and (x["f3"] is None or x["f3"] > 0))
        if ok and j > st0:
            entry = j
            break
        j += 1
    if entry is None:
        episodes.append(dict(stress=iso(S[st0]["d"]), entry=None, note="等不到流動性轉好的訊號"))
        i = j + 1
        continue
    e = S[entry]
    picks = pick(e["d"], S[entry]["dd"])
    # 第二批
    second = None
    for k in range(entry + 1, min(N, entry + R["second_days"])):
        if S[k]["ix"] > S[k]["ma20"]:
            second = k
            break
    # 出場：大盤
    mexit, why = None, None
    for k in range(entry + 1, N):
        x = S[k]
        if x["ix"] <= low_ix * (1 + R["stop"] / 100):
            mexit, why = k, "停損：指數比恐慌低點再跌 10%"
            break
        if x["mr"] and x["mr"] >= R["heat_mr"] and x["money"] >= pre_money:
            mexit, why = k, "升溫：維持率 >170% 且融資回到崩跌前"
            break
        if S[k]["d"] - e["d"] >= R["max_days"] * 7 / 5:
            mexit, why = k, "持有滿 2 年"
            break
    if mexit is None:
        mexit, why = N - 1, "仍持有中（算到最新一天）"
    # 個股
    trades = []
    for p in picks:
        c = p["c"]
        buys = [(entry, R["first"])] + ([(second, 1 - R["first"])] if second and second < mexit else [])
        out_k, out_why = mexit, why
        for k in range(entry + 5, mexit + 1, 5):
            pe = fwd_pe(c, S[k]["d"])[0]
            med = pe_median(c, S[k]["d"])
            if pe and med and pe / med >= R["fair"]:
                out_k, out_why = k, "回到合理本益比（5 年中位數）"
                break
        sell = close_on(c, S[out_k]["d"], adj=True)
        legs = []
        for bk, w in buys:
            if bk > out_k:
                continue
            b = close_on(c, S[bk]["d"], adj=True)
            if b and sell:
                legs.append((w, (sell / b - 1) * 100 - R["cost"]))
        if not legs:
            continue
        wsum = sum(w for w, _ in legs)
        ret = sum(w * r for w, r in legs) / wsum
        trades.append(dict(p, buy=iso(S[entry]["d"]), add=iso(S[second]["d"]) if second and second <= out_k else None,
                           sell=iso(S[out_k]["d"]), why=out_why, ret=round(ret, 1), w=wsum))
    # 投組報酬：每檔 10%，沒用到的資金放現金
    port = sum(t["ret"] * t["w"] * (1 / R["picks"]) for t in trades)
    bench = (S[mexit]["ix"] / e["ix"] - 1) * 100
    episodes.append(dict(stress=iso(S[st0]["d"]), entry=iso(e["d"]), second=iso(S[second]["d"]) if second else None,
                         exit=iso(S[mexit]["d"]), why=why, low_mr=round(low_mr, 1), entry_mr=round(e["mr"], 1),
                         entry_ix=round(e["ix"]), low_ix=round(low_ix), dd=round(S[entry]["dd"], 1),
                         m10=round(S[st0]["m10"], 1), trades=trades, port=round(port, 1), bench=round(bench, 1)))
    i = mexit + 1

# 權益曲線（只在每次事件結束時結算，其餘時間現金）
eq, curve = 100.0, []
for ep in episodes:
    if ep.get("entry"):
        eq *= 1 + ep["port"] / 100
        curve.append([ep["exit"], round(eq, 1)])
ix0, ix1 = S[0]["ix"], S[-1]["ix"]
summary = dict(start=iso(S[0]["d"]), end=iso(S[-1]["d"]), n=len([e for e in episodes if e.get("entry")]),
               equity=round(eq, 1), bench=round(ix1 / ix0 * 100, 1),
               win=sum(1 for e in episodes if e.get("entry") and e["port"] > 0))
series = [[iso(s["d"]), round(s["ix"]), round(s["mr"], 1) if s["mr"] else None, round(s["money"] / 1e8) if s["money"] else None] for s in S[::3]]
os.makedirs(os.path.join(ROOT, "data", "bt"), exist_ok=True)
json.dump(dict(rules=RULES, summary=summary, episodes=episodes, curve=curve, series=series,
               now=dict(d=iso(S[-1]["d"]), ix=S[-1]["ix"], mr=S[-1]["mr"], m10=S[-1]["m10"], dd=S[-1]["dd"], m3=S[-1]["m3"])),
          open(os.path.join(ROOT, "data", "bt", "result.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
print(json.dumps(summary, ensure_ascii=False))
for e in episodes:
    print(e.get("stress"), e.get("entry"), e.get("exit"), e.get("why"), e.get("port"), e.get("bench"), len(e.get("trades") or []))
