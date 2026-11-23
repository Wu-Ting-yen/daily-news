#!/usr/bin/env python3
"""抓新聞 → 更新網頁 → 有重大消息就推播到 Telegram。只用 Python 內建模組。"""
import html
import json
import math
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
TPE = timezone(timedelta(hours=8))
UA = "Mozilla/5.0 (daily-news-bot)"


def load(path, default):
    try:
        with open(os.path.join(ROOT, path), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save(path, obj):
    with open(os.path.join(ROOT, path), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def feed_url(sec):
    if "url" in sec:
        return sec["url"]
    q = urllib.parse.quote(sec["query"])
    return f"https://news.google.com/rss/search?q={q}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"


def strip_tags(s):
    return html.unescape(re.sub(r"<[^>]+>", " ", s or "")).strip()


def fetch_ptt(sec):
    """PTT 網頁版看板列表（例如股板）：抓最新幾頁的標題、推文數、發文時間"""
    board = sec["ptt"]
    url = f"https://www.ptt.cc/bbs/{board}/index.html"
    items = []
    for _ in range(sec.get("pages", 3)):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Cookie": "over18=1"})
        with urllib.request.urlopen(req, timeout=30) as r:
            page = r.read().decode("utf-8", "replace")
        page_main = page.split('r-list-sep')[0]  # 置底文章不要
        for ent in re.findall(r'<div class="r-ent">(.*?)<div class="mark">', page_main, re.S):
            m = re.search(r'<a href="(/bbs/[^"]+/M\.(\d+)\.A\.[^"]+\.html)">(.*?)</a>', ent, re.S)
            if not m:
                continue  # 已刪除的文章
            href, ts, title = m.group(1), int(m.group(2)), html.unescape(m.group(3)).strip()
            nrec = strip_tags((re.search(r'<div class="nrec">(.*?)</div>', ent, re.S) or [None, ""])[1])
            push = 100 if nrec == "爆" else (-10 if nrec.startswith("X") else int(nrec) if nrec.isdigit() else 0)
            items.append({
                "id": "ptt:" + href,
                "title": title,
                "source": f"PTT {sec.get('board_name', board)}",
                "link": "https://www.ptt.cc" + href,
                "time": datetime.fromtimestamp(ts, TPE).isoformat(),
                "section": sec["id"],
                "forum": True,
                "push": push,
            })
        prev = re.search(r'<a class="btn wide" href="([^"]+)">&lsaquo; 上頁</a>', page)
        if not prev:
            break
        url = "https://www.ptt.cc" + prev.group(1)
    return items


def ptt_keep(sec, it):
    """要顯示的 PTT 文章：新聞類一律顯示，其他要夠多人推；公告、閒聊串不要"""
    t = it["title"]
    if re.match(r"^(Re: |Fw: )?\[(公告|閒聊)\]", t) or "盤中閒聊" in t or "盤後閒聊" in t:
        return False
    if re.match(r"^\[(新聞|情報)\]", t):
        return True
    return it.get("push", 0) >= sec.get("min_push", 20)


def fetch(sec):
    if "ptt" in sec:
        return fetch_ptt(sec)
    req = urllib.request.Request(feed_url(sec), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        root = ET.fromstring(r.read())
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        source = (it.findtext("source") or "").strip()
        # Google News 標題結尾是「 - 媒體名稱」，拿掉
        if source and title.endswith(" - " + source):
            title = title[: -len(" - " + source)]
        try:
            ts = parsedate_to_datetime(it.findtext("pubDate")).astimezone(TPE)
        except Exception:
            ts = datetime.now(TPE)
        link = (it.findtext("link") or "").strip()
        if not title or not link:
            continue
        items.append({
            "id": (it.findtext("guid") or link).strip(),
            "title": title,
            "source": source,
            "link": link,
            "time": ts.isoformat(),
            "section": sec["id"],
        })
    return items


def norm(t):
    return re.sub(r"\W+", "", t.lower())


STOP = set("即時 新聞 美股 台股 快訊 最新 一次 今日 今天 明天 美元 億元 市場 投資 股價 公司 表示 宣布 財經 全球 國際 日報".split())


def tokens(t):
    t = re.sub(r"【[^】]*】", "", t)
    t = re.sub(r"\s*-\s*股市爆料同學會\s*$", "", t)
    t = re.sub(r"^\w{2,6}\s+\S+\s*-\s*", "", t) if re.match(r"^[0-9A-Z]{2,6}\s", t) else t  # 論壇「2330 台積電 - 」前綴
    num = {x.replace("％", "%") for x in re.findall(r"\d{2,}(?:%|％)", t)}
    en = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9]+", t)}
    zh = set()
    for seg in re.findall(r"[\u4e00-\u9fff]+", t):
        zh |= {seg[i:i + 2] for i in range(len(seg) - 1)}
    return (en | zh | num) - STOP


def cluster(items, threshold=0.18):
    """把講同一件事的新聞歸成一組。回傳 [[最早那則, 後續報導...], ...]"""
    items = sorted(items, key=lambda x: x["time"])
    toks = [tokens(i["title"]) for i in items]
    n = max(1, len(items))
    df = {}
    for t in toks:
        for x in t:
            df[x] = df.get(x, 0) + 1

    def w(x):  # 越少見的詞越重要；英文名稱（公司、代號）加重
        return math.log(n / df[x]) * (1.5 if x.isascii() else 1)

    def weight(t):
        return sum(w(x) for x in t)

    groups = []  # {"m": members, "t": [(tokens, weight)]}
    for it, tk in zip(items, toks):
        wt = weight(tk)
        best, best_sc = None, 0
        for g in groups:
            for mt, mw in g["t"][:3]:
                sc = weight(tk & mt) / max(1e-9, (wt * mw) ** 0.5)
                if sc > best_sc:
                    best, best_sc = g, sc
        if best and best_sc >= threshold:
            best["m"].append(it)
            best["t"].append((tk, wt))
        else:
            groups.append({"m": [it], "t": [(tk, wt)]})
    # 第二輪：標題提到同一個少見的英文名稱（例如 SpaceX），而且中文內容也有點像，才併成同一則
    # （只看名稱會把「輝達」「OpenAI」整週的新聞全部吞成一大包，重要新聞反而被藏起來）
    def sim(a, b):
        (ta, wa), (tb, wb) = a, b
        return weight(ta & tb) / max(1e-9, (wa * wb) ** 0.5)

    merged, by_name = [], {}
    for g in groups:
        names = {x for x in g["t"][0][0] if x.isascii() and len(x) >= 4 and 3 <= df[x] <= n * 0.12}
        target = None
        for x in sorted(names):
            cand = by_name.get(x)
            if cand and len(cand["m"]) < 25 and sim(g["t"][0], cand["lead"]) >= threshold * 0.6:
                target = cand
                break
        if target is None:
            target = {"m": [], "lead": g["t"][0]}
            merged.append(target)
            for x in names:
                by_name.setdefault(x, target)
        target["m"].extend(g["m"])
    merged = [t["m"] for t in merged]
    return [sorted(m, key=lambda x: x["time"]) for m in merged]


def send_telegram(text):
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat:
        return False
    data = urllib.parse.urlencode({
        "chat_id": chat, "text": text, "parse_mode": "HTML",
        "disable_web_page_preview": "false",
    }).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=20)
        return True
    except Exception as e:
        print("telegram error:", e, file=sys.stderr)
        return False


def find_chat_id():
    """沒設 TELEGRAM_CHAT_ID 時，從最近傳給機器人的訊息找出你的 chat id 並印出來。"""
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token or os.environ.get("TELEGRAM_CHAT_ID", "").strip():
        return
    saved = os.path.join(ROOT, "data", "chat_id")
    if os.path.exists(saved):
        os.environ["TELEGRAM_CHAT_ID"] = open(saved).read().strip()
        return
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getUpdates", timeout=20) as r:
            for u in json.load(r).get("result", []):
                msg = u.get("message") or {}
                if msg.get("chat"):
                    os.environ["TELEGRAM_CHAT_ID"] = str(msg["chat"]["id"])
                    with open(saved, "w") as f:
                        f.write(str(msg["chat"]["id"]))
                    print("找到 chat id")
    except Exception as e:
        print("getUpdates error:", e, file=sys.stderr)


def render(cfg, store, updated):
    groups = cluster([v for v in store.values() if not v.get("low") and not v.get("forum")])
    secs = {s["id"]: s for s in cfg["sections"]}
    forum = {}
    for v in sorted(store.values(), key=lambda x: x["time"], reverse=True):
        if v.get("forum") and v["section"] in secs and ptt_keep(secs[v["section"]], v):
            forum.setdefault(v["section"], []).append([v])
    low = {}
    for v in sorted(store.values(), key=lambda x: x["time"], reverse=True):
        if v.get("low"):
            low.setdefault(v["section"], []).append(v)
    newest = lambda g: max(x["time"] for x in g)
    groups.sort(key=newest, reverse=True)
    alerts = [g for g in groups if any(x.get("alert") for x in g)][:15]
    shown = {id(g) for g in alerts}
    by_sec = {s["id"]: [] for s in cfg["sections"]}
    for g in groups:
        sec = g[0]["section"]
        if id(g) not in shown and sec in by_sec and len(by_sec[sec]) < cfg["max_items_per_section"]:
            by_sec[sec].append(g)
    for sid, gl in forum.items():
        by_sec[sid] = gl[: secs[sid].get("max_items", 40)]

    def ago(iso):
        return f'<time datetime="{iso}">{datetime.fromisoformat(iso).strftime("%m/%d %H:%M")}</time>'

    def card(g):
        # 標題：跟這個話題最貼近的「最新」一則；其餘依時間新到舊
        lt = tokens(g[0]["title"])
        it = g[0]
        for x in sorted(g[1:], key=lambda x: x["time"], reverse=True):
            xt = tokens(x["title"])
            if len(lt & xt) / max(1, len(lt | xt)) >= 0.15:
                it = x
                break
        g = [it] + sorted([x for x in g if x is not it], key=lambda x: x["time"], reverse=True)
        hot = " hot" if any(x.get("alert") for x in g) else ""
        out = (f'<article class="card{hot}"><a class="main" href="{html.escape(it["link"])}" target="_blank" rel="noopener">'
               f'<h3>{html.escape(it["title"])}</h3>'
               f'<div class="meta"><span class="src">{html.escape(it["source"])}</span>{ago(it["time"])}'
               + (f'<span class="push{" boom" if it["push"] >= 100 else ""}">{"爆" if it["push"] >= 100 else "推 " + str(it["push"])}</span>' if it.get("forum") else "")
               + '</div></a>')
        if len(g) > 1:
            out += f'<details><summary>另有 {len(g) - 1} 則相關報導</summary><div class="rels">'
            for x in g[1:]:
                out += (f'<a class="rel" href="{html.escape(x["link"])}" target="_blank" rel="noopener">'
                        f'<span>{html.escape(x["title"])}</span>'
                        f'<div class="meta"><span class="src">{html.escape(x["source"])}</span>{ago(x["time"])}</div></a>')
            out += "</div></details>"
        return out + "</article>"

    tabs = [("alerts", "重大快訊", alerts)] + [(s["id"], s["name"], by_sec[s["id"]]) for s in cfg["sections"]]
    nav = "".join(f'<button data-tab="{i}">{html.escape(n)}<b>{len(l)}</b></button>' for i, n, l in tabs)
    body = ""
    for i, n, l in tabs:
        extra = ""
        if low.get(i):
            extra = (f'<details class="others"><summary>其他來源・論壇與娛樂 {len(low[i])} 則</summary><div class="rels">'
                     + "".join(f'<a class="rel" href="{html.escape(x["link"])}" target="_blank" rel="noopener">'
                               f'<span>{html.escape(x["title"])}</span><div class="meta"><span class="src">'
                               f'{html.escape(x["source"])}</span>{ago(x["time"])}</div></a>' for x in low[i][:30])
                     + "</div></details>")
        body += (f'<section id="{i}" data-name="{html.escape(n)}">'
                 + ("".join(card(g) for g in l) or '<p class="empty">目前沒有新消息</p>') + extra + "</section>")

    tpl = open(os.path.join(ROOT, "template.html"), encoding="utf-8").read()
    out = tpl.replace("{{UPDATED}}", updated).replace("{{NAV}}", nav).replace("{{BODY}}", body)
    with open(os.path.join(ROOT, "index.html"), "w", encoding="utf-8") as f:
        f.write(out)


def main():
    cfg = load("config.json", {})
    store = load("data/news.json", {})
    first_run = not store
    now = datetime.now(TPE)
    cutoff = now - timedelta(days=cfg.get("keep_days", 3))
    kws = [k.lower() for k in cfg.get("alert_keywords", [])]
    block = cfg.get("other_sources", [])
    titles = {norm(v["title"]) for v in store.values()}
    old_ids = set(store)

    # 先處理特定主題（IPO、太空…），再處理一般分類；完全相同的標題只留一則
    ordered = [s for s in cfg["sections"] if "query" in s] + [s for s in cfg["sections"] if "query" not in s]
    for sec in ordered:
        try:
            items = fetch(sec)
        except Exception as e:
            print(f"[{sec['id']}] 抓取失敗: {e}", file=sys.stderr)
            continue
        for it in items:
            if it.get("forum"):
                if it["id"] in store:
                    store[it["id"]]["push"] = it["push"]  # 推文數會一直變，更新
                elif datetime.fromisoformat(it["time"]) >= cutoff:
                    store[it["id"]] = it
                continue
            nt = norm(it["title"])
            if datetime.fromisoformat(it["time"]) < cutoff or it["id"] in store or nt in titles:
                continue
            titles.add(nt)
            if any(b.lower() in it["source"].lower() for b in block):
                it["low"] = True  # 論壇、娛樂等：不刪，放到每個分頁最下面收合
            elif any(k in it["title"].lower() for k in kws):
                it["alert"] = True
            store[it["id"]] = it

    # 只有「全新的事件」才推播；同一件事後續的報導不再重複通知
    new_alerts = []
    if not first_run:
        for g in cluster([v for v in store.values() if datetime.fromisoformat(v["time"]) >= cutoff and not v.get("low") and not v.get("forum")]):
            lead = g[0]
            if (lead["id"] not in old_ids and lead.get("alert")
                    and datetime.fromisoformat(lead["time"]) > now - timedelta(hours=6)):
                new_alerts.append(lead)

    for v in store.values():
        if not v.get("forum") and any(b.lower() in v["source"].lower() for b in block):
            v["low"] = True
            v.pop("alert", None)
    store = {k: v for k, v in store.items() if datetime.fromisoformat(v["time"]) >= cutoff}
    save("data/news.json", store)
    render(cfg, store, now.strftime("%Y/%m/%d %H:%M"))

    find_chat_id()
    page = os.environ.get("PAGE_URL", "")
    marker = os.path.join(ROOT, "data", "telegram_started")
    if os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() and os.environ.get("TELEGRAM_CHAT_ID", "").strip() and not os.path.exists(marker):
        open(marker, "w").close()
        send_telegram("✅ 財經新聞小幫手已啟動！之後有重大消息會第一時間通知你。" + (f"\n每日整理：{page}" if page else ""))
    for it in sorted(new_alerts, key=lambda x: x["time"])[-cfg.get("max_alerts_per_run", 8):]:
        t = datetime.fromisoformat(it["time"]).strftime("%H:%M")
        send_telegram(f'🔔 <b>{html.escape(it["title"])}</b>\n{html.escape(it["source"])} · {t}\n<a href="{html.escape(it["link"])}">閱讀原文</a>')
    print(f"共 {len(store)} 則，新快訊 {len(new_alerts)} 則")


if __name__ == "__main__":
    main()
