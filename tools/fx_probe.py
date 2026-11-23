import urllib.request
UA={"User-Agent":"Mozilla/5.0"}
for y in (2026,2025):
    b=urllib.request.urlopen(urllib.request.Request(f"https://www.tpefx.com.tw/uploads/service/tw/{y}nt.csv",headers=UA),timeout=30).read()
    for enc in ("utf-8-sig","big5","cp950"):
        try: t=b.decode(enc); print("enc",enc); break
        except Exception: pass
    L=t.splitlines(); print(y,len(L)); print("\n".join(L[:12])); print("..."); print("\n".join(L[-6:]))
