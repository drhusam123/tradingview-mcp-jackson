"""
EGX Exit Optimizer — backtest smarter exits on REAL price paths.
Since magnitude is unpredictable at entry (regressor Spearman≈0), the only way to
capture the +60% winners is via EXIT logic that lets winners run. Tests several
exit strategies on the clean trade set's real forward price paths.

Cost = 0.2% round-trip (0.1% buy + 0.1% sell).
"""
import sqlite3, json, statistics as st, math, sys
from pathlib import Path

DB = Path('/Users/dr.husam/tradingview-mcp-jackson/data/egx_trading.db')
TRADES = Path('/tmp/egx_clean_trades.json')
COST = 0.002
POS = 0.10            # fixed fraction per trade
MAXD = 15            # max trading days to hold
OUT = Path('/tmp/egx_exit_report.json')


def get_path(conn, sym, entry_date, ndays=MAXD):
    """Return list of bars (open,high,low,close) for the ndays trading bars AFTER entry_date."""
    rows = conn.execute(
        "SELECT date(bar_time,'unixepoch') d, open,high,low,close FROM ohlcv_history "
        "WHERE symbol=? AND date(bar_time,'unixepoch')>? ORDER BY bar_time LIMIT ?",
        (sym, entry_date, ndays)
    ).fetchall()
    return [(r[0], float(r[1] or 0), float(r[2] or 0), float(r[3] or 0), float(r[4] or 0)) for r in rows]


def atr14(conn, sym, entry_date):
    rows = conn.execute(
        "SELECT high,low,close FROM ohlcv_history WHERE symbol=? AND date(bar_time,'unixepoch')<=? "
        "ORDER BY bar_time DESC LIMIT 15", (sym, entry_date)
    ).fetchall()
    if len(rows) < 2: return None
    rows = [(float(h or 0), float(l or 0), float(c or 0)) for h,l,c in rows][::-1]
    trs = []
    for i in range(1, len(rows)):
        h,l,_ = rows[i]; pc = rows[i-1][2]
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    return sum(trs)/len(trs) if trs else None


def sim_fixed(entry, path, hold):
    if len(path) < 1: return None
    idx = min(hold, len(path)) - 1
    exit_px = path[idx][4]
    return (exit_px - entry)/entry - COST, idx+1


def sim_target_stop(entry, path, target=0.15, stop=-0.07, timeout=10):
    for i, (_,o,h,l,c) in enumerate(path[:timeout]):
        if (h-entry)/entry >= target: return target - COST, i+1
        if (l-entry)/entry <= stop:   return stop - COST, i+1
    idx = min(timeout, len(path)) - 1
    if idx < 0: return None
    return (path[idx][4]-entry)/entry - COST, idx+1


def sim_atr_trail(entry, path, atr, mult=2.5):
    if atr is None or atr <= 0: return None
    peak = entry
    for i,(_,o,h,l,c) in enumerate(path):
        peak = max(peak, h)
        stop = peak - mult*atr
        if l <= stop:
            return (stop-entry)/entry - COST, i+1
    idx = len(path)-1
    return (path[idx][4]-entry)/entry - COST, len(path)


def sim_hybrid(entry, path, atr, mult=1.5):
    """If up >10% by day 3 → trail (let it run); else exit day 5."""
    if len(path) < 1: return None
    up_d3 = (path[min(2,len(path)-1)][2]-entry)/entry  # high by ~day3
    if up_d3 > 0.10 and atr and atr > 0:
        peak = entry
        for i,(_,o,h,l,c) in enumerate(path):
            peak = max(peak,h); stop = peak - mult*atr
            if l <= stop: return (stop-entry)/entry - COST, i+1
        idx=len(path)-1
        return (path[idx][4]-entry)/entry - COST, len(path)
    else:
        idx = min(5,len(path))-1
        return (path[idx][4]-entry)/entry - COST, idx+1


def metrics(rets, holds, name):
    if not rets: return None
    n=len(rets); wr=sum(1 for r in rets if r>=0.07)/n
    avg=st.mean(rets); sd=st.pstdev(rets) if n>1 else 1e-9
    avg_hold=st.mean(holds) if holds else 5
    sharpe = (avg/sd*math.sqrt(252/max(avg_hold,1))) if sd>1e-9 else 0
    sharpe=max(-20,min(20,sharpe))
    # equity at 10% fixed fraction
    eq=1.0; peak=1.0; mdd=0
    for r in rets:
        eq*= (1+POS*r); peak=max(peak,eq); mdd=min(mdd,(eq-peak)/peak)
    big = sum(1 for r in rets if r>=0.20)
    return {'strategy':name,'n':n,'win_rate':round(wr,4),'avg_return':round(avg,4),
            'sharpe':round(sharpe,3),'max_dd':round(mdd,4),'avg_hold_days':round(avg_hold,1),
            'total_return':round(eq-1,4),'big_winners_captured':big}


def main():
    d=json.load(open(TRADES)); trades=d['trades']
    conn=sqlite3.connect(str(DB))
    strats={k:([],[]) for k in ['baseline_5d','target_stop','atr_trail','hybrid','hold_3d','hold_7d','hold_10d']}
    skipped=0
    for i,t in enumerate(trades):
        sym=t['symbol']; ed=t['entry_date']; entry=t['entry_price']
        if entry<=0: skipped+=1; continue
        path=get_path(conn,sym,ed)
        if len(path)<3: skipped+=1; continue
        # skip corporate-action distortions in path
        bad=False; pc=entry
        for _,o,h,l,c in path:
            if pc>0 and abs(c-pc)/pc>0.50: bad=True; break
            pc=c
        if bad: skipped+=1; continue
        atr=atr14(conn,sym,ed)
        for name,fn in [('baseline_5d',lambda:sim_fixed(entry,path,5)),
                        ('hold_3d',lambda:sim_fixed(entry,path,3)),
                        ('hold_7d',lambda:sim_fixed(entry,path,7)),
                        ('hold_10d',lambda:sim_fixed(entry,path,10)),
                        ('target_stop',lambda:sim_target_stop(entry,path)),
                        ('atr_trail',lambda:sim_atr_trail(entry,path,atr)),
                        ('hybrid',lambda:sim_hybrid(entry,path,atr))]:
            r=fn()
            if r: strats[name][0].append(r[0]); strats[name][1].append(r[1])
        if (i+1)%1000==0: print(f"[exit] {i+1}/{len(trades)} skipped={skipped}",flush=True)
    conn.close()
    results=[metrics(rl,hl,nm) for nm,(rl,hl) in strats.items()]
    results=[r for r in results if r]
    results.sort(key=lambda x:-x['sharpe'])
    print("\n"+"="*92)
    print("EGX EXIT OPTIMIZER — REAL PRICE-PATH BACKTEST  (cost 0.2%, 10% sizing)")
    print("="*92)
    print(f"trades={len(trades)} skipped={skipped}\n")
    print(f"  {'strategy':16} {'n':>5} {'win%':>7} {'avg/trd':>9} {'Sharpe':>7} {'maxDD':>8} {'hold':>5} {'totRet':>10} {'bigCap':>7}")
    print("  "+"-"*88)
    base=next(r for r in results if r['strategy']=='baseline_5d')
    for r in results:
        print(f"  {r['strategy']:16} {r['n']:>5} {r['win_rate']*100:>6.1f}% {r['avg_return']*100:>+8.2f}% "
              f"{r['sharpe']:>7.2f} {r['max_dd']*100:>7.1f}% {r['avg_hold_days']:>5.1f} {r['total_return']*100:>+9.1f}% {r['big_winners_captured']:>7}")
    print("  "+"-"*88)
    best=results[0]
    print(f"\n  BASELINE (5d): Sharpe={base['sharpe']:.2f} avg={base['avg_return']*100:+.2f}% bigCap={base['big_winners_captured']}")
    print(f"  BEST ({best['strategy']}): Sharpe={best['sharpe']:.2f} avg={best['avg_return']*100:+.2f}% bigCap={best['big_winners_captured']}")
    print(f"  Sharpe improvement: {best['sharpe']/max(base['sharpe'],0.01):.2f}x | avg-return improvement: {best['avg_return']/max(base['avg_return'],0.0001):.2f}x")
    print("="*92)
    json.dump({'baseline':base,'best':best,'all':results,'skipped':skipped}, open(OUT,'w'), indent=1)
    print(f"[report] {OUT}")

if __name__=='__main__': main()
