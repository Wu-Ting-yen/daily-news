import re, urllib.request
UA={"User-Agent":"Mozilla/5.0"}
def g(u):
    try:
        b=urllib.request.urlopen(urllib.request.Request(u,headers=UA),timeout=30).read()
        print("OK",u,len(b)); return b.decode("utf-8","replace")
    except Exception as e:
        print("ERR",u,e); return ""
h=g("https://www.tpefx.com.tw/"); print(h)
for src in re.findall(r'(?:src|href)="([^"]+)"',h):
    if src.startswith("http") and "tpefx" not in src: continue
    u=src if src.startswith("http") else "https://www.tpefx.com.tw/"+src.lstrip("/")
    t=g(u); print(sorted(set(re.findall(r'(?:src|href)="([^"]+)"',t)))[:80]); print(re.sub(r"\s+"," ",re.sub(r"<[^>]+>"," ",t))[:1500])
for u in ["https://www.cbc.gov.tw/tw/lp-2151-1.html","https://www.cbc.gov.tw/tw/cp-2151-188471-bc29b-1.html"]:
    t=g(u); print(re.sub(r"\s+"," ",re.sub(r"<[^>]+>"," ",t))[:2500])
    print([l for l in set(re.findall(r'href="([^"]+)"',t)) if any(k in l for k in ("csv","xls","dl-","cp-2151","public"))][:40])
