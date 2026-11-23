import re, urllib.request
UA={"User-Agent":"Mozilla/5.0"}
def g(u):
    try:
        b=urllib.request.urlopen(urllib.request.Request(u,headers=UA),timeout=30).read()
        print("OK",u,len(b)); return b
    except Exception as e:
        print("ERR",u,e); return b""
b=g("https://www.cbc.gov.tw/public/data/bkrldc.txt")
for enc in ("utf-8","big5"):
    try: t=b.decode(enc); break
    except Exception: pass
print(t[:800]); print("...", t[-600:])
h=g("https://www.tpefx.com.tw/web/index/index.jsp").decode("utf-8","replace")
L=sorted(set(re.findall(r'(?:src|href|action)="([^"]+)"',h))); print(L[:120])
print(re.sub(r"\s+"," ",re.sub(r"<[^>]+>"," ",re.sub(r"<script.*?</script>","",h,flags=re.S)))[:3000])
