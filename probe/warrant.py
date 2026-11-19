import urllib.request, json
UA={"User-Agent":"Mozilla/5.0"}
for u in ["https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap42_O","https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap37_O"]:
    try:
        b=urllib.request.urlopen(urllib.request.Request(u,headers=UA),timeout=60).read()
        j=json.loads(b); print("==",u,len(j)); print(json.dumps(j[:3],ensure_ascii=False)[:1500])
        ks=[k for k in j[0] if "標的" in k]; print(ks)
    except Exception as e: print(u,e)
