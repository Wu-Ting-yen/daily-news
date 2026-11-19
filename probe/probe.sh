set -x
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
mkdir -p probe/out
curl -sSL -m 60 -A "$UA" "https://cdn.cboe.com/api/global/delayed_quotes/options/AAPL.json" | gzip > probe/out/cboe_aapl.json.gz; ls -la probe/out
curl -sSL -m 60 -A "$UA" "https://cdn.cboe.com/api/global/delayed_quotes/options/_SPX.json" | head -c 300; echo
curl -sSL -m 60 -A "$UA" "https://query1.finance.yahoo.com/v8/finance/chart/AAPL?range=1y&interval=1d" > probe/out/y_d.json; head -c 300 probe/out/y_d.json; echo
curl -sSL -m 60 -A "$UA" "https://query1.finance.yahoo.com/v8/finance/chart/AAPL?range=3mo&interval=1h" > probe/out/y_h.json; head -c 300 probe/out/y_h.json; echo
curl -sSL -m 60 -A "$UA" -H "Accept: application/json" "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=40&offset=0&download=true" | gzip > probe/out/nasdaq.json.gz; ls -la probe/out
curl -sSL -m 60 -A "$UA" "https://stooq.com/q/d/l/?s=aapl.us&i=d" | head -5
curl -sSL -m 60 -A "$UA" "https://cdn.cboe.com/api/global/delayed_quotes/quotes/AAPL.json" | head -c 600; echo
curl -sSL -m 60 -A "$UA" "https://cdn.cboe.com/api/global/delayed_quotes/options/GOOG.json" | gzip > probe/out/cboe_goog.json.gz
