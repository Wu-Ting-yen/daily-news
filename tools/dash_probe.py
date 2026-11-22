import urllib.request, re, json
UA={"User-Agent":"Mozilla/5.0"}
def get(u):
    try: return urllib.request.urlopen(urllib.request.Request(u,headers=UA),timeout=40).read().decode("utf-8","replace")
    except Exception as e: return "ERR "+str(e)
base="https://www.twse.com.tw"
h=get(base+"/dashboard/zh/credit/margin.html"); print(len(h)); print(h[:2500])
for s in re.findall(r'src="([^"]+\.js[^"]*)"',h):
    u=s if s.startswith("http") else base+(s if s.startswith("/") else "/dashboard/zh/credit/"+s)
    t=get(u); print("=== JS",u,len(t))
    for m in set(re.findall(r'["\'`](/[^"\'`]*(?:api|rwd|json|dashboard|credit|margin)[^"\'`]*)["\'`]',t)): print("  ",m)
    for m in list(set(re.findall(r'.{0,80}(?:維持率|maint|Maint).{0,80}',t)))[:30]: print("  >",m)
