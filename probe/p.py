import urllib.request, json
UA = {"User-Agent": "Mozilla/5.0 (daily-news options)", "Accept": "application/json"}
def g(url):
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30); return r.status, r.read()
    except Exception as e:
        return str(e), b""
s, b = g("https://api.nasdaq.com/api/quote/AAPL/option-chain?assetclass=stocks&limit=5000&fromdate=all&todate=undefined&money=all&type=all")
print("nasdaq", s, len(b)); print(b[:1500])
s, b = g("https://query2.finance.yahoo.com/v7/finance/options/AAPL")
print("yahoo", s, len(b)); print(b[:300])
