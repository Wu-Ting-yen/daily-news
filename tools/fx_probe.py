import re, urllib.request
UA={"User-Agent":"Mozilla/5.0"}
def g(u):
    try:
        b=urllib.request.urlopen(urllib.request.Request(u,headers=UA),timeout=30).read()
        print("OK",u,len(b)); return b.decode("utf-8","replace")
    except Exception as e:
        print("ERR",u,e); return ""
def txt(h): return re.sub(r"\s+"," ",re.sub(r"<[^>]+>"," ",re.sub(r"<(script|style).*?</\1>","",h,flags=re.S)))
for u in ["https://www.tpefx.com.tw/web/service/service.jsp","https://www.tpefx.com.tw/web/download/download.jsp","https://www.tpefx.com.tw/web/index/index.jsp"]:
    h=g(u); print(sorted(set(re.findall(r'(?:src|href|action|url)\s*[:=]\s*["\']([^"\']+)["\']',h)))[:100]); print(txt(h)[:2500])
    print("AJAX", re.findall(r'\$\.(?:ajax|post|get)\([^)]{0,200}',h)[:10])
