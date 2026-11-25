/* 即時報價：在瀏覽器裡直接向鉅亨網（ws.api.cnyes.com）要報價，不經過 GitHub。
   只抓「畫面上看得到」的股票，開盤時間每 10～15 秒更新一次，收盤後只抓一次。
   每天收盤後算好的分析（地板、壓力、分數…）完全不動，這裡只多加「即時」那一行。 */
(function () {
  var bar = document.getElementById('lvbar');
  if (!bar) return;
  var KIND = bar.dataset.kind;               // tw | us
  var API = 'https://ws.api.cnyes.com/ws/api/v1/quote/quotes/';
  var EVERY = KIND === 'tw' ? 10000 : 15000;
  var cache = {}, vis = [], observed = typeof WeakSet === 'function' ? new WeakSet() : null;
  var lastOk = 0, fails = 0, timer = null, busy = false, again = null;

  /* ── 開盤時間判斷（用交易所當地時間，夏令時間自動處理）── */
  function local(tz, d) {
    var p = {};
    new Intl.DateTimeFormat('en-US', { timeZone: tz, weekday: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
      .formatToParts(d || new Date()).forEach(function (x) { p[x.type] = x.value });
    return { wd: p.weekday, m: (+p.hour % 24) * 60 + +p.minute, s: p.second, hh: ('0' + (+p.hour % 24)).slice(-2), mm: p.minute };
  }
  function inSession() {
    var t = KIND === 'tw' ? local('Asia/Taipei') : local('America/New_York');
    if (t.wd === 'Sat' || t.wd === 'Sun') return false;
    return KIND === 'tw' ? (t.m >= 9 * 60 && t.m <= 13 * 60 + 35) : (t.m >= 9 * 60 + 30 && t.m <= 16 * 60 + 5);
  }
  function hm(ts, tz, sec) { var t = local(tz, new Date(ts * 1000)); return t.hh + ':' + t.mm + (sec ? ':' + t.s : ''); }
  function stamp(ts) {
    if (!ts) return '';
    if (KIND === 'tw') return hm(ts, 'Asia/Taipei', true);
    return '美東 ' + hm(ts, 'America/New_York') + '（台北 ' + hm(ts, 'Asia/Taipei') + '）';
  }

  /* ── 數字格式 ── */
  function n(v, d) { return v == null || isNaN(v) ? '—' : Number(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }) }
  function dec(v) { return KIND === 'us' ? 2 : (v >= 100 ? (v % 1 ? 1 : 0) : 2) }
  function tone(v) { return v > 0 ? 'up' : v < 0 ? 'down' : '' }
  function arrow(v) { return v > 0 ? '▲' : v < 0 ? '▼' : '' }
  function vol(q) {
    var v = q['800001'];
    if (v == null) return '';
    if (KIND === 'tw') return '成交 ' + n(v, 0) + ' 張';
    return '成交 ' + (v >= 1e8 ? n(v / 1e8, 2) + ' 億股' : n(v / 1e4, 0) + ' 萬股');
  }
  function pct(a, b) { return (a / b - 1) * 100 }
  function sg(v, d) { return (v > 0 ? '+' : v < 0 ? '−' : '') + n(Math.abs(v), d == null ? 1 : d) }

  /* ── 抓報價 ── */
  function get(syms) {
    return fetch(API + syms.join(',') + '?column=G', { cache: 'no-store' })
      .then(function (r) { return r.json() })
      .then(function (j) {
        if (j && j.statusCode === 200 && j.data) return j.data;
        if (syms.length > 1) return Promise.all(syms.map(function (s) { return get([s]).catch(function () { return [] }) }))
          .then(function (a) { return [].concat.apply([], a) });
        return [];
      });
  }
  function wanted() {
    var s = (bar.dataset.syms || '').split(',').map(function (x) { return x.split('|')[0] }).filter(Boolean);
    vis.forEach(function (el) { var k = el.dataset.lv; if (s.indexOf(k) < 0) s.push(k) });
    return s;
  }
  function pull(onlyNew) {
    if (document.hidden) return;
    if (busy) { again = again === false ? false : onlyNew; return }
    var list = wanted().filter(function (s) { return !onlyNew || !cache[s] });
    if (!list.length) return;
    busy = true;
    var chunks = [];
    for (var i = 0; i < list.length; i += 60) chunks.push(list.slice(i, i + 60));
    Promise.all(chunks.map(get)).then(function (res) {
      res.forEach(function (arr) { arr.forEach(function (q) { cache[q['0']] = q }) });
      lastOk = Date.now(); fails = 0; paint();
    }).catch(function () { fails++; paint() }).then(function () {
      busy = false;
      if (again !== null) { var a = again; again = null; pull(a) }
    });
  }

  /* ── 畫上去 ── */
  function paintBar() {
    var open = inSession(), items = '';
    (bar.dataset.syms || '').split(',').forEach(function (x) {
      var p = x.split('|'), q = cache[p[0]];
      if (!q) return;
      items += '<span class="lvi"><em>' + p[1] + '</em><b>' + n(q['6'], 2) + '</b><i class="' + tone(q['11']) + '">' + arrow(q['11']) + n(Math.abs(q['56']), 2) + '%</i></span>';
    });
    var any = null;
    Object.keys(cache).forEach(function (k) { if (!any || cache[k]['200007'] > any) any = cache[k]['200007'] });
    var fresh = any && (Date.now() / 1000 - any) < 15 * 60;
    document.documentElement.classList.toggle('lv-open', !!(open && fresh));
    var state = fails && !lastOk ? '<span class="lvs off">即時報價暫時連不上，下面先顯示收盤後的分析</span>'
      : open && fresh ? '<span class="lvs on"><i></i>盤中・每 ' + EVERY / 1000 + ' 秒更新</span>'
      : open ? '<span class="lvs"><i></i>開盤時間但沒有新成交（可能休市）</span>'
      : '<span class="lvs"><i></i>已收盤・顯示最後成交</span>';
    bar.querySelector('.lvrow').innerHTML = state + items;
    var t = bar.querySelector('.lvt');
    if (t) t.textContent = lastOk ? '上次更新 ' + hm(lastOk / 1000, 'Asia/Taipei', true) + '（台北）' : '';
  }
  function paintCard(el) {
    var q = cache[el.dataset.lv];
    if (!q) return;
    var px = q['6'], chg = q['11'], cp = q['56'], ex = '';
    if (KIND === 'tw' && el.dataset.hi) {
      ex = '<span>距三個月高點 ' + sg(pct(px, +el.dataset.hi)) + '%</span>';
    }
    if (KIND === 'us' && el.dataset.flo) {
      var lo = +el.dataset.flo, hi = +el.dataset.fhi, st, cls, d;
      if (px < lo) { st = '跌破地板'; cls = 'bad'; d = '在地板下方 ' + n(Math.abs(pct(px, lo)), 1) + '%' }
      else if (px <= hi * 1.03) { st = '測試地板'; cls = 'mid'; d = px <= hi ? '在地板區間內' : '離地板 ' + sg(pct(px, hi)) + '%' }
      else { st = '地板上方'; cls = 'good'; d = '離地板 ' + sg(pct(px, hi)) + '%' }
      ex = '<span>盤中位置 <u class="st ' + cls + '">' + st + '</u> ' + d + '</span>';
      if (el.dataset.cw) ex += '<span>距買權牆 $' + n(+el.dataset.cw, 0) + ' ' + sg(pct(px, +el.dataset.cw)) + '%</span>';
    }
    el.innerHTML = '<div class="lvm"><span class="lvtag"><i></i>即時</span><b>' + (KIND === 'us' ? '$' : '') + n(px, dec(px)) + '</b>'
      + '<span class="' + tone(chg) + '">' + arrow(chg) + n(Math.abs(chg), dec(px)) + '　' + sg(cp, 2) + '%</span></div>'
      + '<div class="lvx">' + ex + '<span>' + vol(q) + '</span><span class="tm">' + stamp(q['200007']) + '</span></div>';
    el.classList.add('ok');
  }
  function paintGauge() {
    document.querySelectorAll('.tr[data-lv]').forEach(function (tr) {
      var q = cache[tr.dataset.lv];
      if (!q) return;
      var lo = +tr.dataset.lo, hi = +tr.dataset.hi, p = Math.max(0, Math.min(100, (q['6'] - lo) / (hi - lo) * 100));
      var m = tr.querySelector('.lvpt');
      if (!m) { m = document.createElement('i'); m.className = 'lvpt'; tr.appendChild(m) }
      m.style.left = p.toFixed(1) + '%';
    });
  }
  function paint() {
    paintBar();
    document.querySelectorAll('[data-lv]').forEach(function (el) { if (el.classList.contains('lv')) paintCard(el) });
    paintGauge();
  }

  /* ── 只抓看得到的 ── */
  var io = 'IntersectionObserver' in window ? new IntersectionObserver(function (es) {
    var add = false;
    es.forEach(function (e) {
      var i = vis.indexOf(e.target);
      if (e.isIntersecting && i < 0) { vis.push(e.target); add = true }
      else if (!e.isIntersecting && i >= 0) vis.splice(i, 1);
    });
    vis = vis.filter(function (el) { return el.isConnected });
    if (add) { clearTimeout(io._t); io._t = setTimeout(function () { paint(); pull(!inSession()) }, 300) }
  }, { rootMargin: '300px 0px' }) : null;
  function scan() {
    document.querySelectorAll('[data-lv]').forEach(function (el) {
      if (observed ? observed.has(el) : el._lvo) return;
      observed ? observed.add(el) : (el._lvo = 1);
      if (io) io.observe(el); else vis.push(el);
      if (cache[el.dataset.lv] && el.classList.contains('lv')) paintCard(el);
    });
  }

  function loop() {
    clearTimeout(timer);
    scan();
    if (inSession()) pull(false);
    timer = setTimeout(loop, fails ? Math.min(60000, EVERY * (1 + fails)) : EVERY);
  }
  var q = document.getElementById('q');
  if (q) q.addEventListener('input', function () { setTimeout(scan, 60) });
  document.addEventListener('click', function () { setTimeout(scan, 80) });
  document.addEventListener('toggle', function () { setTimeout(scan, 60) }, true);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) { pull(false) } });
  scan();
  setTimeout(function () { pull(false) }, 50);
  loop();
})();
