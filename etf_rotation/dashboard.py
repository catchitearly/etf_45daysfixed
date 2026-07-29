"""
Builds a single self-contained docs/index.html dashboard (for GitHub Pages).

Runs BOTH RS methods (Mansfield vs Momentum, see etf_rotation.rs) across all
three reporting segments -- each segment an INDEPENDENT simulation starting
fresh with config.INITIAL_CAPITAL -- under IDENTICAL top_n / rebalance /
cost rules, so any performance difference on the dashboard is attributable
to the signal itself and not to some other config difference.
"""
import json
import os
import datetime as dt

import pandas as pd

from . import config
from .backtest import compute_metrics, drawdown_series, run_all_segments
from .rs import rank_on_date


def _series_for_chart(equity_curve: pd.DataFrame, rebase=True):
    ec = equity_curve
    if len(ec) == 0:
        return [], []
    eq = ec["equity"]
    if rebase:
        eq = eq / eq.iloc[0] * 100
    labels = [d.strftime("%Y-%m-%d") for d in ec.index]
    return labels, [round(v, 3) for v in eq.tolist()]


def _drawdown_for_chart(equity_curve: pd.DataFrame):
    dd = drawdown_series(equity_curve) * 100
    labels = [d.strftime("%Y-%m-%d") for d in dd.index]
    return labels, [round(v, 3) for v in dd.tolist()]


def _method_payload(result, label):
    equity_curve = result["equity_curve"]
    trade_log = result["trade_log"]
    metrics = compute_metrics(equity_curve, trade_log)
    eq_labels, eq_vals = _series_for_chart(equity_curve)
    dd_labels, dd_vals = _drawdown_for_chart(equity_curve)
    return {
        "label": label,
        "metrics": metrics,
        "equity": {"labels": eq_labels, "values": eq_vals},
        "drawdown": {"labels": dd_labels, "values": dd_vals},
    }


def build_current_ranking(rs: pd.DataFrame, prices: pd.DataFrame, top_n, as_of=None):
    as_of = as_of or prices.index.max()
    ranked = rank_on_date(rs, prices, as_of, top_n=max(top_n, 10))
    rows = []
    for i, (t, val) in enumerate(ranked):
        rows.append({
            "rank": i + 1, "ticker": t,
            "name": config.NAME_MAP.get(t, t), "code": config.CODE_MAP.get(t, t),
            "rs": round(val, 3), "in_portfolio": i < top_n,
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


def build_signal_log_table(signal_log: pd.DataFrame, top_n, n=26):
    if signal_log is None or len(signal_log) == 0:
        return []
    sl = signal_log.copy().sort_values("execute_date", ascending=False).head(n)
    sl["scan_date"] = pd.to_datetime(sl["scan_date"]).dt.strftime("%Y-%m-%d")
    sl["execute_date"] = pd.to_datetime(sl["execute_date"]).dt.strftime("%Y-%m-%d")
    rank_cols = [f"rank_{i+1}" for i in range(top_n)]
    sl["top_n"] = sl[rank_cols].apply(
        lambda r: ", ".join(config.CODE_MAP.get(t, t) for t in r if isinstance(t, str)), axis=1
    )
    return sl[["scan_date", "execute_date", "top_n"]].to_dict(orient="records")


SEGMENT_LABELS = {
    "backtest": "Backtest 2018\u20132024",
    "ft1": "Forward Test 2025",
    "ft2": "Forward Test 2026 (YTD)",
}


def _load_robustness_payload():
    if not os.path.exists(config.ROBUSTNESS_JSON):
        return None
    with open(config.ROBUSTNESS_JSON) as f:
        return json.load(f)


def render_dashboard(prices, top_n=config.TOP_N, methods=None, rebalance_mode=None,
                      out_path=config.DASHBOARD_HTML):
    methods = methods or config.RS_METHODS
    rebalance_mode = rebalance_mode or config.REBALANCE_MODE

    all_results = run_all_segments(prices, methods=methods, top_n=top_n, rebalance_mode=rebalance_mode)

    segments_payload = []
    for seg_key, seg_label in SEGMENT_LABELS.items():
        seg_entry = {"key": seg_key, "label": seg_label, "methods": {}}
        for method in methods:
            result = all_results[method][seg_key]
            seg_entry["methods"][method] = _method_payload(result, seg_label)
        segments_payload.append(seg_entry)

    # comparison table: one row per (segment, method)
    comparison_rows = []
    for seg_key, seg_label in SEGMENT_LABELS.items():
        for method in methods:
            m = all_results[method][seg_key]
            metrics = compute_metrics(m["equity_curve"], m["trade_log"])
            comparison_rows.append({"segment": seg_label, "method": method, **metrics})

    # "current" state = latest segment (ft2), shown per method
    latest_seg = "ft2"
    ranking = {}
    holdings = {}
    trades = {}
    signals = {}
    for method in methods:
        result = all_results[method][latest_seg]
        ranking[method] = build_current_ranking(result["rs"], prices, top_n)
        holdings[method] = build_holdings_table(result["final_portfolio"], prices)
        trades[method] = build_trade_log_table(result["trade_log"])
        signals[method] = build_signal_log_table(result["signal_log"], top_n)

    payload = {
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M IST"),
        "data_source": config.DATA_SOURCE,
        "top_n": top_n,
        "lookback": config.LOOKBACK_DAYS,
        "initial_capital": config.INITIAL_CAPITAL,
        "txn_cost_bps": config.TXN_COST_BPS,
        "rebalance_mode": rebalance_mode,
        "methods": methods,
        "segments": segments_payload,
        "comparison_rows": comparison_rows,
        "ranking": ranking,
        "holdings": holdings,
        "trades": trades,
        "signals": signals,
        "robustness": _load_robustness_payload(),
    }

    html = _HTML_TEMPLATE.replace("__PAYLOAD_JSON__", json.dumps(payload))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        f.write(html)
    return out_path


_METHOD_META = {
    "mansfield": {"name": "Mansfield RS", "color": "#4FD8C0",
                  "desc": "ratio-vs-own-45d-SMA \u2014 smoothed, lags trend changes"},
    "momentum": {"name": "Momentum RS", "color": "#E3B341",
                 "desc": "raw 45d return vs peer average \u2014 unsmoothed, reacts immediately"},
}

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
  main{padding:24px 32px 60px; max-width:1320px; margin:0 auto;}

  .tabs{display:flex; gap:4px; margin-bottom:20px; border-bottom:1px solid var(--border); flex-wrap:wrap;}
  .tab{padding:10px 16px; font-family:var(--mono); font-size:13px; color:var(--muted); cursor:pointer; border-bottom:2px solid transparent; user-select:none;}
  .tab.active{color:var(--teal); border-bottom-color:var(--teal);}
  .tab.comparison-tab.active{color:var(--amber); border-bottom-color:var(--amber);}

  .method-cols{display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:20px;}
  @media(max-width:820px){ .method-cols{grid-template-columns:1fr;} }
  .method-head{display:flex; align-items:center; gap:8px; margin-bottom:10px; font-family:var(--mono); font-size:13px; font-weight:600;}
  .dot{width:9px; height:9px; border-radius:50%; display:inline-block;}
  .method-desc{color:var(--muted); font-size:11px; font-family:var(--mono); margin-bottom:12px;}

  .grid{display:grid; grid-template-columns:repeat(auto-fit, minmax(120px,1fr)); gap:10px; margin-bottom:14px;}
  .card{background:var(--panel); border:1px solid var(--border); border-radius:10px; padding:12px 14px;}
  .card .label{font-size:10px; color:var(--muted); text-transform:uppercase; letter-spacing:.05em; font-family:var(--mono);}
  .card .value{font-family:var(--mono); font-size:18px; font-weight:600; margin-top:5px;}
  .pos{color:var(--teal);} .neg{color:var(--coral);}

  .panel{background:var(--panel); border:1px solid var(--border); border-radius:10px; padding:18px 20px; margin-bottom:20px;}
  .panel h3{margin:0 0 14px; font-size:14px; color:var(--muted); font-weight:600; text-transform:uppercase; letter-spacing:.04em; font-family:var(--mono);}
  .chart-wrap{position:relative; height:300px;}
  .chart-wrap.small{height:220px;}

  table{width:100%; border-collapse:collapse; font-family:var(--mono); font-size:12px;}
  th{text-align:left; color:var(--muted); font-weight:500; padding:7px 9px; border-bottom:1px solid var(--border); font-size:10.5px; text-transform:uppercase; letter-spacing:.04em;}
  td{padding:6px 9px; border-bottom:1px solid #1B222C;}
  tr:hover td{background:var(--panel2);}
  .buy{color:var(--teal);} .sell{color:var(--coral);}

  .rs-bar-row{display:flex; align-items:center; gap:10px; margin-bottom:6px;}
  .rs-bar-row .rk{width:20px; color:var(--muted); font-family:var(--mono); font-size:11px;}
  .rs-bar-row .tk{width:110px; font-family:var(--mono); font-size:11.5px; flex-shrink:0;}
  .rs-bar-track{flex:1; background:var(--panel2); border-radius:4px; height:14px; overflow:hidden; position:relative;}
  .rs-bar-fill{height:100%; border-radius:4px;}
  .rs-bar-val{width:56px; text-align:right; font-family:var(--mono); font-size:11px; color:var(--muted);}

  .subtabs{display:flex; gap:4px; margin-bottom:14px;}
  .subtab{padding:6px 12px; font-family:var(--mono); font-size:11.5px; color:var(--muted); cursor:pointer; background:var(--panel2); border:1px solid var(--border); border-radius:6px;}
  .subtab.active{color:var(--bg); background:var(--teal); border-color:var(--teal);}

  .note{color:var(--muted); font-size:11.5px; font-family:var(--mono); line-height:1.6; margin-bottom:14px;}
  .placeholder{color:var(--muted); font-family:var(--mono); font-size:13px; text-align:center; padding:40px 20px;}
  .placeholder code{background:var(--panel2); border:1px solid var(--border); padding:2px 8px; border-radius:4px; color:var(--amber);}
  .badge{display:inline-block; padding:1px 7px; border-radius:10px; font-size:10px; font-family:var(--mono); margin-left:6px;}
  .badge.clean{background:rgba(79,216,192,0.15); color:var(--teal);}
  .badge.warn{background:rgba(255,123,114,0.15); color:var(--coral);}

  footer{text-align:center; color:var(--muted); font-family:var(--mono); font-size:11px; padding:30px; border-top:1px solid var(--border);}
  @media(max-width:700px){ main{padding:16px;} header{padding:20px 16px;} }
</style>
</head>
<body>
<header>
  <div>
    <h1>ETF Relative-Strength Rotation</h1>
    <div class="sub" id="subHeader"></div>
  </div>
  <div class="meta">Last updated <b id="genAt"></b> &middot; source <b id="dataSource"></b><br>Capital \u20b9<span id="capital"></span> (fresh per segment) &middot; Top-<span id="topn"></span> &middot; cost <span id="cost"></span> bps/trade</div>
</header>
<main>

  <div class="tabs" id="segTabs"></div>
  <div id="segBody"></div>
  <div id="robustnessBody" style="display:none;"></div>

  <div class="panel">
    <h3>Method Comparison Summary</h3>
    <table id="comparisonTable"></table>
  </div>

  <div class="panel">
    <h3>Current Relative-Strength Ranking <span id="rankAsOf" style="color:var(--muted); text-transform:none; font-weight:400;"></span></h3>
    <div class="subtabs" id="rankSubtabs"></div>
    <div id="rsLeaderboard"></div>
  </div>

  <div class="panel">
    <h3>Current Holdings <span id="holdAsOf" style="color:var(--muted); text-transform:none; font-weight:400;"></span></h3>
    <div class="subtabs" id="holdSubtabs"></div>
    <table id="holdingsTable"></table>
  </div>

  <div class="panel">
    <h3>Weekly Signal Log (most recent, current segment)</h3>
    <div class="subtabs" id="signalSubtabs"></div>
    <table id="signalTable"></table>
  </div>

  <div class="panel">
    <h3>Trade Log (most recent, current segment)</h3>
    <div class="subtabs" id="tradeSubtabs"></div>
    <table id="tradeTable"></table>
  </div>

</main>
<footer>Systematic simulation for research/educational purposes &middot; not investment advice &middot; generated automatically via GitHub Actions</footer>

<script>
const DATA = __PAYLOAD_JSON__;
const METHOD_META = {
  mansfield: {name:'Mansfield RS', color:'#4FD8C0', desc:'ratio-vs-own-45d-SMA \u2014 smoothed, lags trend changes'},
  momentum:  {name:'Momentum RS',  color:'#E3B341', desc:'raw 45d return vs peer average \u2014 unsmoothed, reacts immediately'}
};

document.getElementById('genAt').textContent = DATA.generated_at;
const srcEl = document.getElementById('dataSource');
srcEl.textContent = DATA.data_source;
srcEl.style.color = DATA.data_source === 'fyers' ? 'var(--amber)' : 'var(--teal)';
document.getElementById('capital').textContent = DATA.initial_capital.toLocaleString('en-IN');
document.getElementById('topn').textContent = DATA.top_n;
document.getElementById('cost').textContent = (DATA.txn_cost_bps*10000).toFixed(0);
document.getElementById('subHeader').textContent =
  `${DATA.methods.map(m=>METHOD_META[m].name).join(' vs ')} \u00b7 ${DATA.lookback}d lookback \u00b7 rebalance: ${DATA.rebalance_mode} \u00b7 weekly Saturday scan / Monday execution`;

function fmtPct(v){ if(v===null||v===undefined) return '\u2014'; const cls = v>=0?'pos':'neg'; return '<span class="'+cls+'">'+(v>=0?'+':'')+v.toFixed(2)+'%</span>'; }
function fmtNum(v){ return v===null||v===undefined ? '\u2014' : Number(v).toLocaleString('en-IN'); }

// ============================================================
// Segment tabs (Backtest / FT1 / FT2), each showing both methods side by side
// ============================================================
const tabsEl = document.getElementById('segTabs');
const bodyEl = document.getElementById('segBody');
let charts = [];

function renderSeg(idx){
  charts.forEach(c=>c.destroy()); charts=[];
  const seg = DATA.segments[idx];

  let cardsHtml = '<div class="method-cols">';
  DATA.methods.forEach(method=>{
    const mp = seg.methods[method];
    const meta = METHOD_META[method];
    const m = mp.metrics;
    cardsHtml += `<div>
      <div class="method-head"><span class="dot" style="background:${meta.color}"></span>${meta.name}</div>
      <div class="method-desc">${meta.desc}</div>
      <div class="grid">
        <div class="card"><div class="label">Total Return</div><div class="value">${fmtPct(m.total_return_pct)}</div></div>
        <div class="card"><div class="label">CAGR</div><div class="value">${fmtPct(m.cagr_pct)}</div></div>
        <div class="card"><div class="label">Max DD</div><div class="value">${fmtPct(m.max_drawdown_pct)}</div></div>
        <div class="card"><div class="label">Sharpe</div><div class="value">${m.sharpe ?? '\u2014'}</div></div>
        <div class="card"><div class="label">Calmar</div><div class="value">${m.calmar ?? '\u2014'}</div></div>
        <div class="card"><div class="label">Trades</div><div class="value">${fmtNum(m.num_trades)}</div></div>
      </div>
    </div>`;
  });
  cardsHtml += '</div>';

  bodyEl.innerHTML = `
    ${cardsHtml}
    <div class="panel"><h3>Equity Curve Overlay (rebased to 100)</h3><div class="chart-wrap"><canvas id="eqChart"></canvas></div></div>
    <div class="panel"><h3>Drawdown Overlay</h3><div class="chart-wrap small"><canvas id="ddChart"></canvas></div></div>
  `;

  const eqDatasets = DATA.methods.map(method=>{
    const mp = seg.methods[method];
    const meta = METHOD_META[method];
    return { label: meta.name, data: mp.equity.values, borderColor: meta.color,
             backgroundColor: 'transparent', fill:false, pointRadius:0, borderWidth:1.6, tension:0.1 };
  });
  const anyLabels = seg.methods[DATA.methods[0]].equity.labels;
  charts.push(new Chart(document.getElementById('eqChart'), {
    type:'line',
    data:{ labels: anyLabels, datasets: eqDatasets },
    options:{ responsive:true, maintainAspectRatio:false,
      plugins:{legend:{labels:{color:'#8A97A6', font:{family:'IBM Plex Mono', size:11}}}},
      scales:{ x:{ ticks:{ color:'#8A97A6', maxTicksLimit:8, font:{family:'IBM Plex Mono', size:10}}, grid:{color:'#1B222C'}},
               y:{ ticks:{ color:'#8A97A6', font:{family:'IBM Plex Mono', size:10}}, grid:{color:'#1B222C'}}}}
  }));

  const ddDatasets = DATA.methods.map(method=>{
    const mp = seg.methods[method];
    const meta = METHOD_META[method];
    return { label: meta.name, data: mp.drawdown.values, borderColor: meta.color,
             backgroundColor:'transparent', fill:false, pointRadius:0, borderWidth:1.4, tension:0.1 };
  });
  charts.push(new Chart(document.getElementById('ddChart'), {
    type:'line',
    data:{ labels: anyLabels, datasets: ddDatasets },
    options:{ responsive:true, maintainAspectRatio:false,
      plugins:{legend:{labels:{color:'#8A97A6', font:{family:'IBM Plex Mono', size:11}}}},
      scales:{ x:{ ticks:{ color:'#8A97A6', maxTicksLimit:8, font:{family:'IBM Plex Mono', size:10}}, grid:{color:'#1B222C'}},
               y:{ ticks:{ color:'#8A97A6', font:{family:'IBM Plex Mono', size:10}}, grid:{color:'#1B222C'}}}}
  }));

  [...tabsEl.children].forEach((t,i)=>t.classList.toggle('active', i===idx));
}

DATA.segments.forEach((seg,i)=>{
  const t = document.createElement('div');
  t.className = 'tab' + (i===0?' active':'');
  t.textContent = seg.label;
  t.onclick = ()=>{
    document.getElementById('segBody').style.display = '';
    document.getElementById('robustnessBody').style.display = 'none';
    renderSeg(i);
  };
  tabsEl.appendChild(t);
});
renderSeg(0);

const robTab = document.createElement('div');
robTab.className = 'tab comparison-tab';
robTab.textContent = 'Robustness & Curve-Fit Checks';
robTab.onclick = ()=>{
  [...tabsEl.children].forEach(t=>t.classList.remove('active'));
  robTab.classList.add('active');
  document.getElementById('segBody').style.display = 'none';
  document.getElementById('robustnessBody').style.display = '';
  renderRobustness();
};
tabsEl.appendChild(robTab);

// ============================================================
// Comparison summary table
// ============================================================
const ct = document.getElementById('comparisonTable');
ct.innerHTML = '<tr><th>Segment</th><th>Method</th><th>Total Return</th><th>CAGR</th><th>Max DD</th><th>Sharpe</th><th>Calmar</th><th>Trades</th></tr>' +
  DATA.comparison_rows.map(r=>{
    const meta = METHOD_META[r.method];
    return `<tr><td>${r.segment}</td><td><span class="dot" style="background:${meta.color}; margin-right:6px;"></span>${meta.name}</td>
      <td>${fmtPct(r.total_return_pct)}</td><td>${fmtPct(r.cagr_pct)}</td><td>${fmtPct(r.max_drawdown_pct)}</td>
      <td>${r.sharpe ?? '\u2014'}</td><td>${r.calmar ?? '\u2014'}</td><td>${fmtNum(r.num_trades)}</td></tr>`;
  }).join('');

// ============================================================
// Per-method sub-tabbed sections: ranking / holdings / signals / trades
// ============================================================
function buildSubtabs(containerId, renderFn){
  const el = document.getElementById(containerId);
  DATA.methods.forEach((method,i)=>{
    const b = document.createElement('div');
    b.className = 'subtab' + (i===0?' active':'');
    b.textContent = METHOD_META[method].name;
    b.onclick = ()=>{
      [...el.children].forEach(c=>c.classList.remove('active'));
      b.classList.add('active');
      renderFn(method);
    };
    el.appendChild(b);
  });
  renderFn(DATA.methods[0]);
}

buildSubtabs('rankSubtabs', (method)=>{
  const rk = DATA.ranking[method];
  document.getElementById('rankAsOf').textContent = '\u2014 as of ' + rk.as_of;
  const lb = document.getElementById('rsLeaderboard');
  lb.innerHTML = '';
  const maxAbsRS = Math.max(...rk.rows.map(r=>Math.abs(r.rs)), 1);
  rk.rows.forEach(r=>{
    const pct = Math.min(Math.abs(r.rs)/maxAbsRS*100, 100);
    const color = r.in_portfolio ? METHOD_META[method].color : (r.rs>=0 ? '#3A5F58' : 'var(--coral)');
    const row = document.createElement('div');
    row.className = 'rs-bar-row';
    row.innerHTML = `<div class="rk">${r.rank}</div><div class="tk">${r.code}</div>
      <div class="rs-bar-track"><div class="rs-bar-fill" style="width:${pct}%; background:${color};"></div></div>
      <div class="rs-bar-val">${r.rs.toFixed(2)}</div>`;
    lb.appendChild(row);
  });
});

buildSubtabs('holdSubtabs', (method)=>{
  const h = DATA.holdings[method];
  document.getElementById('holdAsOf').textContent = '\u2014 as of ' + h.as_of + ' \u00b7 total value \u20b9' + fmtNum(h.total_value) + ' \u00b7 cash \u20b9' + fmtNum(h.cash);
  const ht = document.getElementById('holdingsTable');
  ht.innerHTML = '<tr><th>Ticker</th><th>Name</th><th>Units</th><th>Price</th><th>Value</th></tr>' +
    h.rows.map(r=>`<tr><td>${r.ticker}</td><td>${r.name}</td><td>${fmtNum(r.units)}</td><td>${r.price??'\u2014'}</td><td>\u20b9${fmtNum(r.value)}</td></tr>`).join('');
});

buildSubtabs('signalSubtabs', (method)=>{
  const st = document.getElementById('signalTable');
  st.innerHTML = '<tr><th>Scan (Sat/Fri close)</th><th>Executed (Mon close)</th><th>Top-N</th></tr>' +
    DATA.signals[method].map(r=>`<tr><td>${r.scan_date}</td><td>${r.execute_date}</td><td>${r.top_n}</td></tr>`).join('');
});

buildSubtabs('tradeSubtabs', (method)=>{
  const tt = document.getElementById('tradeTable');
  tt.innerHTML = '<tr><th>Date</th><th>Action</th><th>Ticker</th><th>Name</th><th>Units</th><th>Price</th><th>Gross</th><th>Cost</th></tr>' +
    DATA.trades[method].map(r=>`<tr><td>${r.date}</td><td class="${r.action==='BUY'?'buy':'sell'}">${r.action}</td><td>${r.ticker}</td><td>${r.name}</td><td>${fmtNum(r.units)}</td><td>${r.price}</td><td>${fmtNum(r.gross)}</td><td>${r.cost}</td></tr>`).join('');
});

// ============================================================
// Robustness & curve-fit-avoidance tab
// ============================================================
let robCharts = [];
let robRendered = false;

function renderRobustness(){
  const el = document.getElementById('robustnessBody');
  const R = DATA.robustness;

  if (!R){
    el.innerHTML = `<div class="panel"><div class="placeholder">
      Robustness suite hasn't been generated yet.<br><br>
      Run <code>python scripts/run_robustness.py</code> locally, or trigger the
      <code>Robustness Sweep</code> GitHub Actions workflow, then refresh this page.
      </div></div>`;
    return;
  }

  if (robRendered){ return; }  // charts only need to be built once; data doesn't change on tab toggle
  robRendered = true;
  robCharts.forEach(c=>c.destroy()); robCharts=[];

  const methods = Object.keys(R.best_full_period_config);
  const pctCols = ['5','25','50','75','95'];

  // ---- A. Lookback stability sweep ----
  let html = `<div class="panel">
    <h3>Lookback Stability Sweep (${R.lookback_sweep_range[0]}\u2013${R.lookback_sweep_range[1]} days, step ${R.lookback_sweep_range[2]})</h3>
    <div class="note">A real edge shows a smooth hill in Sharpe/CAGR vs. lookback. A spike at one specific
    value with noise on either side is a sign of curve-fitting to that value, not a genuine signal.</div>
    <div class="method-cols">
      <div class="chart-wrap"><canvas id="sweepSharpeChart"></canvas></div>
      <div class="chart-wrap"><canvas id="sweepCagrChart"></canvas></div>
    </div>
  </div>`;

  // ---- B. Walk-forward validation ----
  html += `<div class="panel"><h3>Walk-Forward Validation</h3>
    <div class="note">Best lookback chosen using ONLY 2018\u20132022 (train). That exact, unchanged config is then
    tested \u2014 without re-tuning \u2014 on 2023\u20132024, 2025, and 2026 YTD.</div>`;
  methods.forEach(method=>{
    const wf = R.walk_forward[method];
    const meta = METHOD_META[method];
    html += `<div class="method-head" style="margin-top:10px;"><span class="dot" style="background:${meta.color}"></span>${meta.name}
      \u2014 locked lookback: <b style="color:${meta.color}">${wf.locked_lookback}d</b> (train Sharpe ${wf.train_sharpe ?? '\u2014'}, window ${wf.train_window[0]} to ${wf.train_window[1]})</div>
    <table><tr><th>Test Period</th><th>CAGR</th><th>Max DD</th><th>Sharpe</th><th>Calmar</th><th>Trades</th></tr>`;
    for (const [label, m] of Object.entries(wf.test_results)){
      html += `<tr><td>${label}</td><td>${fmtPct(m.cagr_pct)}</td><td>${fmtPct(m.max_drawdown_pct)}</td><td>${m.sharpe ?? '\u2014'}</td><td>${m.calmar ?? '\u2014'}</td><td>${fmtNum(m.num_trades)}</td></tr>`;
    }
    html += `</table>`;
  });
  html += `</div>`;

  // ---- C. Regime-split ----
  html += `<div class="panel"><h3>Regime-Split Performance (locked lookback per method)</h3>
    <div class="note">Same locked config as the walk-forward test, broken out by broad NSE market regime, so a method
    that only works in trending markets can't hide behind a good blended average.</div>
    <table><tr><th>Regime</th>${methods.map(m=>`<th>${METHOD_META[m].name} CAGR</th><th>${METHOD_META[m].name} Max DD</th>`).join('')}</tr>`;
  const regimeLabels = {
    "2018_choppy": "2018 (choppy/correction)", "2019_sideways": "2019 (sideways)",
    "2020_2021_bull": "2020\u20132021 (COVID crash + bull)", "2022_bear_choppy": "2022 (bear/choppy)",
    "2023_2024_bull": "2023\u20132024 (bull/grind-up)"
  };
  Object.keys(regimeLabels).forEach(rk=>{
    html += `<tr><td>${regimeLabels[rk]}</td>`;
    methods.forEach(method=>{
      const m = R.regime_split[method].regimes[rk];
      html += `<td>${fmtPct(m.cagr_pct)}</td><td>${fmtPct(m.max_drawdown_pct)}</td>`;
    });
    html += `</tr>`;
  });
  html += `</table></div>`;

  // ---- D. Bootstrap resampling ----
  html += `<div class="panel"><h3>Bootstrap Resampling (1000x, trades resampled with replacement)</h3>
    <div class="note">Distribution of outcomes if the SAME per-trade returns had occurred in a resampled order/mix,
    for the best full-period lookback per method. If the 5th-percentile is near or below zero, the edge may not be
    statistically robust \u2014 it could be a good draw rather than a good strategy.</div>`;
  methods.forEach(method=>{
    const b = R.bootstrap[method];
    const meta = METHOD_META[method];
    if (!b){ html += `<div class="note">${meta.name}: no trades to resample.</div>`; return; }
    html += `<div class="method-head"><span class="dot" style="background:${meta.color}"></span>${meta.name} \u2014 ${b.n_trades} round-trip trades, best full-period lookback ${R.best_full_period_config[method].best_lookback_full_period}d</div>
    <table><tr><th>Percentile</th>${pctCols.map(p=>`<th>P${p}</th>`).join('')}</tr>
      <tr><td>Mean trade return</td>${pctCols.map(p=>`<td>${fmtPct(b.mean_return_pct[p])}</td>`).join('')}</tr>
      <tr><td>Sharpe-like</td>${pctCols.map(p=>`<td>${b.sharpe_like[p]}</td>`).join('')}</tr>
      <tr><td>Total compounded return</td>${pctCols.map(p=>`<td>${fmtPct(b.total_compounded_return_pct[p])}</td>`).join('')}</tr>
    </table>`;
  });
  html += `</div>`;

  // ---- E. Trade-order shuffle test ----
  html += `<div class="panel"><h3>Trade-Order Shuffle Test (500x, same trades reordered)</h3>
    <div class="note">Same trades, randomly reordered \u2014 total return is necessarily identical across shuffles
    (it's the same multiset of returns compounded), but Max Drawdown and Calmar are path-dependent. Wide spread means
    the headline drawdown number owes a lot to when winners/losers happened to land, not just the strategy itself.</div>`;
  methods.forEach(method=>{
    const s = R.shuffle_test[method];
    const meta = METHOD_META[method];
    if (!s){ html += `<div class="note">${meta.name}: no trades to shuffle.</div>`; return; }
    html += `<div class="method-head"><span class="dot" style="background:${meta.color}"></span>${meta.name}</div>
    <table><tr><th>Percentile</th>${pctCols.map(p=>`<th>P${p}</th>`).join('')}</tr>
      <tr><td>Max Drawdown</td>${pctCols.map(p=>`<td>${fmtPct(s.max_dd_pct[p])}</td>`).join('')}</tr>
      <tr><td>Calmar-like</td>${pctCols.map(p=>`<td>${s.calmar_like ? s.calmar_like[p] : '\u2014'}</td>`).join('')}</tr>
    </table>`;
  });
  html += `</div>`;

  // ---- F. Data quality ----
  const dq = R.data_quality;
  const invIssues = R.portfolio_invariant_issues || [];
  html += `<div class="panel"><h3>Data Quality &amp; Portfolio Invariant Checks
    ${invIssues.length===0 ? '<span class="badge clean">clean</span>' : `<span class="badge warn">${invIssues.length} issue(s)</span>`}
    </h3>
    <div class="note">Flags any single-day price move &gt;${dq.suspicious_moves_threshold_pct}% (ETFs essentially never move
    this much in one session under normal conditions \u2014 could be a stale/bad tick or an unadjusted corporate action,
    worth checking against actual NSE data before trusting numbers near it) and independently re-verifies that equity
    never went negative or NaN anywhere in the best-lookback runs.</div>`;
  if (dq.n_flags === 0){
    html += `<div class="note">No suspicious single-day moves &gt;${dq.suspicious_moves_threshold_pct}% found across the universe.</div>`;
  } else {
    html += `<table><tr><th>Ticker</th><th>Date</th><th>Prev Price</th><th>Price</th><th>Move</th></tr>` +
      dq.flags.map(f=>`<tr><td>${f.ticker}</td><td>${f.date}</td><td>${f.prev_price ?? '\u2014'}</td><td>${f.price}</td><td>${fmtPct(f.pct_move)}</td></tr>`).join('') +
      `</table>`;
  }
  if (invIssues.length > 0){
    html += `<table style="margin-top:10px;"><tr><th>Type</th><th>Method</th><th>Lookback</th><th>Count</th><th>Detail</th></tr>` +
      invIssues.map(i=>`<tr><td>${i.type}</td><td>${i.method}</td><td>${i.lookback}</td><td>${i.count}</td><td>${JSON.stringify(i)}</td></tr>`).join('') +
      `</table>`;
  }
  html += `</div>`;

  el.innerHTML = html;

  // ---- Charts for section A ----
  const sweepByMethod = {};
  methods.forEach(m => sweepByMethod[m] = R.sweep.filter(r=>r.method===m).sort((a,b)=>a.lookback-b.lookback));
  const lookbackLabels = sweepByMethod[methods[0]].map(r=>r.lookback);

  robCharts.push(new Chart(document.getElementById('sweepSharpeChart'), {
    type:'line',
    data:{ labels: lookbackLabels, datasets: methods.map(m=>({
      label: METHOD_META[m].name + ' Sharpe', data: sweepByMethod[m].map(r=>r.sharpe),
      borderColor: METHOD_META[m].color, backgroundColor:'transparent', fill:false, pointRadius:2, borderWidth:1.6, tension:0.25
    })) },
    options:{ responsive:true, maintainAspectRatio:false,
      plugins:{ legend:{labels:{color:'#8A97A6', font:{family:'IBM Plex Mono', size:10}}}, title:{display:true, text:'Sharpe vs. Lookback (days)', color:'#8A97A6', font:{family:'IBM Plex Mono', size:11}} },
      scales:{ x:{ title:{display:true, text:'lookback (days)', color:'#8A97A6'}, ticks:{ color:'#8A97A6', font:{family:'IBM Plex Mono', size:10}}, grid:{color:'#1B222C'}},
               y:{ ticks:{ color:'#8A97A6', font:{family:'IBM Plex Mono', size:10}}, grid:{color:'#1B222C'}}}}
  }));

  robCharts.push(new Chart(document.getElementById('sweepCagrChart'), {
    type:'line',
    data:{ labels: lookbackLabels, datasets: methods.map(m=>({
      label: METHOD_META[m].name + ' CAGR %', data: sweepByMethod[m].map(r=>r.cagr_pct),
      borderColor: METHOD_META[m].color, backgroundColor:'transparent', fill:false, pointRadius:2, borderWidth:1.6, tension:0.25
    })) },
    options:{ responsive:true, maintainAspectRatio:false,
      plugins:{ legend:{labels:{color:'#8A97A6', font:{family:'IBM Plex Mono', size:10}}}, title:{display:true, text:'CAGR % vs. Lookback (days)', color:'#8A97A6', font:{family:'IBM Plex Mono', size:11}} },
      scales:{ x:{ title:{display:true, text:'lookback (days)', color:'#8A97A6'}, ticks:{ color:'#8A97A6', font:{family:'IBM Plex Mono', size:10}}, grid:{color:'#1B222C'}},
               y:{ ticks:{ color:'#8A97A6', font:{family:'IBM Plex Mono', size:10}}, grid:{color:'#1B222C'}}}}
  }));
}

</script>
</body>
</html>
"""
