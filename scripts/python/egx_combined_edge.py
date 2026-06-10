"""
EGX Combined Edge — entry (model top-decile) + exit (target_stop) together.
Decisive validation before changing predict_ensemble: does the proven exit
improvement stack on the model's best entries?
"""
import sqlite3, json, statistics as st, math, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import lightgbm as lgb, xgboost as xgb, joblib, numpy as np
from explosion_ml import _build_feature_row, _build_ohlcv_cache, _load_egx30_cache

DB=Path('/Users/dr.husam/tradingview-mcp-jackson/data/egx_trading.db')
M=Path('/Users/dr.husam/tradingview-mcp-jackson/scripts/python/models/ml_trainer')
COST=0.002; POS=0.10

def load_models():
    return (lgb.Booster(model_file=str(M/'explosion_lgbm_v3.txt')),
            (lambda b: (b.load_model(str(M/'explosion_xgb_v1.json')) or b))(xgb.Booster()),
            joblib.load(str(M/'explosion_rf_v1.pkl')),
            joblib.load(str(M/'explosion_et_v1.pkl')))

def get_path(conn, sym, ed, n=12):
    rows=conn.execute("SELECT open,high,low,close FROM ohlcv_history WHERE symbol=? AND date(bar_time,'unixepoch')>? ORDER BY bar_time LIMIT ?",(sym,ed,n)).fetchall()
    return [(float(o or 0),float(h or 0),float(l or 0),float(c or 0)) for o,h,l,c in rows]

def target_stop(entry, path, tgt=0.15, stp=-0.07, to=10):
    for i,(o,h,l,c) in enumerate(path[:to]):
        if entry>0 and (h-entry)/entry>=tgt: return tgt-COST
        if entry>0 and (l-entry)/entry<=stp: return stp-COST
    idx=min(to,len(path))-1
    if idx<0 or entry<=0: return None
    return (path[idx][3]-entry)/entry - COST

def fixed5(entry, path):
    idx=min(5,len(path))-1
    if idx<0 or entry<=0: return None
    return (path[idx][3]-entry)/entry - COST

def sharpe(rets, hold=6):
    if len(rets)<2: return 0
    sd=st.pstdev(rets)
    return max(-20,min(20, st.mean(rets)/sd*math.sqrt(252/hold))) if sd>1e-9 else 0

def eq_dd(rets):
    eq=1;pk=1;mdd=0
    for r in rets: eq*=(1+POS*r);pk=max(pk,eq);mdd=min(mdd,(eq-pk)/pk)
    return eq-1, mdd

def main():
    trades=json.load(open('/tmp/egx_clean_trades.json'))['trades']
    feats=json.load(open(str(M/'explosion_features_v3.json'))); nfeat=len(feats)
    lgbm,xgbm,rf,et=load_models()
    conn=sqlite3.connect(str(DB)); conn.row_factory=sqlite3.Row
    cache=_build_ohlcv_cache(conn,'2025-12-31'); egx30=_load_egx30_cache(conn)
    print(f"[cache] {len(cache)} symbols, EGX30={len(egx30)}",flush=True)
    scored=[]
    for i,t in enumerate(trades):
        sym=t['symbol']; ed=t['entry_date']; entry=t['entry_price']
        df=cache.get(sym)
        if df is None or entry<=0: continue
        row=_build_feature_row(df, ed, egx30=egx30)
        if row is None: continue
        X=np.array([row[:nfeat]],dtype=np.float32)
        try:
            p=(0.40*float(lgbm.predict(X)[0])+0.25*float(xgbm.predict(xgb.DMatrix(X))[0])
               +0.20*float(rf.predict_proba(X)[0,1])+0.15*float(et.predict_proba(X)[0,1]))
        except Exception: continue
        path=get_path(conn,sym,ed)
        if len(path)<3: continue
        # corporate-action guard
        bad=False;pc=entry
        for o,h,l,c in path:
            if pc>0 and abs(c-pc)/pc>0.50: bad=True;break
            pc=c
        if bad: continue
        r_fixed=fixed5(entry,path); r_ts=target_stop(entry,path)
        if r_fixed is None or r_ts is None: continue
        scored.append({'p':p,'fixed':r_fixed,'ts':r_ts})
        if (i+1)%1000==0: print(f"[score] {i+1}/{len(trades)} kept={len(scored)}",flush=True)
    conn.close()
    scored.sort(key=lambda x:-x['p'])
    n=len(scored); top=scored[:int(n*0.20)]  # top 20% by model prob
    def summ(rows,key):
        rs=[r[key] for r in rows]; wr=sum(1 for x in rs if x>=0.07)/len(rs)
        tr,dd=eq_dd(rs)
        return dict(n=len(rs),win=round(wr,3),avg=round(st.mean(rs),4),sharpe=round(sharpe(rs),3),
                    totRet=round(tr,3),maxDD=round(dd,4))
    print("\n"+"="*84)
    print("COMBINED EDGE — Entry (model top-20%) × Exit (target_stop vs fixed-5d)")
    print("="*84)
    print(f"scored={n}  top20%={len(top)}\n")
    print(f"  {'segment':24}{'n':>5}{'win%':>7}{'avg':>8}{'Sharpe':>8}{'maxDD':>8}{'totRet':>9}")
    print("  "+"-"*70)
    for label,rows,key in [('ALL + fixed5d',scored,'fixed'),('ALL + target_stop',scored,'ts'),
                           ('TOP20% + fixed5d',top,'fixed'),('TOP20% + target_stop',top,'ts')]:
        s=summ(rows,key)
        print(f"  {label:24}{s['n']:>5}{s['win']*100:>6.1f}%{s['avg']*100:>+7.2f}%{s['sharpe']:>8.2f}{s['maxDD']*100:>7.1f}%{s['totRet']*100:>+8.1f}%")
    print("  "+"-"*70)
    base=summ(scored,'fixed'); best=summ(top,'ts')
    print(f"\n  MAXIMAL: TOP20%+target_stop  Sharpe={best['sharpe']:.2f} win={best['win']*100:.1f}% DD={best['maxDD']*100:.1f}%")
    print(f"  vs baseline ALL+fixed5d      Sharpe={base['sharpe']:.2f} win={base['win']*100:.1f}% DD={base['maxDD']*100:.1f}%")
    print(f"  Sharpe lift: {best['sharpe']/max(base['sharpe'],0.01):.2f}x  |  WinRate lift: {best['win']/max(base['win'],0.01):.2f}x")
    print("="*84)
    json.dump({'baseline':base,'maximal':best}, open('/tmp/egx_combined_edge.json','w'),indent=1)
    print("[report] /tmp/egx_combined_edge.json")

if __name__=='__main__': main()
