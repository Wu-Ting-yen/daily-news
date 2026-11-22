import datetime as dt, json, os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TZ = dt.timezone(dt.timedelta(hours=8))
r = json.load(open(os.path.join(ROOT, "data", "bt", "result.json"), encoding="utf-8"))
t = open(os.path.join(ROOT, "backtest_template.html"), encoding="utf-8").read()
t = t.replace("{{UPDATED}}", dt.datetime.now(TZ).strftime("%m/%d %H:%M")).replace("{{DATA}}", json.dumps(r, ensure_ascii=False, separators=(",", ":")))
open(os.path.join(ROOT, "backtest.html"), "w", encoding="utf-8").write(t)
