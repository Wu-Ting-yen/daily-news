"""一次性資料抓取：給「買價外賣權＋Delta 避險」選股分析用（不是網頁的一部分）"""
import json, gzip, time, sys, os, urllib.request, urllib.parse, datetime as dt
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
      "Accept": "application/json, text/plain, */*", "Accept-Encoding": "gzip"}
STOCKS = ("AAPL MSFT NVDA GOOG AMZN META AVGO TSLA TSM BRK.B AMD NFLX PLTR INTC MU QCOM ARM MRVL ORCL CRM ADBE CSCO IBM "
          "UBER SHOP COIN MSTR HOOD SMCI SNOW PYPL ABNB BABA PDD JPM BAC C WFC GS V MA XOM CVX WMT COST TGT HD LOW MCD "
          "SBUX NKE KO PEP PG JNJ PFE MRK ABBV LLY UNH CVS DIS T VZ BA CAT GE DE F GM SOFI").split()
ETFS = "SPY QQQ IWM GLD TLT".split()

def get(url, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return json.loads(raw.decode("utf-8"))
        except Exception as e:
            print("fail", url[:100], e, file=sys.stderr)
            if getattr(e, "code", 0) and 400 <= e.code < 500:
                return None
            time.sleep(2 + 3 * i)
    return None

f = lambda v: float(str(v).replace(",", "").replace("$", "")) if v not in (None, "", "--", "N/A") else 0.0
out = {"fetched": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "stocks": {}}
for sym in STOCKS + ETFS:
    cls = "etf" if sym in ETFS else "stocks"
    y = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym.replace('.', '-')}?range=2y&interval=1d")
    rec = {}
    try:
        r = y["chart"]["result"][0]; q = r["indicators"]["quote"][0]
        rec["meta"] = {k: r["meta"].get(k) for k in ("regularMarketPrice", "regularMarketTime", "previousClose", "chartPreviousClose")}
        rec["daily"] = [[dt.datetime.utcfromtimestamp(t).strftime("%Y-%m-%d"), q["open"][i], q["high"][i], q["low"][i], q["close"][i], q["volume"][i]]
                        for i, t in enumerate(r["timestamp"]) if q["close"][i] is not None]
    except Exception as e:
        print("yahoo parse", sym, e, file=sys.stderr)
    j = get(f"https://api.nasdaq.com/api/quote/{urllib.parse.quote(sym)}/option-chain?assetclass={cls}&limit=10000"
            "&fromdate=all&todate=undefined&money=all&type=all")
    rows = ((((j or {}).get("data") or {}).get("table") or {}).get("rows")) or []
    spot = (rec.get("meta") or {}).get("regularMarketPrice") or 0
    chain, exp = [], None
    today = dt.date.today()
    for row in rows:
        if row.get("expirygroup"):
            try: exp = dt.datetime.strptime(row["expirygroup"], "%B %d, %Y").date()
            except ValueError: exp = None
            continue
        if not exp or not row.get("strike"): continue
        dte = (exp - today).days
        k = f(row["strike"])
        if dte < 0 or dte > 130 or (spot and not (0.6 * spot <= k <= 1.4 * spot)): continue
        for cp, pre in (("C", "c_"), ("P", "p_")):
            chain.append([exp.isoformat(), cp, k, f(row.get(pre + "Bid")), f(row.get(pre + "Ask")), f(row.get(pre + "Last")),
                          f(row.get(pre + "Openinterest")), f(row.get(pre + "Volume"))])
    rec["chain"] = chain
    rec["asof"] = (((j or {}).get("data") or {}).get("lastTrade") or "")
    e = get(f"https://api.nasdaq.com/api/analyst/{urllib.parse.quote(sym)}/earnings-date") if cls == "stocks" else None
    rec["earn"] = ((e or {}).get("data") or {}).get("announcement") if e else None
    out["stocks"][sym] = rec
    print(sym, len(rec.get("daily", [])), len(chain), rec["earn"], file=sys.stderr)
    time.sleep(0.8)
os.makedirs("probe_gamma", exist_ok=True)
tag = os.environ.get("TAG", "run")
with gzip.open(f"probe_gamma/{tag}.json.gz", "wt") as fh:
    json.dump(out, fh)
