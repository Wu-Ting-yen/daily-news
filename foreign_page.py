"""把 data/foreign/ 的資料組成 foreign.html（圖表在瀏覽器裡用 SVG 畫）。"""
import datetime as dt, json, os

ROOT = os.path.dirname(os.path.abspath(__file__))
TZ = dt.timezone(dt.timedelta(hours=8))


def fx_on(fx, d):
    """d 當天或之前最近一天的匯率"""
    best = None
    for k, v in fx.items():
        if k <= d:
            best = (k, v)
        else:
            break
    return best


def build(rows, fx, res, flow=None, vol=None):
    # 每月：取每個月最後一個交易日
    months = {}
    for r in rows:
        months[r["d"][:7]] = r
    mlist = []
    prev = None
    for m in sorted(months):
        r = months[m]
        mlist.append({
            "m": m, "d": r["d"], "total": r["total"], "tsmc": r["tsmc"], "other": r["other"],
            "tsmc_own": r["tsmc_own"], "tsmc_w": r["tsmc_w"], "fval": r["fval"], "mcap": r["mcap"],
            "chg": round(r["total"] - prev["total"], 2) if prev else None,
        })
        prev = r
    # 外匯存底＋匯率（月底、月平均）
    rlist = []
    pv = None
    for m in sorted(res):
        if m < "2025-01":
            continue
        days = [v for k, v in fx.items() if k.startswith(m)]
        end = fx_on(fx, m + "-31")
        rlist.append({
            "m": m, "res": res[m], "chg": round(res[m] - pv, 2) if pv is not None else None,
            "fx_end": end[1] if end and end[0].startswith(m) else None,
            "fx_avg": round(sum(days) / len(days), 3) if days else None,
        })
        pv = res[m]
    # 每日：附上當天匯率
    drows = []
    for r in rows:
        f = fx.get(r["d"])
        drows.append([r["d"], r["total"], r["tsmc"], r["other"], r["taiex"], f, r["tsmc_own"]])
    fxd = [[k, v] for k, v in fx.items() if k >= "2026-01-01"]
    # 每日資金流向：外資買賣超（上市、上櫃，億元）、匯率、台北外匯市場成交量（億美元）
    flow, vol = flow or {}, vol or {}
    fl = []
    days = sorted({r["d"] for r in rows} | set(flow))
    for k in days:
        f = flow.get(k) or {}
        fl.append([k, f.get("tw"), f.get("otc"), fx.get(k), vol.get(k)])
    last = rows[-1] if rows else None
    data = {"months": mlist, "daily": drows, "reserves": rlist, "fx": fxd, "flow": fl}
    now = dt.datetime.now(TZ)
    with open(os.path.join(ROOT, "foreign_template.html"), encoding="utf-8") as f:
        html = f.read()
    html = html.replace("{{UPDATED}}", now.strftime("%m/%d %H:%M"))
    html = html.replace("{{DATA}}", json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    html = html.replace("{{ASOF}}", last["d"] if last else "—")
    with open(os.path.join(ROOT, "foreign.html"), "w", encoding="utf-8") as f:
        f.write(html)
