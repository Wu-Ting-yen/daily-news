#!/usr/bin/env python3
"""抓新聞 → 更新網頁 → 有重大消息就推播到 Telegram。只用 Python 內建模組。"""
import html
import json
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


def fetch(sec):
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
    return re.sub(r"\W+", "", t.lower())[:40]


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
    by_sec = {s["id"]: [] for s in cfg["sections"]}
    for it in sorted(store.values(), key=lambda x: x["time"], reverse=True):
        if it["section"] in by_sec and len(by_sec[it["section"]]) < cfg["max_items_per_section"]:
            by_sec[it["section"]].append(it)
    alerts = sorted([i for i in store.values() if i.get("alert_main")], key=lambda x: x["time"], reverse=True)[:15]

    def card(it):
        t = datetime.fromisoformat(it["time"]).strftime("%m/%d %H:%M")
        return (f'<li><a href="{html.escape(it["link"])}" target="_blank" rel="noopener">'
                f'<span class="t">{html.escape(it["title"])}</span>'
                f'<span class="m">{html.escape(it["source"])} · {t} · 閱讀原文 ›</span></a></li>')

    nav = "".join(f'<a href="#{s["id"]}">{html.escape(s["name"])}</a>' for s in cfg["sections"])
    body = ""
    if alerts:
        body += '<section id="alerts" class="hot"><h2>🔔 重大快訊</h2><ul>' + "".join(card(i) for i in alerts) + "</ul></section>"
    for s in cfg["sections"]:
        lst = by_sec[s["id"]]
        body += (f'<section id="{s["id"]}"><h2>{html.escape(s["name"])}</h2><ul>'
                 + ("".join(card(i) for i in lst) or '<li class="empty">目前沒有新消息</li>') + "</ul></section>")

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
    seen_titles = {(i["section"], norm(i["title"])) for i in store.values()}
    alerted = {norm(i["title"]) for i in store.values() if i.get("alert")}
    kws = [k.lower() for k in cfg.get("alert_keywords", [])]
    new_alerts = []

    for sec in cfg["sections"]:
        try:
            items = fetch(sec)
        except Exception as e:
            print(f"[{sec['id']}] 抓取失敗: {e}", file=sys.stderr)
            continue
        for it in items:
            key = sec["id"] + "|" + it["id"]
            if datetime.fromisoformat(it["time"]) < cutoff or key in store:
                continue
            nt = norm(it["title"])
            if (sec["id"], nt) in seen_titles:  # 同一區塊內相同新聞只留一則
                continue
            seen_titles.add((sec["id"], nt))
            if any(k in it["title"].lower() for k in kws):
                it["alert"] = True
                if nt not in alerted:  # 同一則快訊只推播一次、重大快訊區只列一次
                    alerted.add(nt)
                    it["alert_main"] = True
                    if not first_run and datetime.fromisoformat(it["time"]) > now - timedelta(hours=6):
                        new_alerts.append(it)
            store[key] = it

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
