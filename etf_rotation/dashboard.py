"""
Builds a single self-contained docs/index.html dashboard (for GitHub Pages)
from a completed backtest result. Data is baked into the page at build time
as JSON; charts render client-side with Chart.js (CDN).
"""
import json
import os
import datetime as dt

import pandas as pd

from . import config
from .backtest import compute_metrics, drawdown_series


def _series_for_chart(equity_curve: pd.DataFrame, start=None, end=None, rebase=True):
    ec = equity_curve.copy()
    if start:
        ec = ec[ec.index >= pd.Timestamp(start)]
    if end:
        ec = ec[ec.index <= pd.Timestamp(end)]
    if len(ec) == 0:
        return [], []
    eq = ec["equity"]
    if rebase:
        eq = eq / eq.iloc[0] * 100
    labels = [d.strftime("%Y-%m-%d") for d in ec.index]
    return labels, [round(v, 3) for v in eq.tolist()]


def _drawdown_for_chart(equity_curve: pd.DataFrame, start=None, end=None):
    dd = drawdown_series(equity_curve) * 100
    if start:
        dd = dd[dd.index >= pd.Timestamp(start)]
    if end:
        dd = dd[dd.index <= pd.Timestamp(end)]
    labels = [d.strftime("%Y-%m-%d") for d in dd.index]
    return labels, [round(v, 3) for v in dd.tolist()]


def build_segment_payload(equity_curve, trade_log, seg_start, seg_end, label):
    metrics = compute_metrics(equity_curve, trade_log, start=seg_start, end=seg_end)
    eq_labels, eq_vals = _series_for_chart(equity_curve, seg_start, seg_end)
    dd_labels, dd_vals = _drawdown_for_chart(equity_curve, seg_start, seg_end)
    return {
        "label": label,
        "start": seg_start,
        "end": seg_end,
        "metrics": metrics,
        "equity": {"labels": eq_labels, "values": eq_vals},
        "drawdown": {"labels": dd_labels, "values": dd_vals},
    }


def build_current_ranking(rs: pd.DataFrame, prices: pd.DataFrame, top_n=config.TOP_N, as_of=None):
    from .rs import rank_on_date
    as_of = as_of or prices.index.max()
    ranked = rank_on_date(rs, prices, as_of, top_n=max(top_n, 10))
    rows = []
    for i, (t, val) in enumerate(ranked):
        rows.append({
            "rank": i + 1,
            "ticker": t,
            "name": config.NAME_MAP.get(t, t),
            "code": config.CODE_MAP.get(t, t),
            "rs": round(val, 3),
            "in_portfolio": i < top_n,
        })
    return {"as_of": str(pd.Timestamp(as_of).date()), "rows": rows}


def build_holdings_table(portfolio, prices, as_of=None):
    as_of = as_of or prices.index.max()
    row = prices.loc[as_of] if as_of in prices.index else prices.iloc[-1]
    rows = []
    total_val = portfolio.cash
    for t, units in portfolio.holdings.items():
        px = row.get(t, float("nan"))
        val = units * px if px == px else 0
        total_val += val
        rows.append({"ticker": t, "name": config.NAME_MAP.get(t, t), "units": units,
                      "price": round(px, 2) if px == px else None, "value": round(val, 2)})
    rows.sort(key=lambda r: -r["value"])
    return {"as_of": str(pd.Timestamp(as_of).date()), "cash": round(portfolio.cash, 2),
            "total_value": round(total_val, 2), "rows": rows}


def build_trade_log_table(trade_log: pd.DataFrame, n=40):
    if trade_log is None or len(trade_log) == 0:
        return []
    tl = trade_log.copy().sort_values("date", ascending=False).head(n)
    tl["date"] = pd.to_datetime(tl["date"]).dt.strftime("%Y-%m-%d")
    tl["name"] = tl["ticker"].map(lambda t: config.NAME_MAP.get(t, t))
    cols = ["date", "action", "ticker", "name", "units", "price", "gross", "cost"]
    return tl[cols].round(2).to_dict(orient="records")


def build_signal_log_table(signal_log: pd.DataFrame, n=26):
    if signal_log is None or len(signal_log) == 0:
        return []
    sl = signal_log.copy().sort_values("execute_date", ascending=False).head(n)
    sl["scan_date"] = pd.to_datetime(sl["scan_date"]).dt.strftime("%Y-%m-%d")
    sl["execute_date"] = pd.to_datetime(sl["execute_date"]).dt.strftime("%Y-%m-%d")
    rank_cols = [c for c in sl.columns if c.startswith("rank_")]
    sl["top_n"] = sl[rank_cols].apply(
        lambda r: ", ".join(config.CODE_MAP.get(t, t) for t in r if isinstance(t, str)), axis=1
    )
    return sl[["scan_date", "execute_date", "top_n"]].to_dict(orient="records")


def render_dashboard(result, prices, top_n=config.TOP_N, out_path=config.DASHBOARD_HTML):
    equity_curve = result["equity_curve"]
    trade_log = result["trade_log"]
    signal_log = result["signal_log"]
    rs = result["rs"]
    pf = result["final_portfolio"]

    s1, e1 = config.SEGMENT_1
    s2, e2 = config.SEGMENT_2
    s3, e3 = config.SEGMENT_3
    e3 = e3 or str(prices.index.max().date())

    segments = [
        build_segment_payload(equity_curve, trade_log, s1, e1, "Backtest 2018\u20132024"),
        build_segment_payload(equity_curve, trade_log, s2, e2, "Forward Test 2025"),
        build_segment_payload(equity_curve, trade_log, s3, e3, "Forward Test 2026 (YTD)"),
    ]
    combined = build_segment_payload(equity_curve, trade_log, config.BACKTEST_START, e3, "Full Period")

    ranking = build_current_ranking(rs, prices, top_n=top_n)
    holdings = build_holdings_table(pf, prices)
    trades = build_trade_log_table(trade_log)
    signals = build_signal_log_table(signal_log)

    payload = {
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M IST"),
        "top_n": top_n,
        "lookback": config.LOOKBACK_DAYS,
        "initial_capital": config.INITIAL_CAPITAL,
        "txn_cost_bps": config.TXN_COST_BPS,
        "segments": segments,
        "combined": combined,
        "ranking": ranking,
        "holdings": holdings,
        "trades": trades,
        "signals": signals,
    }

    html = _HTML_TEMPLATE.replace("__PAYLOAD_JSON__", json.dumps(payload))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        f.write(html)
    return out_path


_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ETF Relative-Strength Rotation \u2014 Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600;700&family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<style>
  :root{
    --bg:#0B0F14; --panel:#121821; --panel2:#171F2A; --border:#26313F;
    --text:#E9EEF3; --muted:#8A97A6; --teal:#4FD8C0; --coral:#FF7B72; --amber:#E3B341;
    --mono:'IBM Plex Mono', ui-monospace, monospace; --sans:'Inter', system-ui, sans-serif;
  }
  *{box-sizing:border-box;}
  body{margin:0; background:var(--bg); color:var(--text); font-family:var(--sans); -webkit-font-smoothing:antialiased;}
  header{padding:28px 32px 20px; border-bottom:1px solid var(--border); display:flex; justify-content:space-between; align-items:flex-end; flex-wrap:wrap; gap:16px;}
  header h1{margin:0; font-size:22px; font-weight:800; letter-spacing:-0.01em;}
  header .sub{color:var(--muted); font-family:var(--mono); font-size:12px; margin-top:6px;}
  .meta{font-family:var(--mono); font-size:12px; color:var(--muted); text-align:right;}
  .meta b{color:var(--amber);}
  main{padding:24px 32px 60px; max-width:1280px; margin:0 auto;}

  .tabs{display:flex; gap:4px; margin-bottom:20px; border-bottom:1px solid var(--border); flex-wrap:wrap;}
  .tab{padding:10px 16px; font-family:var(--mono); font-size:13px; color:var(--muted); cursor:pointer; border-bottom:2px solid transparent; user-select:none;}
  .tab.active{color:var(--teal); border-bottom-color:var(--teal);}

  .grid{display:grid; grid-template-columns:repeat(auto-fit, minmax(150px,1fr)); gap:12px; margin-bottom:20px;}
  .card{background:var(--panel); border:1px solid var(--border); border-radius:10px; padding:14px 16px;}
  .card .label{font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:.05em; font-family:var(--mono);}
  .card .value{font-family:var(--mono); font-size:22px; font-weight:600; margin-top:6px;}
  .pos{color:var(--teal);} .neg{color:var(--coral);}

  .panel{background:var(--panel); border:1px solid var(--border); border-radius:10px; padding:18px 20px; margin-bottom:20px;}
  .panel h3{margin:0 0 14px; font-size:14px; color:var(--muted); font-weight:600; text-transform:uppercase; letter-spacing:.04em; font-family:var(--mono);}
  .chart-wrap{position:relative; height:280px;}

  table{width:100%; border-collapse:collapse; font-family:var(--mono); font-size:12.5px;}
  th{text-align:left; color:var(--muted); font-weight:500; padding:8px 10px; border-bottom:1px solid var(--border); font-size:11px; text-transform:uppercase; letter-spacing:.04em;}
  td{padding:7px 10px; border-bottom:1px solid #1B222C;}
  tr:hover td{background:var(--panel2);}
  .buy{color:var(--teal);} .sell{color:var(--coral);}
  .chip{display:inline-block; padding:2px 8px; border-radius:20px; background:var(--panel2); border:1px solid var(--border); font-size:11px; margin:2px;}
  .chip.in{border-color:var(--teal); color:var(--teal);}

  .rs-bar-row{display:flex; align-items:center; gap:10px; margin-bottom:6px;}
  .rs-bar-row .rk{width:22px; color:var(--muted); font-family:var(--mono); font-size:12px;}
  .rs-bar-row .tk{width:130px; font-family:var(--mono); font-size:12.5px; flex-shrink:0;}
  .rs-bar-track{flex:1; background:var(--panel2); border-radius:4px; height:16px; overflow:hidden; position:relative;}
  .rs-bar-fill{height:100%; border-radius:4px;}
  .rs-bar-val{width:60px; text-align:right; font-family:var(--mono); font-size:12px; color:var(--muted);}

  footer{text-align:center; color:var(--muted); font-family:var(--mono); font-size:11px; padding:30px; border-top:1px solid var(--border);}
  @media(max-width:700px){ main{padding:16px;} header{padding:20px 16px;} }
</style>
</head>
<body>
<header>
  <div>
    <h1>ETF Relative-Strength Rotation</h1>
    <div class="sub">Mansfield RS vs. peer basket &middot; 45d lookback &middot; weekly Saturday scan / Monday execution</div>
  </div>
  <div class="meta">Last updated <b id="genAt"></b><br>Capital \u20b9<span id="capital"></span> &middot; Top-<span id="topn"></span> &middot; cost <span id="cost"></span> bps/trade</div>
</header>
<main>

  <div class="panel">
    <h3>Current Relative-Strength Ranking <span id="rankAsOf" style="color:var(--muted); text-transform:none; font-weight:400;"></span></h3>
    <div id="rsLeaderboard"></div>
  </div>

  <div class="tabs" id="segTabs"></div>
  <div id="segBody"></div>

  <div class="panel">
    <h3>Current Holdings <span id="holdAsOf" style="color:var(--muted); text-transform:none; font-weight:400;"></span></h3>
    <table id="holdingsTable"></table>
  </div>

  <div class="panel">
    <h3>Weekly Signal Log (most recent)</h3>
    <table id="signalTable"></table>
  </div>

  <div class="panel">
    <h3>Trade Log (most recent)</h3>
    <table id="tradeTable"></table>
  </div>

</main>
<footer>Systematic simulation for research/educational purposes &middot; not investment advice &middot; generated automatically via GitHub Actions</footer>

<script>
const DATA = __PAYLOAD_JSON__;
document.getElementById('genAt').textContent = DATA.generated_at;
document.getElementById('capital').textContent = DATA.initial_capital.toLocaleString('en-IN');
document.getElementById('topn').textContent = DATA.top_n;
document.getElementById('cost').textContent = (DATA.txn_cost_bps*10000).toFixed(0);

function fmtPct(v){ if(v===null||v===undefined) return '\u2014'; const cls = v>=0?'pos':'neg'; return '<span class="'+cls+'">'+(v>=0?'+':'')+v.toFixed(2)+'%</span>'; }
function fmtNum(v){ return v===null||v===undefined ? '\u2014' : Number(v).toLocaleString('en-IN'); }

// --- RS leaderboard ---
const lb = document.getElementById('rsLeaderboard');
document.getElementById('rankAsOf').textContent = '\u2014 as of ' + DATA.ranking.as_of;
const maxAbsRS = Math.max(...DATA.ranking.rows.map(r=>Math.abs(r.rs)), 1);
DATA.ranking.rows.forEach(r=>{
  const pct = Math.min(Math.abs(r.rs)/maxAbsRS*100, 100);
  const color = r.in_portfolio ? 'var(--teal)' : (r.rs>=0 ? '#3A5F58' : 'var(--coral)');
  const row = document.createElement('div');
  row.className = 'rs-bar-row';
  row.innerHTML = `<div class="rk">${r.rank}</div><div class="tk">${r.code} <span style="color:var(--muted)">${r.in_portfolio?'\u25CF':''}</span></div>
    <div class="rs-bar-track"><div class="rs-bar-fill" style="width:${pct}%; background:${color};"></div></div>
    <div class="rs-bar-val">${r.rs.toFixed(2)}</div>`;
  lb.appendChild(row);
});

// --- Segment tabs ---
const allSegs = [DATA.combined, ...DATA.segments];
const tabsEl = document.getElementById('segTabs');
const bodyEl = document.getElementById('segBody');
let charts = [];

function renderSeg(idx){
  charts.forEach(c=>c.destroy()); charts=[];
  const seg = allSegs[idx];
  const m = seg.metrics;
  bodyEl.innerHTML = `
    <div class="grid">
      <div class="card"><div class="label">Total Return</div><div class="value">${fmtPct(m.total_return_pct)}</div></div>
      <div class="card"><div class="label">CAGR</div><div class="value">${fmtPct(m.cagr_pct)}</div></div>
      <div class="card"><div class="label">Max Drawdown</div><div class="value">${fmtPct(m.max_drawdown_pct)}</div></div>
      <div class="card"><div class="label">Sharpe</div><div class="value">${m.sharpe ?? '\u2014'}</div></div>
      <div class="card"><div class="label">Calmar</div><div class="value">${m.calmar ?? '\u2014'}</div></div>
      <div class="card"><div class="label">Trades</div><div class="value">${fmtNum(m.num_trades)}</div></div>
    </div>
    <div class="panel"><h3>Equity Curve (rebased to 100) &mdash; ${seg.start} to ${seg.end}</h3><div class="chart-wrap"><canvas id="eqChart"></canvas></div></div>
    <div class="panel"><h3>Drawdown</h3><div class="chart-wrap"><canvas id="ddChart"></canvas></div></div>
  `;
  const eqCtx = document.getElementById('eqChart');
  charts.push(new Chart(eqCtx, {
    type:'line',
    data:{ labels: seg.equity.labels, datasets:[{ data: seg.equity.values, borderColor:'#4FD8C0', backgroundColor:'rgba(79,216,192,0.08)', fill:true, pointRadius:0, borderWidth:1.5, tension:0.1 }]},
    options:{ responsive:true, maintainAspectRatio:false, plugins:{legend:{display:false}}, scales:{ x:{ ticks:{ color:'#8A97A6', maxTicksLimit:8, font:{family:'IBM Plex Mono', size:10}}, grid:{color:'#1B222C'}}, y:{ ticks:{ color:'#8A97A6', font:{family:'IBM Plex Mono', size:10}}, grid:{color:'#1B222C'}}}}
  }));
  const ddCtx = document.getElementById('ddChart');
  charts.push(new Chart(ddCtx, {
    type:'line',
    data:{ labels: seg.drawdown.labels, datasets:[{ data: seg.drawdown.values, borderColor:'#FF7B72', backgroundColor:'rgba(255,123,114,0.12)', fill:true, pointRadius:0, borderWidth:1.5, tension:0.1 }]},
    options:{ responsive:true, maintainAspectRatio:false, plugins:{legend:{display:false}}, scales:{ x:{ ticks:{ color:'#8A97A6', maxTicksLimit:8, font:{family:'IBM Plex Mono', size:10}}, grid:{color:'#1B222C'}}, y:{ ticks:{ color:'#8A97A6', font:{family:'IBM Plex Mono', size:10}}, grid:{color:'#1B222C'}}}}
  }));
  [...tabsEl.children].forEach((t,i)=>t.classList.toggle('active', i===idx));
}

allSegs.forEach((seg,i)=>{
  const t = document.createElement('div');
  t.className = 'tab' + (i===0?' active':'');
  t.textContent = seg.label;
  t.onclick = ()=>renderSeg(i);
  tabsEl.appendChild(t);
});
renderSeg(0);

// --- Holdings table ---
document.getElementById('holdAsOf').textContent = '\u2014 as of ' + DATA.holdings.as_of + ' \u00b7 total value \u20b9' + fmtNum(DATA.holdings.total_value) + ' \u00b7 cash \u20b9' + fmtNum(DATA.holdings.cash);
const ht = document.getElementById('holdingsTable');
ht.innerHTML = '<tr><th>Ticker</th><th>Name</th><th>Units</th><th>Price</th><th>Value</th></tr>' +
  DATA.holdings.rows.map(r=>`<tr><td>${r.ticker}</td><td>${r.name}</td><td>${fmtNum(r.units)}</td><td>${r.price??'\u2014'}</td><td>\u20b9${fmtNum(r.value)}</td></tr>`).join('');

// --- Signal log ---
const st = document.getElementById('signalTable');
st.innerHTML = '<tr><th>Scan (Sat/Fri close)</th><th>Executed (Mon close)</th><th>Top-N</th></tr>' +
  DATA.signals.map(r=>`<tr><td>${r.scan_date}</td><td>${r.execute_date}</td><td>${r.top_n}</td></tr>`).join('');

// --- Trade log ---
const tt = document.getElementById('tradeTable');
tt.innerHTML = '<tr><th>Date</th><th>Action</th><th>Ticker</th><th>Name</th><th>Units</th><th>Price</th><th>Gross</th><th>Cost</th></tr>' +
  DATA.trades.map(r=>`<tr><td>${r.date}</td><td class="${r.action==='BUY'?'buy':'sell'}">${r.action}</td><td>${r.ticker}</td><td>${r.name}</td><td>${fmtNum(r.units)}</td><td>${r.price}</td><td>${fmtNum(r.gross)}</td><td>${r.cost}</td></tr>`).join('');
</script>
</body>
</html>
"""
