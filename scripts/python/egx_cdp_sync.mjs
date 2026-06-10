/**
 * EGX CDP Direct Sync — pulls live EGX_DLY: bars from TradingView via CDP.
 * Proven path (audit-verified 2026-05-30). Writes raw bars to JSON for the
 * Python validator+loader to ingest (keeps DB writes single-owner = Python).
 *
 * Usage:
 *   node egx_cdp_sync.mjs <symbolsCsv> <timeframe> <count> <outJson>
 *   node egx_cdp_sync.mjs COMI,HRHO,EGX30 D 500 /tmp/egx_sync_D.json
 */
import CDP from 'chrome-remote-interface';
import { writeFileSync } from 'fs';

const [,, symbolsCsv, timeframe='D', countStr='500', outPath='/tmp/egx_sync.json'] = process.argv;
const symbols = symbolsCsv.split(',').map(s => s.trim()).filter(Boolean);
const count = parseInt(countStr, 10);

const API  = 'window.TradingViewApi._activeChartWidgetWV.value()';
const BARS = API + '._chartWidget.model().mainSeries().bars()';
const SETTLE_MS = 3500;

async function ev(R, expr) {
  const r = await R.evaluate({ expression: expr, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) return { __err: r.exceptionDetails.text || 'exception' };
  return r.result.value;
}

function pullBarsExpr(n) {
  return `(function(){try{
    var b=${BARS}; if(!b||typeof b.lastIndex!=='function') return {__err:'no bars'};
    var end=b.lastIndex(), start=Math.max(b.firstIndex(), end-${n}+1), out=[];
    for(var i=start;i<=end;i++){var v=b.valueAt(i);
      if(v) out.push({time:v[0],open:v[1],high:v[2],low:v[3],close:v[4],volume:v[5]||0});}
    return {sym:${API}.symbol(), res:${API}.resolution(), bars:out, total:b.size()};
  }catch(e){return {__err:String(e)}}})()`;
}

const list = await (await fetch('http://localhost:9222/json/list')).json();
const chart = list.find(t => /tradingview\.com\/chart/i.test(t.url||''));
if (!chart) { console.error(JSON.stringify({ok:false,error:'no chart tab'})); process.exit(1); }

const client = await CDP({ target: chart.webSocketDebuggerUrl });
const { Runtime } = client;
await Runtime.enable();

const results = {};
let ok = 0, fail = 0;
for (const sym of symbols) {
  const tvSym = sym.startsWith('EGX_DLY:') ? sym : `EGX_DLY:${sym}`;
  const tvSymJson = JSON.stringify(tvSym);
  const tfJson = JSON.stringify(timeframe);
  await ev(Runtime, `(function(){try{
    var chart=${API};
    chart.setSymbol(${tvSymJson}, {});
    chart.setResolution(${tfJson}, {});
    return true;
  }catch(e){return String(e)}})()`);
  await new Promise(r => setTimeout(r, SETTLE_MS));

  let matched = false;
  for (let i = 0; i < 20; i++) {
    const current = await ev(Runtime, `(function(){try{return ${API}.symbol()}catch(e){return ''}})()`);
    if (String(current || '').toUpperCase() === tvSym.toUpperCase()) {
      matched = true;
      break;
    }
    await new Promise(r => setTimeout(r, 500));
  }

  if (!matched) {
    const current = await ev(Runtime, `(function(){try{return ${API}.symbol()}catch(e){return ''}})()`);
    results[sym] = { error: `symbol_mismatch expected=${tvSym} got=${current || 'unknown'}` };
    fail++;
    process.stderr.write(`✗ ${sym} — symbol mismatch (${current || 'unknown'})\n`);
    continue;
  }

  let pull = await ev(Runtime, pullBarsExpr(count));
  // one retry if bars not ready yet
  if (pull && pull.__err || !pull || !pull.bars || pull.bars.length === 0) {
    await new Promise(r => setTimeout(r, SETTLE_MS));
    pull = await ev(Runtime, pullBarsExpr(count));
  }
  if (pull && pull.bars && pull.bars.length > 0 && String(pull.sym || '').toUpperCase() === tvSym.toUpperCase()) {
    results[sym] = { tvSym: pull.sym, res: pull.res, total: pull.total, bars: pull.bars };
    ok++;
    process.stderr.write(`✓ ${sym} ${pull.sym} (${pull.bars.length} bars, last close ${pull.bars[pull.bars.length-1].close})\n`);
  } else {
    results[sym] = { error: (pull && pull.__err) || `empty_or_symbol_mismatch got=${pull?.sym || 'unknown'}` };
    fail++;
    process.stderr.write(`✗ ${sym} — ${(pull && pull.__err) || `empty_or_symbol_mismatch got=${pull?.sym || 'unknown'}`}\n`);
  }
}
await client.close();
writeFileSync(outPath, JSON.stringify({ ok, fail, timeframe, count, results }, null, 0));
console.log(JSON.stringify({ ok, fail, out: outPath }));
