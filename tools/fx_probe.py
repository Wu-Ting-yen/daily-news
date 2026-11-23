import re, urllib.request, json
UA={"User-Agent":"Mozilla/5.0"}
def g(u,n=600):
    try:
        b=urllib.request.urlopen(urllib.request.Request(u,headers=UA),timeout=30).read()
        print("OK",u,len(b)); return b
    except Exception as e:
        print("ERR",u,e); return b""
b=g("https://www.twse.com.tw/rwd/zh/fund/BFI82U?type=day&dayDate=20261120&response=json"); print(b[:700].decode("utf-8","replace"))
b=g("https://www.tpex.org.tw/web/stock/3insti/3insti_summary/3itrdsum_result.php?l=zh-tw&o=json&d=115/11/20&t=D"); print(b[:900].decode("utf-8","replace"))
for u in ["https://www.tpefx.com.tw/","https://www.tpefx.com.tw/uploads/service/tw/","https://www.tpefx.com.tw/service/","https://www.tpefx.com.tw/tw/"]:
    b=g(u).decode("utf-8","replace")
    links=set(re.findall(r'href="([^"]+)"',b))
    print([l for l in links if any(k in l.lower() for k in ("service","trade","data","rate","usd","csv","xls","pdf","stat"))][:60])
    print(re.sub(r"\s+"," ",re.sub(r"<[^>]+>"," ",b))[:800])
