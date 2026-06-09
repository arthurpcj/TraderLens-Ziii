"""Local interactive HTML pivot report (Priority 2 / FR-PIVOT phase-1).

SQLite full archive -> round-trips (+ decoupled annotation layer) -> a
SELF-CONTAINED single HTML file (offline, portable). Five views (FR-PIVOT-4/5):
  1. KPI headline   — Net/Gross P&L, commissions, win rate, Profit Factor,
                      Expectancy, Max Drawdown (amt/%/days), win/loss streaks.
  2. Calendar heat  — daily net P&L, click a day to drill into its round-trips;
                      toggle-filter by setup / class.
  3. Equity curve   — cumulative net P&L (inline SVG, no chart lib) + max-DD band.
  4. By-setup table — performance + execution (winner vs loser avg hold).
  5. Pivot + detail — PivotTable.js drag-slice + a sortable/filterable table.

Colors are neutral & cross-cultural (FR-PIVOT-7): blue=profit / amber=loss, NOT
red/green, always paired with +/- and up/down glyphs. Reads SQLite only (no
Flex). Output (reports/pivot_latest.html) is gitignored (contains real trades).

CLI: python -m src.pivot [--db PATH] [--out PATH]
     python -m src.pivot --tag-template [--annotations PATH]   # refresh CSV
     python -m src.pivot --review-flow                          # glued review loop
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import subprocess
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

from . import annotations, exporter, r_multiple, sqlite_store
from .constants import (
    ANNOTATIONS_PATH,
    DEFAULT_EXPORT_LOOKBACK_DAYS,
    ET_TZ,
    EXPORT_DIR,
    PROJECT_ROOT,
    SQLITE_PATH,
)
from .roundtrip import RoundTrip, coalesce_fills, pair_round_trips

VENDOR = PROJECT_ROOT / "assets" / "vendor"
DEFAULT_OUT = PROJECT_ROOT / "reports" / "pivot_latest.html"

# Page CSS + client app are plain constants (NOT f-strings) so their many { }
# need no escaping. They reference the injected globals DATA + CFG.
_PAGE_CSS = """
 /* Tier-1 #2: responsive layout. CSS Grid + media queries fill the screen
    on wide monitors, gracefully degrade on laptops/tablets. Capped at
    1800px so 27"/4K monitors don't stretch text past readable line length.
    Structure kept semantic + class-based so a future --email render path
    can target the same DOM with inline-styled table layout. */
 *{box-sizing:border-box}
 body{font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;
   max-width:1800px;margin:24px auto;padding:0 24px;color:#222}
 h1{margin:0 0 2px} h2{margin:30px 0 8px;font-size:16px}
 .meta{color:#888;font-size:12px;margin-bottom:16px}

 /* KPI cards (Tier-1 #4) — three visual tiers: headline (Net P&L, big),
    primary edge metrics (medium grid), secondary accounting/streaks (small).
    auto-fit (not auto-fill) collapses empty tracks so existing cards stretch
    to fill the row — phase-1's auto-fill left visible empty columns on wide
    screens (user-observed right-side dead area). */
 .card{border:1px solid #e5e5e5;border-radius:8px;padding:9px 14px;min-width:0}
 .card .k{color:#888;font-size:11px;text-transform:uppercase;letter-spacing:.02em}
 .card .v{font-size:19px;font-weight:600;margin-top:2px;overflow-wrap:break-word}
 .card small{color:#999;font-weight:400;font-size:11px}

 .cards-headline{margin:8px 0 12px}
 .card-xl{border:1px solid #d8dde2;border-radius:10px;padding:14px 20px;background:#fafbfc}
 .card-xl .k{color:#888;font-size:11px;text-transform:uppercase;letter-spacing:.03em}
 .card-xl .v{font-size:34px;font-weight:700;line-height:1.15;margin-top:4px}

 .cards-primary{display:grid;grid-template-columns:repeat(auto-fit, minmax(180px, 1fr));
   gap:10px;margin:8px 0}

 .cards-secondary{display:grid;grid-template-columns:repeat(auto-fit, minmax(130px, 1fr));
   gap:8px;margin:8px 0 16px}
 .cards-secondary .card{padding:6px 10px}
 .cards-secondary .card .v{font-size:15px}
 .cards-secondary .card .k{font-size:10px}

 /* 2-row KPI layout: NET P&L hero on the left spans full block height,
    primary + secondary stack on the right. Collapses to 1-col on narrow
    screens so the hero stays glance-prominent without crushing cards. */
 .kpi-grid{display:grid;grid-template-columns:minmax(220px, 1.4fr) 5fr;
   gap:12px;margin:8px 0 16px;align-items:stretch}
 .kpi-grid > .cards-headline{margin:0}
 .kpi-grid .kpi-right{display:flex;flex-direction:column;gap:8px;min-width:0}
 .kpi-grid .kpi-right > div{margin:0}
 .kpi-grid .card-xl{height:100%;display:flex;flex-direction:column;justify-content:center}
 .kpi-grid .card-xl .v{font-size:42px}

 /* Brand block — right column of the header bar; never fights the filters. */
 .brand{flex:0 1 auto;text-align:right;min-width:0}
 .brand h1{margin:0;font-size:19px;line-height:1.2}
 .brand .meta{font-size:11px;color:#888;margin-top:3px}

 .pos{color:#2b6cb0} .neg{color:#c2792e}
 table.grid{border-collapse:collapse;margin:8px 0;font-size:13px}
 table.grid th,table.grid td{border:1px solid #e5e5e5;padding:4px 10px;text-align:right;white-space:nowrap}
 table.grid th:first-child,table.grid td:first-child{text-align:left}
 table.grid th{background:#f7f7f8;cursor:default}
 table.sortable th{cursor:pointer} table.sortable th:hover{background:#eef1f4}
 .note{color:#999;font-size:11px;margin:4px 0}
 .warn{background:#fbf6ec;border:1px solid #e7d6b0;border-radius:6px;padding:8px 12px;font-size:12px;margin:8px 0}
 .muted{color:#999}

 /* sticky header bar (Tier-1 #1) — filters left, brand right; bleeds to edges. */
 .header-bar{position:sticky;top:0;background:#fdfdfd;border-bottom:1px solid #e5e5e5;
   padding:8px 24px;margin:0 -24px 16px -24px;z-index:100;
   box-shadow:0 1px 3px rgba(0,0,0,0.04);font-size:12px;color:#444;
   display:flex;justify-content:space-between;align-items:flex-start;gap:24px;flex-wrap:wrap}
 .header-bar .filters{flex:1 1 460px;min-width:0}
 .header-bar .range-info{margin-bottom:4px}
 .header-bar #rangeLabel{font-weight:600;color:#222}
 .header-bar .controls{margin-top:4px}
 .header-bar .controls select{font-size:12px;margin:0 4px}
 .header-bar .controls button{font-size:12px;margin:0 2px;padding:1px 6px}
 .header-bar .controls input[type=date]{font-size:11px;padding:1px 4px;border:1px solid #ccc;border-radius:3px;margin:0 2px}
 .header-bar .controls label{margin:0 4px}
 .header-bar .controls .sep{color:#ccc;margin:0 4px}

 /* Tier-1 #5 preset chips. Active chip filled blue (matches POS color). */
 .chips{display:flex;flex-wrap:wrap;gap:4px;margin:2px 0 4px}
 .chip{font:inherit;font-size:11px;padding:3px 9px;border:1px solid #ccc;
   background:#fff;border-radius:11px;cursor:pointer;color:#444}
 .chip:hover{background:#f0f0f0}
 .chip.active{background:#2b6cb0;color:#fff;border-color:#2b6cb0}

 /* Calendar — FR-PIVOT-8: the viewport is exactly `calCols` months and renders
    as a SINGLE row (no wrap). Cell width scales up as months-per-row goes down
    so fewer columns get more breathing room; data-cols="N" on .cal-wrap (set by
    renderCal from `calCols`) drives the size variants. overflow-x:auto lets a
    wide window (cols=4) or a narrow screen scroll instead of wrapping — the old
    flex-wrap + max-width caps wrapped the 3rd month onto a 2nd row once a full
    3-month window exceeded the cap. 6 weekday columns (Mon-Fri + Sun, no Sat). */
 .cal-wrap{display:flex;flex-wrap:nowrap;gap:18px;align-items:flex-start;
   overflow-x:auto}
 .cal-wrap[data-cols="1"] .cal td,.cal-wrap[data-cols="1"] .cal th{width:100px;height:80px}
 .cal-wrap[data-cols="1"] .cal .dn{font-size:13px}
 .cal-wrap[data-cols="1"] .cal .dp{font-size:15px}
 .cal-wrap[data-cols="1"] .cal .dc{font-size:11px}
 .cal-wrap[data-cols="2"] .cal td,.cal-wrap[data-cols="2"] .cal th{width:72px;height:60px}
 .cal-wrap[data-cols="2"] .cal .dp{font-size:12px}
 .cal-wrap[data-cols="3"] .cal td,.cal-wrap[data-cols="3"] .cal th{width:56px;height:50px}
 /* [data-cols="4"] uses the base 46×42 cell — phase-1 default density */

 .cal-cols-picker{font-size:11px;color:#666;margin:4px 0 8px;
   display:flex;align-items:center;gap:6px;flex-wrap:wrap}
 .cal-cols-picker .chip{padding:2px 10px;min-width:26px}
 .cal-cols-picker .chip:disabled{opacity:.35;cursor:default}
 .cal{font-size:11px}
 .cal h3{font-size:13px;margin:4px 0;font-weight:600;cursor:pointer;user-select:none}
 .cal h3:hover{color:#2b6cb0}
 .cal table{border-collapse:collapse} .cal td,.cal th{width:46px;height:42px;border:1px solid #eee;
   text-align:center;vertical-align:top;padding:2px}
 .cal th{height:auto;color:#999;font-weight:500}
 .cal td.day{cursor:pointer;user-select:none} .cal td.day:hover{outline:2px solid #2b6cb0}
 .cal .dn{color:#999;font-size:10px} .cal .dp{font-weight:600;font-size:11px} .cal .dc{color:#777;font-size:9px}
 .cal td.sel{outline:2px solid #2b6cb0}
 /* Tier-1 #5 active-range highlight — inset blue outline overlays the
    heatmap shade without disturbing it (background-color would clash). */
 .cal td.in-range{box-shadow:inset 0 0 0 2px rgba(43,108,176,0.55)}
 .drill{margin:8px 0;font-size:12px;overflow-x:auto}

 #detailFilter{font-size:12px;padding:3px 6px;width:280px;max-width:100%;margin:4px 0}
 #detailCount{font-size:11px;color:#888;margin-left:10px}
 #detailExport{font-size:12px;padding:3px 8px;margin-left:10px;cursor:pointer}
 .scroll-x{overflow-x:auto;max-width:100%}
 .row-idx{color:#aaa;text-align:right;font-variant-numeric:tabular-nums;
   width:1%;white-space:nowrap;padding-left:8px;padding-right:8px}

 /* Tier-1 #3: detail table — bounded scroll region + browser-native
    virtualization (content-visibility) + sticky thead. Scales to ~50k rows
    without any JS virtualization library; max-height keeps the page navigable
    even when archives grow to thousands of round-trips. */
 #detail{overflow:auto;max-height:600px;max-width:100%;border:1px solid #eee;border-radius:4px}
 table.virt thead th{position:sticky;top:0;background:#f7f7f8;z-index:1;
   box-shadow:inset 0 -1px 0 #e5e5e5}
 table.virt tbody tr{content-visibility:auto;contain-intrinsic-size:0 28px}

 /* --- Breakpoints. KPI tiers shrink, calendar collapses to 1-col on narrow. */
 @media (max-width: 1000px){
   .cards-primary{grid-template-columns:repeat(auto-fill, minmax(150px, 1fr))}
   .cards-secondary{grid-template-columns:repeat(auto-fill, minmax(110px, 1fr))}
   .card .v{font-size:17px}
   .card-xl .v{font-size:28px}
   .kpi-grid .card-xl .v{font-size:32px}
 }
 @media (max-width: 700px){
   body{padding:0 14px;margin:14px auto}
   .header-bar{padding:8px 14px;margin:0 -14px 14px -14px}
   .brand{text-align:left;flex-basis:100%}
   .cards-primary{grid-template-columns:repeat(2, 1fr);gap:8px}
   .cards-secondary{grid-template-columns:repeat(2, 1fr);gap:6px}
   .kpi-grid{grid-template-columns:1fr}
   .kpi-grid .card-xl{height:auto}
   .kpi-grid .card-xl .v{font-size:26px}
   .card{padding:8px 10px} .card .v{font-size:15px}
   .card-xl{padding:10px 14px} .card-xl .v{font-size:24px}
   .cal-wrap{grid-template-columns:1fr}
   h2{font-size:15px;margin:22px 0 6px}
 }
"""

_APP_JS = r"""
(function(){
  // === Tier-1 #1: filter state as the single source. All sections re-render
  // off the same `filterState` so KPI / equity / by-setup / calendar / detail
  // / pivot stay in lockstep (full-linkage). Time-range UI (chips / from-to /
  // ←→ / calendar drag-select) is Tier-1 #5; the from/to slots in state are
  // already wired so adding the UI later is zero refactor.
  var POS = CFG.pos, NEG = CFG.neg;
  var filterState = { setup: '', "class": '', from: null, to: null };
  var activePreset = 'all';   // Tier-1 #5: which chip is "lit"; null = custom (←→/manual/drag)
  var dragStart = null;       // Tier-1 #5: drag-select on calendar cells
  var LIFETIME = null;        // Tier-1 #6: full-DATA baseline, set once at boot;
                              // streak cards show "<current> · all-time: <lifetime>"
                              // so user never loses absolute reference under filter.
  var calCols = 3;            // FR-PIVOT-8: viewport width = months shown (default 3)
  var calAnchor = null;       // FR-PIVOT-8: right-edge month "YYYY-MM" of the
                              // calendar viewport; set at boot to most-recent data month
  var $sel = function(id){return document.getElementById(id);};

  // === helpers ===
  function fmt(v){ if(v==null) return ''; var s=(v<0?'-':'+')+'$'+Math.abs(v).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2}); return s; }
  function signed(v){ return (v>=0?'+':'-')+'$'+Math.abs(v).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2}); }
  function cls(v){ return v>=0?'pos':'neg'; }
  function esc(s){ return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];}); }
  function pnl(r){ return r.PnL_USD==null?0:r.PnL_USD; }

  function uniq(key){ var s={}; DATA.forEach(function(r){ s[r[key]]=1; }); return Object.keys(s).sort(); }
  function fillSel(id,label,key){ var el=$sel(id); el.innerHTML='<option value="">all '+label+'</option>'+
      uniq(key).map(function(v){return '<option value="'+esc(v)+'">'+esc(v)+'</option>';}).join(''); }

  // The single filter predicate everything uses.
  function filtered(){
    var s = filterState;
    return DATA.filter(function(r){
      if(s.setup && r.Setup !== s.setup) return false;
      if(s["class"] && r.Class !== s["class"]) return false;
      if(s.from && r.CloseDate < s.from) return false;
      if(s.to && r.CloseDate > s.to) return false;
      return true;
    });
  }

  // === Tier-1 #5: time filter system ===
  // "Today" must be NY time, not local — sister-project MEMORY's timezone iron
  // rule (the difference matters around midnight Beijing = noon NY when a NY
  // user has been active and a Beijing user is finishing their day).
  function nyToday(){
    return new Date().toLocaleDateString('en-CA', {timeZone:'America/New_York'});
  }
  function addDays(iso, n){
    return new Date(Date.parse(iso+'T00:00:00Z') + n*86400000).toISOString().slice(0,10);
  }
  function startOfWeek(iso){    // Monday of the ISO week containing iso (UTC math)
    var dow = new Date(Date.parse(iso+'T00:00:00Z')).getUTCDay(); // 0=Sun..6=Sat
    return addDays(iso, dow === 0 ? -6 : 1 - dow);
  }
  function startOfMonth(iso){ return iso.slice(0,8) + '01'; }
  function endOfMonth(iso){
    var y = +iso.slice(0,4), m = +iso.slice(5,7);
    return new Date(Date.UTC(y, m, 0)).toISOString().slice(0,10);   // 0th of next = last of this
  }

  function presetRange(name){
    var t = nyToday();
    if(name === 'today')      return {from:t, to:t};
    if(name === 'this-week')  return {from:startOfWeek(t), to:t};
    if(name === 'last-week'){
      var thisMon = startOfWeek(t);
      return {from:addDays(thisMon, -7), to:addDays(thisMon, -1)};
    }
    if(name === 'this-month') return {from:startOfMonth(t), to:t};
    if(name === 'last-month'){
      var lastPrev = addDays(startOfMonth(t), -1);
      return {from:startOfMonth(lastPrev), to:lastPrev};
    }
    if(name === 'last-30')    return {from:addDays(t, -29), to:t};   // 30 days inclusive
    if(name === 'ytd')        return {from:t.slice(0,4)+'-01-01', to:t};
    /* all */                 return {from:null, to:null};
  }

  function applyPreset(name){
    var r = presetRange(name);
    filterState.from = r.from;
    filterState.to = r.to;
    activePreset = name;
    syncDateInputs(); syncChipActive(); reanchorToFilter();
    rerender();
  }
  function syncDateInputs(){
    if(!$sel('fFrom')) return;     // not yet wired (early boot)
    $sel('fFrom').value = filterState.from || '';
    $sel('fTo').value = filterState.to || '';
  }
  function syncChipActive(){
    // Only the date-preset chips toggle here. The calendar width buttons
    // (data-cols) and the calendar arrows (calPrev/calNext) manage their own
    // state — scoping to [data-preset] keeps this from resetting them.
    var chips = document.querySelectorAll('.chip[data-preset]');
    for(var i=0;i<chips.length;i++){
      chips[i].className = (chips[i].getAttribute('data-preset') === activePreset) ? 'chip active' : 'chip';
    }
  }
  function clearActivePreset(){
    if(activePreset !== null){ activePreset = null; syncChipActive(); }
  }
  // ← / → step the active window by its own width. No-op without a window.
  function shiftRange(dir){
    if(!filterState.from || !filterState.to) return;
    var d0 = Date.parse(filterState.from+'T00:00:00Z');
    var d1 = Date.parse(filterState.to+'T00:00:00Z');
    var width = Math.round((d1-d0)/86400000) + 1;
    filterState.from = addDays(filterState.from, dir*width);
    filterState.to   = addDays(filterState.to,   dir*width);
    clearActivePreset(); syncDateInputs(); reanchorToFilter(); rerender();
  }
  function onDateInputChange(){
    filterState.from = $sel('fFrom').value || null;
    filterState.to   = $sel('fTo').value || null;
    // Disallow inverted range silently — clamp to/from to the other end.
    if(filterState.from && filterState.to && filterState.from > filterState.to){
      filterState.to = filterState.from;
      $sel('fTo').value = filterState.to;
    }
    clearActivePreset(); reanchorToFilter(); rerender();
  }

  // === analytics — ports of Python src/pivot.py _kpis / _max_drawdown /
  // _streaks / _scoring_rows. Python keeps source-of-truth unit tests; this
  // mirror runs filter-time recompute (cheap for n in thousands). Keep the
  // two in sync; divergence will manifest as the full-dataset JS vs Python
  // outputs differing on the same input. ===
  function computeMaxDD(closed, cum){
    if(!cum.length) return {amount:0,pct:null,days:0,peak_i:0,trough_i:0};
    var peak=cum[0], peak_i=0, best_peak_i=0, trough_i=0, worst=0;
    for(var i=0;i<cum.length;i++){
      if(cum[i]>peak){ peak=cum[i]; peak_i=i; }
      var drop = peak - cum[i];
      if(drop>worst){ worst=drop; trough_i=i; best_peak_i=peak_i; }
    }
    var peakEq = cum[best_peak_i];
    var pct = peakEq>0 ? worst/peakEq*100 : null;
    // CloseDate is YYYY-MM-DD; UTC parse keeps day arithmetic stable across DST.
    var d0 = Date.parse(closed[best_peak_i].CloseDate+'T00:00:00Z');
    var d1 = Date.parse(closed[trough_i].CloseDate+'T00:00:00Z');
    return { amount: worst, pct: pct, days: Math.round((d1-d0)/86400000),
             peak_i: best_peak_i, trough_i: trough_i };
  }

  function computeStreaks(closed){
    var maxW=0, maxL=0, curW=0, curL=0;
    closed.forEach(function(r){
      if(r.PnL_USD>0){ curW++; curL=0; } else { curL++; curW=0; }
      if(curW>maxW) maxW=curW; if(curL>maxL) maxL=curL;
    });
    return [maxW, maxL];
  }

  function computeKpis(records){
    var closed = records.filter(function(r){return r.PnL_USD!=null;});
    var n_closed = closed.length;
    var net = closed.reduce(function(s,r){return s+r.PnL_USD;},0);
    var commission = closed.reduce(function(s,r){return s+(r.Commission||0);},0);
    var gross = net - commission;
    var wins = closed.filter(function(r){return r.PnL_USD>0;});
    var losses = closed.filter(function(r){return r.PnL_USD<=0;});
    var gp = wins.reduce(function(s,r){return s+r.PnL_USD;},0);
    var gl = losses.reduce(function(s,r){return s+r.PnL_USD;},0);  // <=0
    var cum=[], running=0;
    closed.forEach(function(r){ running += r.PnL_USD; cum.push(running); });
    var streaks = computeStreaks(closed);
    return {
      n: records.length, n_closed: n_closed, net: net, gross: gross,
      commission: commission,
      win_rate: n_closed ? wins.length/n_closed*100 : 0,
      n_wins: wins.length, n_losses: losses.length,
      profit_factor: gl<0 ? gp/Math.abs(gl) : null,
      expectancy: n_closed ? net/n_closed : 0,
      avg_win: wins.length ? gp/wins.length : 0,
      avg_loss: losses.length ? gl/losses.length : 0,
      max_win_streak: streaks[0], max_loss_streak: streaks[1],
      cum: cum, closed: closed, dd: computeMaxDD(closed, cum)
    };
  }

  function computeScoring(records){
    var b = {};
    records.forEach(function(r){
      if(r.PnL_USD==null) return;
      var k = r.SetupCode;
      var x = b[k] || (b[k] = { code:k, name:r.Setup, n:0, net:0, gp:0, gl:0,
                                nw:0, nl:0, hw:0, hwn:0, hl:0, hln:0, intraday:0 });
      x.n++; x.net += r.PnL_USD;
      if(r.OpenDate === r.CloseDate) x.intraday++;
      if(r.PnL_USD>0){ x.nw++; x.gp += r.PnL_USD; x.hw += r.Hold_min; x.hwn++; }
      else { x.nl++; x.gl += r.PnL_USD; x.hl += r.Hold_min; x.hln++; }
    });
    var out = [];
    Object.keys(b).forEach(function(k){ var x=b[k]; out.push({
      code:x.code, name:x.name, n:x.n, net:x.net,
      win_rate: x.n ? x.nw/x.n*100 : 0,
      pf: x.gl<0 ? x.gp/Math.abs(x.gl) : null,
      expectancy: x.n ? x.net/x.n : 0,
      avg_win: x.nw ? x.gp/x.nw : 0,
      avg_loss: x.nl ? x.gl/x.nl : 0,
      hold_win: x.hwn ? x.hw/x.hwn : 0,
      hold_loss: x.hln ? x.hl/x.hln : 0,
      intraday_pct: x.n ? x.intraday/x.n*100 : 0
    }); });
    out.sort(function(a,b){ return b.net - a.net; });
    return out;
  }

  // === renderers — each is a pure (state -> DOM) function. The only place
  // they read filter state is via filtered(); they never poke the DOM-level
  // <select>s directly, so swapping the UI later is painless. ===
  // Weekday count Mon-Fri between two ISO dates inclusive (Tier-1 #4). Doesn't
  // model US holidays — ~10/yr -> small bias on 30-day windows, not worth a
  // holiday-calendar dependency in phase-1.
  function tradingDaysBetween(d0iso, d1iso){
    var d0 = Date.parse(d0iso+'T00:00:00Z'), d1 = Date.parse(d1iso+'T00:00:00Z');
    var count = 0;
    for(var t=d0; t<=d1; t+=86400000){
      var dow = new Date(t).getUTCDay();   // 0=Sun..6=Sat
      if(dow!==0 && dow!==6) count++;
    }
    return count;
  }

  function renderRangeBanner(rows){
    // When a date filter is active, the banner reports the FILTER WINDOW —
    // the user's "compass". Otherwise, fall back to the data's actual close
    // extent. (Phase-1 leak: with filter 05-01→05-11 and trades only thru
    // 05-08, the banner used to read "→ 05-08" hiding the empty tail.)
    var f, t, suffix = '';
    if(filterState.from && filterState.to){
      f = filterState.from; t = filterState.to;
      if(rows.length === 0) suffix = ' (no closes in this window)';
    } else {
      var closed = rows.filter(function(r){return r.PnL_USD!=null;});
      if(!closed.length){ $sel('rangeLabel').textContent = 'Range: — · 0 RT'; return; }
      var dates = closed.map(function(r){return r.CloseDate;}).sort();
      f = dates[0]; t = dates[dates.length-1];
    }
    var cal = Math.round((Date.parse(t+'T00:00:00Z')-Date.parse(f+'T00:00:00Z'))/86400000)+1;
    var trad = tradingDaysBetween(f, t);
    // "31 cal-days, ~22 trading" gives PF / Expectancy their statistical
    // context — 20 RT over 22 trading days reads very different from 20 over 200.
    $sel('rangeLabel').textContent = 'Range: ' + f + ' → ' + t +
      ' · ' + cal + ' cal-days, ~' + trad + ' trading · ' + rows.length + ' RT' + suffix;
  }

  // KPI in 3 visual tiers (Tier-1 #4): the headline number stands out, the
  // 5 next-level "edge" metrics share a row, accounting/streaks recede into
  // small cards. Avoids the phase-1 "everything looks equally important" trap.
  function renderKpiHeadline(k){
    var pf = k.profit_factor==null ? '∞' : k.profit_factor.toFixed(2);
    var dd = k.dd;
    var ddPct = dd.pct==null ? '' : ', ' + dd.pct.toFixed(1) + '%';
    var ddTxt = "<span class='neg'>" + signed(-dd.amount) + "</span><small>" + ddPct + ", " + dd.days + "d</small>";
    // Win/Loss ratio (avg win / |avg loss|) — distinct from R-multiple (which
    // needs initial stops we don't have). Shows whether the edge survives
    // even at <50% win rate. ∞ when no losses (small-sample artifact).
    var rr = (k.avg_loss < 0) ? (Math.abs(k.avg_win) / Math.abs(k.avg_loss)) : null;
    var rrTxt = (k.avg_win <= 0 && k.avg_loss === 0) ? '—' : (rr == null ? '∞' : rr.toFixed(2));

    function card(label, value){
      return "<div class='card'><div class='k'>" + label + "</div><div class='v'>" + value + "</div></div>";
    }
    function cardSection(cls_, items){
      return "<div class='" + cls_ + "'>" + items.map(function(c){return card(c[0],c[1]);}).join('') + "</div>";
    }

    // Headline: just the dollars. Big.
    var headline = "<div class='cards-headline'><div class='card-xl'>" +
      "<div class='k'>Net P&L</div>" +
      "<div class='v " + cls(k.net) + "'>" + signed(k.net) + "</div></div></div>";

    // Primary edge metrics — the ones a trader actually decides off of.
    var primary = cardSection('cards-primary', [
      ['Win rate', k.win_rate.toFixed(0) + "% <small>(" + k.n_wins + "/" + k.n_closed + ")</small>"],
      ['Profit factor', pf],
      ['Win/Loss ratio', rrTxt + " <small>:1</small>"],
      ['Expectancy', "<span class='" + cls(k.expectancy) + "'>" + signed(k.expectancy) + "</span>"],
      ['Max drawdown', ddTxt]
    ]);

    // Tier-1 #6: streak cells append "all-time: N" when filter narrows the
    // result, so the user always has the absolute reference visible. When no
    // filter (current==lifetime) the lifetime line collapses — no clutter.
    function streakCell(cur, lt, glyph, klass){
      var main = "<span class='"+klass+"'>"+cur+" "+glyph+"</span>";
      var showLt = (LIFETIME && lt != null && lt !== cur);
      return showLt ? main + " <small>all-time: "+lt+" "+glyph+"</small>" : main;
    }
    var ltWin = LIFETIME ? LIFETIME.max_win_streak : null;
    var ltLoss = LIFETIME ? LIFETIME.max_loss_streak : null;

    // Secondary — accounting + activity + streaks. Smaller, still glanceable.
    var secondary = cardSection('cards-secondary', [
      ['Round-trips', String(k.n)],
      ['Gross P&L', "<span class='" + cls(k.gross) + "'>" + signed(k.gross) + "</span>"],
      ['Commissions', "<span class='neg'>" + signed(k.commission) + "</span>"],
      ['Avg win', "<span class='pos'>" + signed(k.avg_win) + "</span>"],
      ['Avg loss', "<span class='neg'>" + signed(k.avg_loss) + "</span>"],
      ['Max win streak', streakCell(k.max_win_streak, ltWin, '▲', 'pos')],
      ['Max loss streak', streakCell(k.max_loss_streak, ltLoss, '▼', 'neg')]
    ]);

    $sel('kpiHeadline').innerHTML =
      "<div class='kpi-grid'>" + headline +
      "<div class='kpi-right'>" + primary + secondary + "</div>" +
      "</div>";
  }

  function renderEquityCurve(k){
    var cum = k.cum, closed = k.closed;
    if(!cum.length){ $sel('equityCurve').innerHTML = "<p class='muted'>No closed round-trips to plot.</p>"; return; }
    var w=1180, h=300, padx=56, pady=22, iw=w-2*padx, ih=h-2*pady, n=cum.length;
    var lo = Math.min(0, Math.min.apply(null, cum));
    var hi = Math.max(0, Math.max.apply(null, cum));
    var span = (hi - lo) || 1;
    function X(i){ return padx + (n>1 ? iw*i/(n-1) : iw/2); }
    function Y(v){ return pady + ih*(1 - (v-lo)/span); }
    var poly = cum.map(function(v,i){ return X(i).toFixed(1)+','+Y(v).toFixed(1); }).join(' ');
    var zeroY = Y(0), final = cum[n-1];
    var color = final>=0 ? POS : NEG;
    var dd = k.dd, band = '';
    if(dd.amount>0 && dd.trough_i>dd.peak_i){
      var x0 = X(dd.peak_i), x1 = X(dd.trough_i);
      band = "<rect x='"+x0.toFixed(1)+"' y='"+pady+"' width='"+(x1-x0).toFixed(1)+"' height='"+ih+"' fill='"+NEG+"' opacity='0.10'/>"+
        "<circle cx='"+X(dd.peak_i).toFixed(1)+"' cy='"+Y(cum[dd.peak_i]).toFixed(1)+"' r='3' fill='"+NEG+"'/>"+
        "<circle cx='"+X(dd.trough_i).toFixed(1)+"' cy='"+Y(cum[dd.trough_i]).toFixed(1)+"' r='3' fill='"+NEG+"'/>"+
        "<text x='"+((x0+x1)/2).toFixed(1)+"' y='"+(pady+12)+"' text-anchor='middle' font-size='10' fill='"+NEG+"'>max DD "+
        signed(-dd.amount)+" ("+closed[dd.peak_i].CloseDate+"→"+closed[dd.trough_i].CloseDate+")</text>";
    }
    // X-axis date ticks — pin gains/losses to time (MM-DD, matches calendar style)
    var numTicks = Math.min(6, n);
    var xLabels = '';
    for(var t=0; t<numTicks; t++){
      var idx = numTicks===1 ? 0 : Math.round(t*(n-1)/(numTicks-1));
      var d = closed[idx] && closed[idx].CloseDate;
      if(!d) continue;
      var mmdd = d.length>=10 ? d.slice(5) : d;
      var xPos = X(idx);
      xLabels += "<line x1='"+xPos.toFixed(1)+"' y1='"+(h-pady).toFixed(1)+"' x2='"+xPos.toFixed(1)+"' y2='"+(h-pady+4).toFixed(1)+"' stroke='#bbb'/>"+
        "<text x='"+xPos.toFixed(1)+"' y='"+(h-pady+15).toFixed(1)+"' text-anchor='middle' font-size='10' fill='#888'>"+esc(mmdd)+"</text>";
    }
    $sel('equityCurve').innerHTML =
      "<svg viewBox='0 0 "+w+" "+h+"' width='100%' style='max-width:"+w+"px'>"+
      "<rect x='0' y='0' width='"+w+"' height='"+h+"' fill='#fff' stroke='#eee'/>"+band+
      "<line x1='"+padx+"' y1='"+zeroY.toFixed(1)+"' x2='"+(w-padx)+"' y2='"+zeroY.toFixed(1)+"' stroke='#bbb' stroke-dasharray='4 3'/>"+
      "<text x='"+(padx-6)+"' y='"+(zeroY+4).toFixed(1)+"' text-anchor='end' font-size='10' fill='#888'>0</text>"+
      "<text x='"+(padx-6)+"' y='"+(Y(hi)+4).toFixed(1)+"' text-anchor='end' font-size='10' fill='#888'>"+hi.toFixed(0)+"</text>"+
      "<text x='"+(padx-6)+"' y='"+(Y(lo)+4).toFixed(1)+"' text-anchor='end' font-size='10' fill='#888'>"+lo.toFixed(0)+"</text>"+
      xLabels+
      "<polyline points='"+poly+"' fill='none' stroke='"+color+"' stroke-width='2'/>"+
      "<text x='"+(w-padx)+"' y='"+(Y(final)-6).toFixed(1)+"' text-anchor='end' font-size='11' fill='"+color+"' font-weight='bold'>"+signed(final)+"</text>"+
      "</svg>";
  }

  function renderScoringTable(rows){
    if(!rows.length){ $sel('scoringTable').innerHTML = "<p class='muted'>No closed round-trips.</p>"; return; }
    var body = rows.map(function(r){
      var pf = r.pf==null ? '∞' : r.pf.toFixed(2);
      var flag = (r.hold_loss > r.hold_win && r.hold_win > 0) ? ' ⚠' : '';
      return "<tr><td>"+esc(r.name)+"</td><td>"+r.n+"</td>"+
        "<td class='"+cls(r.net)+"'>"+signed(r.net)+"</td>"+
        "<td>"+r.win_rate.toFixed(0)+"%</td><td>"+pf+"</td>"+
        "<td class='"+cls(r.expectancy)+"'>"+signed(r.expectancy)+"</td>"+
        "<td class='pos'>"+signed(r.avg_win)+"</td>"+
        "<td class='neg'>"+signed(r.avg_loss)+"</td>"+
        "<td>"+r.hold_win.toFixed(0)+"m</td>"+
        "<td>"+r.hold_loss.toFixed(0)+"m"+flag+"</td>"+
        "<td>"+r.intraday_pct.toFixed(0)+"%</td></tr>";
    }).join('');
    $sel('scoringTable').innerHTML =
      "<table class='grid'><thead><tr>"+
      "<th>Setup</th><th>#</th><th>Net P&amp;L</th><th>Win%</th><th>PF</th>"+
      "<th>Expectancy</th><th>Avg win</th><th>Avg loss</th>"+
      "<th title='avg hold of winning round-trips'>Hold (win)</th>"+
      "<th title='avg hold of losing round-trips'>Hold (loss)</th>"+
      "<th title='share opened+closed same day'>Intraday%</th>"+
      "</tr></thead><tbody>"+body+"</tbody></table>"+
      "<div class='note'>⚠ = losers held longer than winners (possible \"cut winners, ride losers\").</div>";
  }

  // === calendar heatmap, grouped by close date ===
  var MON=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  function byDay(rows){
    var m={}; rows.forEach(function(r){ var d=r.CloseDate; if(!d)return;
      (m[d]=m[d]||{net:0,n:0,w:0}); m[d].net+=pnl(r); m[d].n++; if(r.Result==='Win')m[d].w++; });
    return m;
  }
  function shade(net,maxAbs){
    if(!net||!maxAbs) return '#fff';
    var a=0.12+0.78*Math.min(1,Math.abs(net)/maxAbs);
    var c=net>0?POS:NEG; // hex -> rgba
    var n=parseInt(c.slice(1),16);
    return 'rgba('+((n>>16)&255)+','+((n>>8)&255)+','+(n&255)+','+a.toFixed(2)+')';
  }
  // === FR-PIVOT-8: calendar windowed viewport ===
  // The calendar is decoupled from the DATE filter (it's a navigator, not a
  // scope): cells show real P&L for the viewport's months under the SETUP/CLASS
  // filter only. The date filter shows as the in-range overlay. JS mirrors of
  // the Python source-of-truth _calendar_window / _resolve_anchor.
  function filteredNoDate(){
    var s=filterState;
    return DATA.filter(function(r){
      if(s.setup && r.Setup !== s.setup) return false;
      if(s["class"] && r.Class !== s["class"]) return false;
      return true;
    });
  }
  function monthExtent(rows){
    var ds=rows.map(function(r){return r.CloseDate;}).filter(Boolean);
    if(!ds.length) return {min:null, max:null};
    ds.sort();
    return {min:ds[0].slice(0,7), max:ds[ds.length-1].slice(0,7)};
  }
  function monthIdx(ym){ return (+ym.slice(0,4))*12 + (+ym.slice(5,7)-1); }
  function idxMonth(i){ var y=Math.floor(i/12), m=i%12+1; return y+'-'+(m<10?'0'+m:''+m); }
  function calWindow(anchor, cols, min, max){
    if(!anchor||!min||!max) return {months:[], hasPrev:false, hasNext:false};
    var lo=monthIdx(min), hi=monthIdx(max);
    var a=Math.max(lo, Math.min(hi, monthIdx(anchor)));
    var months=[]; for(var i=0;i<cols;i++) months.push(idxMonth(a-(cols-1)+i));
    return {months:months, hasPrev:a>lo, hasNext:a<hi};
  }
  function resolveAnchor(cur, to, cols, min, max){
    if(!max) return null;
    if(!to) return cur || max;                       // unbounded "All" -> stay
    var lo=monthIdx(min), hi=monthIdx(max);
    var target=Math.max(lo, Math.min(hi, monthIdx(to)));
    if(cur && calWindow(cur, cols, min, max).months.indexOf(idxMonth(target))>=0) return cur;
    return idxMonth(target);
  }
  // Gentle re-anchor on any explicit date-filter change (chip/drag/title/typed/shift).
  function reanchorToFilter(){
    var ext=monthExtent(filteredNoDate());
    calAnchor=resolveAnchor(calAnchor, filterState.to, calCols, ext.min, ext.max);
  }
  function setCalArrows(prev, next){
    var p=$sel('calPrev'), n=$sel('calNext');
    if(p) p.disabled=!prev;
    if(n) n.disabled=!next;
  }
  // View-only paging: move the viewport, never touch the filter or the stats.
  function pageCal(dir){
    var ext=monthExtent(filteredNoDate());
    if(!ext.max || !calAnchor) return;
    var lo=monthIdx(ext.min), hi=monthIdx(ext.max);
    calAnchor=idxMonth(Math.max(lo, Math.min(hi, monthIdx(calAnchor)+dir*calCols)));
    renderCal();
  }
  function renderCal(){
    var rows=filteredNoDate(), m=byDay(rows);   // FR-PIVOT-8: setup/class only, NOT date
    var wrap=$sel('calendar');
    if(!rows.length){ wrap.innerHTML="<p class='muted'>No round-trips for this filter.</p>"; $sel('drill').innerHTML=''; setCalArrows(false,false); return; }
    var ext=monthExtent(rows);
    // Clamp the anchor into the (possibly setup/class-narrowed) data extent.
    if(!calAnchor || calAnchor>ext.max) calAnchor=ext.max;
    if(calAnchor<ext.min) calAnchor=ext.min;
    var win=calWindow(calAnchor, calCols, ext.min, ext.max);
    // maxAbs + Saturday guard scoped to the VISIBLE viewport days only.
    var visDays=Object.keys(m).filter(function(d){ return win.months.indexOf(d.slice(0,7))>=0; });
    var maxAbs=visDays.length ? Math.max.apply(null,visDays.map(function(d){return Math.abs(m[d].net);})) : 0;
    var satDays = visDays.filter(function(d){ return new Date(d+'T00:00:00').getDay() === 6; });
    var satNote = satDays.length ? ("<div class='note' style='color:#c2792e'>⚠ " +
      satDays.length + " Saturday trade-date(s) in view — Saturday column is hidden in the calendar; see the detail table for these.</div>") : '';
    var html=satNote + '<div class="cal-wrap" data-cols="'+calCols+'">';
    win.months.forEach(function(ym){ html+=monthGrid(+ym.slice(0,4), +ym.slice(5,7)-1, m, maxAbs); });
    html+='</div>';
    wrap.innerHTML=html;
    setCalArrows(win.hasPrev, win.hasNext);
    // Tier-1 #5: month title click filters to that whole month.
    var titles = wrap.querySelectorAll('.cal h3');
    for(var i=0;i<titles.length;i++){
      (function(h3){
        h3.onclick = function(){
          var iso = h3.getAttribute('data-mo');
          filterState.from = iso; filterState.to = endOfMonth(iso);
          clearActivePreset(); syncDateInputs(); reanchorToFilter(); rerender();
        };
      })(titles[i]);
    }
    // Tier-1 #5: drag-select on day cells. mousedown captures start; mouseup
    // on same cell = click (drill, the phase-1 behavior); mouseup on a
    // different cell = range filter. Browser does no native drag UI here, but
    // the in-range outline appears on rerender so the user sees the result.
    var cells = wrap.querySelectorAll('td.day');
    for(var j=0;j<cells.length;j++){
      (function(td){
        td.onmousedown = function(e){
          if(e.button !== 0) return;
          e.preventDefault();
          dragStart = td.getAttribute('data-d');
        };
        td.onmouseup = function(){
          if(dragStart === null) return;
          var start = dragStart, end = td.getAttribute('data-d');
          dragStart = null;
          if(start === end){
            // Click = drill (phase-1 behavior preserved).
            var sels = wrap.querySelectorAll('td.sel');
            for(var k=0;k<sels.length;k++) sels[k].classList.remove('sel');
            td.classList.add('sel');
            drill(end, rows);
          } else {
            var f = start < end ? start : end;
            var t = start < end ? end   : start;
            filterState.from = f; filterState.to = t;
            clearActivePreset(); syncDateInputs(); reanchorToFilter(); rerender();
          }
        };
      })(cells[j]);
    }
    // Mouseleave anywhere on the calendar aborts an in-progress drag — avoids
    // a stuck dragStart if user releases outside a day cell.
    wrap.onmouseleave = function(){ dragStart = null; };
  }
  function monthGrid(Y,M,m,maxAbs){
    // 6-col grid: Mon Tue Wed Thu Fri Sun (Saturday dropped — CME closed Sat,
    // and user confirmed). Sunday kept because CME index futures (ES/NQ/MES/
    // MNQ) reopen Sun 18:00 ET, and IB stamps trade_date with the calendar
    // date — Sun-evening trades would land in this Sun column.
    // Weekday code (Mon=0..Sun=6); colFor maps it to the visible col 0..5,
    // returning -1 for Saturday (skipped from rendering).
    function colFor(wd){ return wd === 5 ? -1 : (wd === 6 ? 5 : wd); }
    var dim = new Date(Y,M+1,0).getDate();
    var monthIso = Y+'-'+String(M+1).padStart(2,'0')+'-01';
    var h = '<div class="cal"><h3 data-mo="'+monthIso+'" title="click to filter to this month">'+
      MON[M]+' '+Y+'</h3><table><tr>'+
      ['Mon','Tue','Wed','Thu','Fri','Sun'].map(function(d){return '<th>'+d+'</th>';}).join('')+
      '</tr><tr>';
    var placed = 0;
    for(var d=1; d<=dim; d++){
      var wd = (new Date(Y,M,d).getDay()+6)%7;     // Mon=0..Sun=6
      if(wd === 5) continue;                        // skip Saturday entirely
      var col = colFor(wd);
      if(placed === 0){
        // pad the leading empty cells before the month's first non-Sat day
        for(var i=0; i<col; i++) h += '<td></td>';
      } else if(col === 0){
        // wrap at Monday (start of next visible week)
        h += '</tr><tr>';
      }
      var ds = Y+'-'+String(M+1).padStart(2,'0')+'-'+String(d).padStart(2,'0');
      var inRange = (filterState.from && filterState.to && ds >= filterState.from && ds <= filterState.to);
      var rngCls = inRange ? ' in-range' : '';
      var cell = m[ds];
      if(cell){ var wr = Math.round(cell.w/cell.n*100);
        h += '<td class="day'+rngCls+'" data-d="'+ds+'" style="background:'+shade(cell.net,maxAbs)+'">'+
             '<div class="dn">'+d+'</div><div class="dp">'+fmt(cell.net)+'</div>'+
             '<div class="dc">'+cell.n+'t · '+wr+'%</div></td>';
      } else {
        // Non-trade days also get in-range highlight if covered, so the
        // active window reads as a contiguous span on the calendar.
        h += '<td class="'+(inRange?'in-range':'')+'"><div class="dn">'+d+'</div></td>';
      }
      placed++;
    }
    return h+'</tr></table></div>';
  }
  function drill(date,rows){
    var day=rows.filter(function(r){return r.CloseDate===date;})
               .sort(function(a,b){return (a.CloseTime>b.CloseTime)?1:-1;});
    var net=day.reduce(function(s,r){return s+pnl(r);},0);
    var h='<h3>'+date+' — '+day.length+' round-trips · <span class="'+(net>=0?'pos':'neg')+'">'+fmt(net)+'</span></h3>';
    h+='<table class="grid"><thead><tr><th>Close</th><th>Underlying</th><th>Dir</th><th>Setup</th>'+
       '<th>Qty</th><th>Hold</th><th>P&amp;L</th><th>Score</th><th>Notes</th></tr></thead><tbody>';
    day.forEach(function(r){ h+='<tr><td>'+esc(r.CloseTime)+'</td><td>'+esc(r.Underlying)+'</td>'+
      '<td>'+esc(r.Direction)+'</td><td>'+esc(r.Setup)+'</td><td>'+r.Qty+'</td>'+
      '<td>'+r.Hold_min+'m</td><td class="'+(pnl(r)>=0?'pos':'neg')+'">'+fmt(pnl(r))+'</td>'+
      '<td>'+esc(r.Score)+'</td><td>'+esc(r.Notes)+'</td></tr>'; });
    $sel('drill').innerHTML=h+'</tbody></table>';
  }

  // --- detail table (sortable + substring filter, Tier-1 #3) ---
  // Close-first column order: user thinks of close as the canonical trade
  // event (P&L realized there). Open Date/Time sit next to it as supporting
  // context. EntryHour dropped from detail — redundant with OpenTime's HH
  // prefix; still available as a pivot dimension.
  var COLS=[
    ['CloseDate','Close date'],['CloseTime','Close time'],
    ['OpenDate','Open date'],['OpenTime','Open time'],
    ['Setup','Setup'],['Class','Class'],['Underlying','Sym'],
    ['Direction','Dir'],['Result','Result'],['Session','Session'],
    ['EntryDOW','DOW'],['HoldBucket','Hold'],['Qty','Qty'],
    ['PnL_USD','P&L'],['Hold_min','Hold(m)'],['Score','Score'],['Notes','Notes']
  ];
  // -1 = descending: newest close on top. Trader scans recent activity first.
  var sortKey='CloseDate', sortDir=-1, filterStr='';
  function detailRows(){
    var rows=filtered();
    if(filterStr){ var q=filterStr.toLowerCase();
      rows=rows.filter(function(r){ return COLS.some(function(c){return String(r[c[0]]==null?'':r[c[0]]).toLowerCase().indexOf(q)>=0;}); }); }
    rows.sort(function(a,b){ var x=a[sortKey],y=b[sortKey];
      if(x==null)x=''; if(y==null)y=''; return (x<y?-1:x>y?1:0)*sortDir; });
    return rows;
  }
  function renderDetail(){
    var rows=detailRows();
    // Row-idx col: position in current view (re-numbered on sort/filter).
    // Total count shown next to filter input — "N of M" while filtering so
    // user sees both visible & full population without scrolling to bottom.
    var totalN = filtered().length;
    var cnt = $sel('detailCount');
    if(cnt){ cnt.textContent = filterStr ? (rows.length + " of " + totalN + " rows") : (rows.length + " rows"); }
    // .virt = browser-native virtualization via content-visibility (Tier-1 #3).
    // Browser short-circuits layout/paint for off-screen rows; scales to ~50k
    // rows without JS work. Combined with sticky thead in the scroll wrapper.
    var h='<table class="grid sortable virt"><thead><tr><th class="row-idx">#</th>'+COLS.map(function(c){
      var arr=c[0]===sortKey?(sortDir>0?' ▲':' ▼'):''; return '<th data-k="'+c[0]+'">'+c[1]+arr+'</th>'; }).join('')+'</tr></thead><tbody>';
    rows.forEach(function(r,i){ h+='<tr><td class="row-idx">'+(i+1)+'</td>'+COLS.map(function(c){
      var v=r[c[0]], cell;
      if(c[0]==='PnL_USD') cell='<td class="'+(pnl(r)>=0?'pos':'neg')+'">'+fmt(pnl(r))+'</td>';
      else cell='<td>'+esc(v)+'</td>';
      return cell; }).join('')+'</tr>'; });
    h+='</tbody></table>';
    var el=$sel('detail'); el.innerHTML=h;
    // Only sortable headers (those with data-k) wire onclick — # column skipped.
    Array.prototype.forEach.call(el.querySelectorAll('th[data-k]'),function(th){
      th.onclick=function(){ var k=th.getAttribute('data-k');
        if(k===sortKey) sortDir=-sortDir; else {sortKey=k; sortDir=1;} renderDetail(); };
    });
  }

  // --- CSV export (Tier-2): download exactly the rows currently visible in
  // the detail table — filter + substring + sort all applied (WYSIWYG). Logic
  // mirror of Python _detail_csv (the unit-tested source of truth). RFC-4180
  // quoting + UTF-8 BOM (﻿, so Excel reads CJK notes), CRLF terminators.
  // Emits raw underlying values, NOT the display-formatted P&L — a spreadsheet
  // wants the number 1234.5, not "+$1,234.50".
  function csvField(v){
    var s = (v==null) ? '' : String(v);
    return /[",\r\n]/.test(s) ? '"'+s.replace(/"/g,'""')+'"' : s;
  }
  function toCSV(rows){
    var head = COLS.map(function(c){ return csvField(c[1]); }).join(',');
    var body = rows.map(function(r){
      return COLS.map(function(c){ return csvField(r[c[0]]); }).join(',');
    });
    return '﻿' + [head].concat(body).join('\r\n') + '\r\n';
  }
  function exportDetailCsv(){
    var rows = detailRows();
    var name = (filterState.from && filterState.to)
      ? 'traderlens_'+filterState.from+'_'+filterState.to+'.csv'
      : 'traderlens_all.csv';
    var blob = new Blob([toCSV(rows)], {type:'text/csv;charset=utf-8'});
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url; a.download = name;
    document.body.appendChild(a); a.click();
    document.body.removeChild(a); URL.revokeObjectURL(url);
  }

  // --- pivot (PivotTable.js) — only the FR-PIVOT-4.4 dims draggable ---
  function initPivot(){
    var hidden=Object.keys(DATA[0]||{}).filter(function(k){return CFG.dims.indexOf(k)<0;});
    // Tier-1 #7: default rows = EntryHour. Per the spec, hour-of-day is the
    // single strongest signal dim for an intraday scalper ("did 9am or 14pm
    // entries do better?"). User can still drag any of the FR-PIVOT-4.4 dims
    // at will; this is the starting view, not a lock.
    window.jQuery('#pivot').pivotUI(DATA,{
      rows:['EntryHour'], cols:['Result'], vals:['PnL_USD'],
      aggregatorName:'Sum', rendererName:'Table', hiddenAttributes:hidden });
  }

  // === Full-linkage rerender: single entrypoint, every visible section refreshes ===
  function rerender(){
    var rows = filtered();
    var k = computeKpis(rows);
    var scoring = computeScoring(rows);
    renderRangeBanner(rows);
    renderKpiHeadline(k);
    renderEquityCurve(k);
    renderScoringTable(scoring);
    renderCal();           // reads filtered() internally
    renderDetail();        // reads filtered() internally
    // PivotTable.js holds its own DOM — for Tier-1 #1 we still init it once
    // with full DATA + hidden non-dim attrs. Filter-aware pivot is a separate
    // ticket: PT.js doesn't expose a clean "swap data" API, so we'll re-init
    // it on filter change later (Tier-1 #5 / #7) when the UX requires it.
  }

  function boot(){
    LIFETIME = computeKpis(DATA);   // Tier-1 #6 — full-DATA baseline, never re-filtered.
    calAnchor = monthExtent(filteredNoDate()).max;   // FR-PIVOT-8: default = most-recent data month
    // Wire CSV export before the empty-DATA bail-out so the button is live even
    // on an empty archive (it just downloads a header-only file — no crash).
    $sel('detailExport').onclick = exportDetailCsv;
    if(!DATA.length){
      $sel('calendar').innerHTML="<p class='muted'>No round-trips.</p>";
      syncChipActive();  // mark "All" active even when empty
      rerender();
      return;
    }
    fillSel('fSetup','setups','Setup');
    fillSel('fClass','classes','Class');
    $sel('fSetup').onchange = function(e){ filterState.setup = e.target.value; rerender(); };
    $sel('fClass').onchange = function(e){ filterState["class"] = e.target.value; rerender(); };
    $sel('fReset').onclick = function(){
      filterState.setup=''; filterState["class"]='';
      $sel('fSetup').value=''; $sel('fClass').value='';
      applyPreset('all');  // clears from/to, lights the "All" chip, rerenders
    };
    $sel('detailFilter').oninput = function(e){ filterStr = e.target.value; renderDetail(); };

    // Tier-1 #5 wiring: chips, from/to inputs, ← →.
    var chips = document.querySelectorAll('.chip');
    for(var i=0;i<chips.length;i++){
      (function(b){ b.onclick = function(){ applyPreset(b.getAttribute('data-preset')); }; })(chips[i]);
    }
    $sel('fFrom').onchange = onDateInputChange;
    $sel('fTo').onchange   = onDateInputChange;
    $sel('rangePrev').onclick = function(){ shiftRange(-1); };
    $sel('rangeNext').onclick = function(){ shiftRange( 1); };

    // FR-PIVOT-8: calendar viewport arrows — view-only paging by `calCols`
    // months. They move the displayed window without touching the date filter
    // (distinct from the filter-bar ← → above, which shift the filter window).
    if($sel('calPrev')) $sel('calPrev').onclick = function(){ pageCal(-1); };
    if($sel('calNext')) $sel('calNext').onclick = function(){ pageCal( 1); };

    // Calendar months-per-row picker (1/2/3/4) — re-renders just the calendar.
    var colPicker = $sel('calColsPicker');
    if(colPicker){
      var colBtns = colPicker.querySelectorAll('.chip');
      for(var ci=0; ci<colBtns.length; ci++){
        (function(b){
          b.onclick = function(){
            calCols = +b.getAttribute('data-cols');
            for(var k=0; k<colBtns.length; k++){
              colBtns[k].className = (colBtns[k] === b) ? 'chip active' : 'chip';
            }
            renderCal();
          };
        })(colBtns[ci]);
      }
    }

    syncChipActive();  // light "All" initially
    rerender();
    initPivot();
  }
  if(window.jQuery){ window.jQuery(boot); } else { document.addEventListener('DOMContentLoaded',boot); }
})();
"""


# --- colors (FR-PIVOT-7): neutral, cross-cultural, colorblind-safe. NOT red/green
# (red=gain in parts of Asia vs green=gain in the US is contradictory + emotive).
# Blue = up/profit, amber = down/loss; always paired with +/- and up/down glyphs.
POS = "#2b6cb0"   # blue  — profit / up
NEG = "#c2792e"   # amber — loss / down
NEUTRAL = "#94a3b8"

# Dimensions surfaced as draggable pivot fields (FR-PIVOT-4.4); everything else
# in a record is detail-only and hidden from the pivot field selector.
_PIVOT_DIMS = ("Setup", "Class", "Underlying", "Direction", "Result", "Session",
               "EntryHour", "EntryDOW", "HoldBucket", "Month", "Week",
               "Qty", "Hold_min", "Score", "Commission", "PnL_USD")


# --- round-trip -> enriched record (drives pivot + calendar + detail in JS) ---

def _record(rt: RoundTrip, tag_code: str, tag_name: str,
            ann: annotations.Annotation | None) -> dict:
    # FR-PIVOT-10: derive R from the entry's optional planned_stop. None stop /
    # invalid stop -> R None (the no-R subset); StopStatus carries the reason so
    # the JS can tally invalid stops into a warning.
    rinfo = r_multiple.r_for_round_trip(rt, ann.planned_stop_value if ann else None)
    return {
        "open_trade_id": rt.open_trade_id,
        "Setup": tag_name,
        "SetupCode": tag_code,
        "Score": ann.score_value if ann else None,
        "Notes": ann.notes if ann else "",
        "Class": rt.trade_class,
        "Underlying": rt.underlying,
        "Direction": rt.direction,
        "Result": "Win" if rt.is_win else "Loss",
        "Session": rt.session,
        "EntryHour": rt.entry_hour,
        "EntryDOW": rt.entry_dow,
        "HoldBucket": rt.hold_bucket,
        "Month": rt.month,
        "Week": rt.week,
        "Expiry": rt.expiry or "",
        "Qty": rt.quantity,
        "PnL_USD": rt.pnl_usd,
        "PnL_pts": rt.pnl_pts,
        "Hold_min": rt.hold_minutes,
        "Commission": round(rt.commission, 2),
        "OpenDate": rt.open_date,
        "OpenTime": rt.open_time,
        "CloseDate": rt.close_date,
        "CloseTime": rt.close_time,
        "OpenPx": rt.open_price,
        "ClosePx": rt.close_price,
        # FR-PIVOT-10 R-multiple. R kept at 4dp (kills JSON float-noise, keeps
        # aggregate precision); display rounds to 2dp. RealizedRisk at cents.
        "R": round(rinfo.r, 4) if rinfo.r is not None else None,
        "RealizedRisk": round(rinfo.realized_risk, 2) if rinfo.realized_risk is not None else None,
        "HasR": rinfo.has_r,
        "StopStatus": rinfo.status,
        "PlannedStop": ann.planned_stop_value if ann else None,
    }


# --- detail-table CSV export (Tier-2) ---
# Columns surfaced in the browser "Download CSV" — a logic mirror of the JS
# `COLS` array in _APP_JS (keep the two in sync). (record-key, header label).
# Same set/order the detail table shows on screen.
_DETAIL_COLS = (
    ("CloseDate", "Close date"), ("CloseTime", "Close time"),
    ("OpenDate", "Open date"), ("OpenTime", "Open time"),
    ("Setup", "Setup"), ("Class", "Class"), ("Underlying", "Sym"),
    ("Direction", "Dir"), ("Result", "Result"), ("Session", "Session"),
    ("EntryDOW", "DOW"), ("HoldBucket", "Hold"), ("Qty", "Qty"),
    ("PnL_USD", "P&L"), ("Hold_min", "Hold(m)"), ("Score", "Score"),
    ("Notes", "Notes"),
)


def _detail_csv(records: list[dict]) -> str:
    """Serialize detail records to a CSV string for the in-browser download.

    Source-of-truth mirror of the JS `toCSV` (unit-tested here; the JS side is
    verified visually). Header = the labels in `_DETAIL_COLS`, one row per
    record in the given order. RFC-4180 quoting (stdlib csv handles `,` `"`
    newlines), CRLF terminators, and a leading UTF-8 BOM so Excel reads CJK
    notes without mojibake. None / missing values become empty cells. Raw
    underlying values are emitted (numbers stay numeric) — a spreadsheet wants
    `1234.5`, not the display-formatted `+$1,234.50`."""
    buf = io.StringIO()
    w = csv.writer(buf)  # defaults: CRLF terminator + QUOTE_MINIMAL (RFC-4180)
    w.writerow([label for _, label in _DETAIL_COLS])
    for r in records:
        w.writerow(["" if r.get(k) is None else r.get(k) for k, _ in _DETAIL_COLS])
    return "﻿" + buf.getvalue()


# --- calendar viewport windowing (Tier-2, FR-PIVOT-8) ---
# Pure month-arithmetic source of truth for the calendar's bounded viewport; the
# JS `calWindow` / `resolveAnchor` mirror these (verified visually). Months are
# "YYYY-MM" strings. Kept here (not just in JS) so the windowing + gentle
# re-anchor rules are unit-tested independently of the DOM.

def _month_index(ym: str) -> int:
    """'YYYY-MM' (or any 'YYYY-MM…' prefix) -> absolute month index for math."""
    return int(ym[:4]) * 12 + (int(ym[5:7]) - 1)


def _index_month(idx: int) -> str:
    """Inverse of _month_index -> 'YYYY-MM'."""
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def _calendar_window(anchor: str | None, cols: int,
                     min_month: str | None, max_month: str | None) -> dict:
    """The N-month viewport ending at `anchor` (FR-PIVOT-8).

    Returns the 'YYYY-MM' months to render (oldest..newest, length == cols) plus
    whether the older/newer paging arrows are live. The anchor is clamped into
    the data extent; the window may include leading/trailing empty months when
    the data is shorter than `cols`. Empty extent -> empty window, no arrows."""
    if not anchor or not min_month or not max_month:
        return {"months": [], "has_prev": False, "has_next": False}
    lo, hi = _month_index(min_month), _month_index(max_month)
    a = max(lo, min(hi, _month_index(anchor)))           # clamp anchor into extent
    months = [_index_month(a - (cols - 1) + i) for i in range(cols)]
    return {"months": months, "has_prev": a > lo, "has_next": a < hi}


def _resolve_anchor(current: str | None, filter_to: str | None, cols: int,
                    min_month: str | None, max_month: str | None) -> str | None:
    """Gentle re-anchor rule (FR-PIVOT-8): on an explicit *bounded* date-filter
    set, move the viewport's right edge to the filter's last month ONLY if that
    month is not already visible; an unbounded filter (filter_to None / 'All')
    leaves the view put. The target is clamped to the data extent. Returns the
    new anchor 'YYYY-MM' (or None when there is no data)."""
    if not max_month:                                    # no data
        return None
    if not filter_to:                                    # unbounded "All" -> stay
        return current or max_month
    lo, hi = _month_index(min_month), _month_index(max_month)
    target = max(lo, min(hi, _month_index(filter_to)))
    if current:
        visible = _calendar_window(current, cols, min_month, max_month)["months"]
        if _index_month(target) in visible:
            return current                               # already on screen -> don't move
    return _index_month(target)


# --- KPI / drawdown / streak computation (Python; the headline + scoring) ---

def _max_drawdown(closed: list[RoundTrip], cum: list[float]) -> dict:
    """Largest peak->trough drop on the equity curve. amount + % of peak +
    duration in calendar days (peak close -> trough close)."""
    if not cum:
        return {"amount": 0.0, "pct": None, "days": 0, "peak_i": 0, "trough_i": 0}
    peak = cum[0]
    peak_i = trough_i = best_peak_i = 0
    worst = 0.0
    for i, v in enumerate(cum):
        if v > peak:
            peak, peak_i = v, i
        drop = peak - v
        if drop > worst:
            worst, trough_i, best_peak_i = drop, i, peak_i
    peak_equity = cum[best_peak_i]
    pct = (worst / peak_equity * 100) if peak_equity > 0 else None
    d0 = datetime.fromisoformat(closed[best_peak_i].close_date)
    d1 = datetime.fromisoformat(closed[trough_i].close_date)
    return {"amount": worst, "pct": pct, "days": (d1 - d0).days,
            "peak_i": best_peak_i, "trough_i": trough_i}


def _streaks(closed: list[RoundTrip]) -> tuple[int, int]:
    """Longest consecutive win / loss runs (in close order)."""
    max_w = max_l = cur_w = cur_l = 0
    for r in closed:
        if r.pnl_usd > 0:
            cur_w += 1; cur_l = 0
        else:
            cur_l += 1; cur_w = 0
        max_w, max_l = max(max_w, cur_w), max(max_l, cur_l)
    return max_w, max_l


def _kpis(rts: list[RoundTrip]) -> dict:
    closed = [r for r in rts if r.pnl_usd is not None]
    n_closed = len(closed)
    net = sum(r.pnl_usd for r in closed)
    commission = sum(r.commission for r in closed)        # signed (cost < 0)
    gross = net - commission                              # strip commission
    wins = [r for r in closed if r.pnl_usd > 0]
    losses = [r for r in closed if r.pnl_usd <= 0]
    gross_profit = sum(r.pnl_usd for r in wins)
    gross_loss = sum(r.pnl_usd for r in losses)           # <= 0
    win_rate = (len(wins) / n_closed * 100) if n_closed else 0.0
    pf = (gross_profit / abs(gross_loss)) if gross_loss < 0 else None  # None = no losses
    expectancy = (net / n_closed) if n_closed else 0.0
    cum, running = [], 0.0
    for r in closed:
        running += r.pnl_usd
        cum.append(running)
    max_w, max_l = _streaks(closed)
    return {
        "n": len(rts), "n_closed": n_closed, "net": net, "gross": gross,
        "commission": commission, "win_rate": win_rate,
        "n_wins": len(wins), "n_losses": len(losses),
        "profit_factor": pf, "expectancy": expectancy,
        "avg_win": (gross_profit / len(wins)) if wins else 0.0,
        "avg_loss": (gross_loss / len(losses)) if losses else 0.0,
        "max_win_streak": max_w, "max_loss_streak": max_l,
        "cum": cum, "dd": _max_drawdown(closed, cum),
    }


def _scoring_rows(rts: list[RoundTrip], tags: list[str], cfg: annotations.TagConfig) -> list[dict]:
    """Per-setup performance + execution row (FR-PIVOT-5). tags[i] is the resolved
    setup CODE for rts[i]."""
    buckets: dict[str, dict] = {}
    for rt, code in zip(rts, tags):
        if rt.pnl_usd is None:
            continue
        b = buckets.setdefault(code, {"n": 0, "net": 0.0, "gp": 0.0, "gl": 0.0,
                                      "nw": 0, "nl": 0, "hw": 0, "hwn": 0,
                                      "hl": 0, "hln": 0, "intraday": 0})
        b["n"] += 1
        b["net"] += rt.pnl_usd
        if rt.is_intraday:
            b["intraday"] += 1
        if rt.pnl_usd > 0:
            b["nw"] += 1; b["gp"] += rt.pnl_usd; b["hw"] += rt.hold_minutes; b["hwn"] += 1
        else:
            b["nl"] += 1; b["gl"] += rt.pnl_usd; b["hl"] += rt.hold_minutes; b["hln"] += 1
    rows = []
    for code, b in buckets.items():
        rows.append({
            "code": code, "name": cfg.display(code), "n": b["n"], "net": b["net"],
            "win_rate": (b["nw"] / b["n"] * 100) if b["n"] else 0.0,
            "pf": (b["gp"] / abs(b["gl"])) if b["gl"] < 0 else None,
            "expectancy": b["net"] / b["n"] if b["n"] else 0.0,
            "avg_win": b["gp"] / b["nw"] if b["nw"] else 0.0,
            "avg_loss": b["gl"] / b["nl"] if b["nl"] else 0.0,
            "hold_win": b["hw"] / b["hwn"] if b["hwn"] else 0.0,
            "hold_loss": b["hl"] / b["hln"] if b["hln"] else 0.0,
            "intraday_pct": b["intraday"] / b["n"] * 100 if b["n"] else 0.0,
        })
    rows.sort(key=lambda r: r["net"], reverse=True)
    return rows


# --- R-multiple aggregates (FR-PIVOT-10) — source of truth (SPEC D4) ---
# Separate from _kpis/_scoring_rows so their signatures + tests stay untouched.
# Operate on the R-bearing `records` (which carry R/HasR/StopStatus from _record).
# JS computeRKpis/computeRScoring mirror these; keep the two in sync.

def _r_kpis(records: list[dict]) -> dict:
    """R aggregates over the closed subset that has a valid R (with-stop).

    coverage = r_n / n_closed. expectancy_r = mean per-round-trip R (mirrors the
    dollar expectancy = mean per-RT pnl). blown = R below the −1R floor.
    invalid_stops = entries whose stop was entered but unusable (zero / wrong-side)
    — surfaced as a data-hygiene warning, distinct from 'no stop entered'."""
    closed = [r for r in records if r["PnL_USD"] is not None]
    with_r = [r for r in closed if r["R"] is not None]
    wins = [r["R"] for r in with_r if r["R"] > 0]
    losses = [r["R"] for r in with_r if r["R"] <= 0]
    rs = [r["R"] for r in with_r]
    invalid = sum(1 for r in records if r.get("StopStatus") in ("zero", "wrong_side"))
    return {
        "r_n": len(with_r), "n_closed": len(closed),
        "expectancy_r": (sum(rs) / len(rs)) if rs else None,
        "avg_win_r": (sum(wins) / len(wins)) if wins else None,
        "avg_loss_r": (sum(losses) / len(losses)) if losses else None,
        "blown": sum(1 for v in rs if v < -1.0),
        "invalid_stops": invalid,
    }


def _r_scoring(records: list[dict]) -> dict[str, dict]:
    """Per-setup R: {SetupCode: {r_n, n, expectancy_r}}. n = closed count in the
    group (the coverage denominator); expectancy_r over the with-R subset."""
    buckets: dict[str, dict] = {}
    for r in records:
        if r["PnL_USD"] is None:
            continue
        b = buckets.setdefault(r["SetupCode"], {"r_n": 0, "n": 0, "rsum": 0.0})
        b["n"] += 1
        if r["R"] is not None:
            b["r_n"] += 1
            b["rsum"] += r["R"]
    return {
        code: {"r_n": b["r_n"], "n": b["n"],
               "expectancy_r": (b["rsum"] / b["r_n"]) if b["r_n"] else None}
        for code, b in buckets.items()
    }


# --- presentation helpers ---

def _signed(v: float) -> str:
    """'+1,234.50' / '-1,234.50' — explicit sign, never relies on color alone."""
    return f"{'+' if v >= 0 else '-'}${abs(v):,.2f}"


def _cls(v: float) -> str:
    return "pos" if v >= 0 else "neg"


# NOTE: Python renderers `_equity_svg` / `_kpi_cards` / `_scoring_table` were
# removed in Tier-1 #1 — rendering moved to _APP_JS so KPI / equity / by-setup
# refresh on every filter change without a page reload. Python `_kpis` /
# `_max_drawdown` / `_streaks` / `_scoring_rows` are kept above as the
# source-of-truth for tests + as the entry point for any future server-side
# or CLI analytics.


def _read_vendor(name: str) -> str:
    p = VENDOR / name
    txt = p.read_text(encoding="utf-8")
    return txt.replace("</script", "<\\/script")  # safe inline


def build_html(rts: list[RoundTrip], stats: dict,
               anns: dict[str, annotations.Annotation] | None = None,
               cfg: annotations.TagConfig | None = None) -> str:
    anns = anns or {}
    cfg = cfg or annotations.TagConfig({}, {})
    tags = [annotations.resolve_setup_tag(rt.open_trade_id, rt.order_ref, anns, cfg)
            for rt in rts]
    records = [_record(rt, code, cfg.display(code), anns.get(rt.open_trade_id))
               for rt, code in zip(rts, tags)]
    # Analytics live in _APP_JS now (filter-time recompute); Python _kpis/
    # _scoring_rows are kept as the unit-tested source of truth (test_pivot.py)
    # and as a Python-side entry point for any future server-side/CLI use.
    data_json = json.dumps(records, ensure_ascii=False)
    cfg_json = json.dumps({"pos": POS, "neg": NEG, "neutral": NEUTRAL,
                           "dims": list(_PIVOT_DIMS)})
    # New York time, English tz abbrev (EDT/EST) — report is English-only.
    gen = datetime.now(timezone.utc).astimezone(ET_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    css = _read_vendor("pivot.min.css")
    jq = _read_vendor("jquery.min.js")
    jqui = _read_vendor("jquery-ui.min.js")
    pivot = _read_vendor("pivot.min.js")

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>TraderLens — Trade Analytics</title>
<style>{css}</style>
<style>{_PAGE_CSS}</style></head><body>
<div class="header-bar" role="region" aria-label="global filter">
  <div class="filters">
    <div class="range-info"><span id="rangeLabel">Range: —</span></div>
    <div class="chips" id="presetChips">
      <button class="chip" data-preset="today">Today</button>
      <button class="chip" data-preset="this-week">This week</button>
      <button class="chip" data-preset="last-week">Last week</button>
      <button class="chip" data-preset="this-month">This month</button>
      <button class="chip" data-preset="last-month">Last month</button>
      <button class="chip" data-preset="last-30">Last 30d</button>
      <button class="chip" data-preset="ytd">YTD</button>
      <button class="chip" data-preset="all">All</button>
    </div>
    <div class="controls">
      Setup <select id="fSetup"></select>
      Class <select id="fClass"></select>
      <span class="sep">·</span>
      <label>from <input type="date" id="fFrom"></label>
      <label>to <input type="date" id="fTo"></label>
      <span class="sep">·</span>
      <button id="rangePrev" type="button" title="shift window back by its own width">←</button>
      <button id="rangeNext" type="button" title="shift window forward by its own width">→</button>
      <button id="fReset" type="button">reset</button>
    </div>
  </div>
  <div class="brand">
    <h1>TraderLens — Trade Analytics</h1>
    <div class="meta">generated {gen} (New York) · neutral colors:
     <span class="pos">▲ blue = profit</span> · <span class="neg">▼ amber = loss</span></div>
  </div>
</div>

<div id="kpiHeadline"></div>

<h2>Calendar — daily net P&amp;L (click a day to drill down)</h2>
<div class="cal-cols-picker">
  <button id="calPrev" class="chip" type="button" title="show older months">←</button>
  <button id="calNext" class="chip" type="button" title="show newer months">→</button>
  <span class="muted" style="margin-left:10px">months:</span>
  <span id="calColsPicker">
    <button class="chip" data-cols="1" type="button">1</button>
    <button class="chip" data-cols="2" type="button">2</button>
    <button class="chip active" data-cols="3" type="button">3</button>
    <button class="chip" data-cols="4" type="button">4</button>
  </span>
</div>
<div id="calendar"></div>
<div id="drill" class="drill"></div>

<h2>Equity curve — cumulative net P&amp;L (by close order)</h2>
<div id="equityCurve"></div>

<h2>By setup — performance &amp; execution (FR-PIVOT-5)</h2>
<div id="scoringTable"></div>

<h2>Pivot — drag fields to slice</h2>
<div id="pivot"></div>

<h2>Trade detail (click a header to sort)</h2>
<input id="detailFilter" type="text" placeholder="filter rows (substring match)…"><span id="detailCount"></span><button id="detailExport" type="button" title="download the rows currently shown as CSV">⬇ CSV</button>
<div id="detail" class="scroll-x"></div>

<script>{jq}</script>
<script>{jqui}</script>
<script>{pivot}</script>
<script>var DATA = {data_json}; var CFG = {cfg_json};</script>
<script>{_APP_JS}</script>
</body></html>"""


def generate(db_path: str | Path = SQLITE_PATH, out: str | Path = DEFAULT_OUT,
             ann_path: str | Path = ANNOTATIONS_PATH, *,
             read_only: bool = False) -> tuple[Path, dict]:
    conn = sqlite_store.connect(str(db_path), read_only=read_only)
    try:
        if not read_only:
            sqlite_store.init_schema(conn)  # migrate only when we own the DB (writes)
        rows = sqlite_store.query_all(conn)
    finally:
        conn.close()
    rts, stats = pair_round_trips(coalesce_fills(rows))  # FR-PIVOT-2c: order = one trade
    anns = annotations.load_annotations(ann_path)
    cfg = annotations.load_tag_config()
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_html(rts, stats, anns, cfg), encoding="utf-8")
    return out_path, {**stats, "legs": len(rows)}


def write_template(db_path: str | Path = SQLITE_PATH,
                   out: str | Path = ANNOTATIONS_PATH) -> dict:
    """Pre-generate/refresh data/annotations.csv from current round-trips
    (FR-PIVOT-3d). User fills setup_tag/score/notes in Excel, then re-runs pivot."""
    conn = sqlite_store.connect(str(db_path))
    try:
        sqlite_store.init_schema(conn)  # idempotent: migrate DBs predating order_ref
        rows = sqlite_store.query_all(conn)
    finally:
        conn.close()
    rts, _ = pair_round_trips(coalesce_fills(rows))  # FR-PIVOT-2c: one row per order
    return annotations.write_tag_template(rts, out)


def _open_in_default_app(path: Path) -> None:
    """Hand a path off to the OS default handler (Excel for csv, browser for html).
    Best-effort: failures are swallowed so the caller can keep going with a
    printed fallback path."""
    p = str(path)
    if sys.platform == "win32":
        os.startfile(p)                                       # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.run(["open", p], check=False)
    else:
        subprocess.run(["xdg-open", p], check=False)


def review_flow(
    db: str,
    annotations_path: str,
    out: str,
    lookback: int | None = DEFAULT_EXPORT_LOOKBACK_DAYS,
    export_dir: str | Path = EXPORT_DIR,
) -> int:
    """Glued one-shot review loop (FR-PIVOT-3d + state-machine re-export).

    Refresh template -> hand csv to Excel -> wait for Enter -> re-export mts
    csv for last `lookback` trade_dates (state machine flips affected dates
    from State A to State B per the latest annotations) -> rebuild html ->
    open in browser. Excel writes the csv on Ctrl+S so users don't have to
    close it before we re-read.

    `lookback=None` means 'all' — re-export every distinct trade_date in
    SQLite. Default 90 days matches INTERFACE_CONTRACT C9 wrapper contract."""
    ann_p = Path(annotations_path).resolve()
    try:
        st = write_template(db, annotations_path)
    except PermissionError:                                   # csv locked by Excel
        print(f"[FAIL] {ann_p} is locked (Excel still has it open?).")
        print("       close Excel and re-run, or run `python -m src.pivot` to skip")
        print("       refresh and just rebuild the html from current annotations.")
        return 3
    print(f"[1/4] annotations -> {ann_p}")
    orphaned_part = f", {st['orphaned']} orphaned kept" if st['orphaned'] else ""
    print(f"      {st['total']} entries ({st['new']} new, {st['preserved']} preserved{orphaned_part})")
    if st.get("backup"):
        print(f"      backup -> {st['backup']}")

    try:
        _open_in_default_app(ann_p)
    except OSError as e:
        print(f"[WARN] could not auto-open Excel: {e}")
        print(f"       open manually: {ann_p}")

    print("[2/4] fill setup_tag / score / notes in Excel, save with Ctrl+S.")
    try:
        input("      press Enter here when done (Ctrl+C to abort) ... ")
    except (KeyboardInterrupt, EOFError):
        print("\n[ABORT] regen skipped; your annotations are saved as-is.")
        return 130

    # [3/4] Re-export mts csv for the lookback window (C8/C9): annotations the
    # user just saved may have flipped some dates to State B (MTS_CONFIRMED).
    conn = sqlite_store.connect(db)
    try:
        sqlite_store.init_schema(conn)
        re_stats = exporter.export_lookback(conn, lookback, Path(export_dir))
    finally:
        conn.close()
    state_b_count = sum(1 for s in re_stats if s.state == "B")
    nonempty = sum(1 for s in re_stats if s.exported_rows > 0)
    lookback_label = "all" if lookback is None else f"last {lookback} days"
    print(f"[3/4] re-export ({lookback_label}): {len(re_stats)} csv files "
          f"({state_b_count} State B / MTS_CONFIRMED, {nonempty} non-empty) -> {export_dir}")

    out_path, stats = generate(db, out)
    print(f"[4/4] {stats['legs']} legs -> {stats['round_trips']} round-trips "
          f"({stats['unmatched_close_qty']} unmatched-close, {stats['still_open_qty']} still-open)")
    print(f"      report -> {out_path}")
    try:
        webbrowser.open(Path(out_path).resolve().as_uri())
    except Exception as e:                                    # noqa: BLE001 (best-effort)
        print(f"[WARN] could not auto-open browser: {e}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.pivot")
    ap.add_argument("--db", default=str(SQLITE_PATH), help="SQLite path")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="output HTML path")
    ap.add_argument("--annotations", default=str(ANNOTATIONS_PATH),
                    help="annotations.csv path (for --tag-template / --review-flow)")
    ap.add_argument("--export-dir", default=str(EXPORT_DIR),
                    help="mts csv output dir (for --review-flow re-export)")
    ap.add_argument("--lookback", default=str(DEFAULT_EXPORT_LOOKBACK_DAYS),
                    help="re-export window (int days, default 90, or 'all'); "
                         "applies to --review-flow only")
    mx = ap.add_mutually_exclusive_group()
    mx.add_argument("--tag-template", action="store_true",
                    help="(re)generate data/annotations.csv instead of the HTML report")
    mx.add_argument("--review-flow", action="store_true",
                    help="glued loop: refresh template -> open Excel -> wait Enter -> "
                         "re-export mts csv (last --lookback days) -> rebuild html -> "
                         "open browser")
    args = ap.parse_args(argv)

    if args.tag_template:
        st = write_template(args.db, args.annotations)
        print(f"[OK] annotations template -> {st['path']}")
        orphaned_part = f", {st['orphaned']} orphaned kept" if st['orphaned'] else ""
        print(f"[OK] {st['total']} entries ({st['new']} new, {st['preserved']} preserved{orphaned_part})")
        print("     fill setup_tag / score / notes in Excel, then re-run the pivot.")
        return 0

    if args.review_flow:
        lookback = None if args.lookback.lower() == "all" else int(args.lookback)
        return review_flow(args.db, args.annotations, args.out,
                           lookback=lookback, export_dir=args.export_dir)

    out_path, stats = generate(args.db, args.out, args.annotations)
    print(f"[OK] {stats['legs']} legs -> {stats['round_trips']} round-trips "
          f"({stats['unmatched_close_qty']} unmatched-close, {stats['still_open_qty']} still-open)")
    print(f"[OK] report -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
