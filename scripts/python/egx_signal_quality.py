"""
EGX Signal Quality — find the HIGHEST-QUALITY signal tier + volume confirmation.
DOM/order-flow unavailable (delayed EGX feed). So we improve quality via:
 1. Precision tiering: top 5/10/15/20/30% by model prob (concentration vs win-rate)
 2. Volume-confirmation overlay: does requiring vol surge raise precision?
 3. Accumulation proxy (poor-man's order flow from OHLCV).
All on real trades + target_stop exit. Saves per-trade scores for instant reuse.
"""
import sqlite3, json, statistics as st, math, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import lightgbm as lgb, xgboost as xgb, joblib, numpy as np
from explosion_ml import _build_feature_row, _build_ohlcv_cache, _load_egx30_cache

DB=Path('/Users/dr.husam/tradingview-mcp-jackson/data/egx_trading.db')
M=Path('/Users/dr.husam/tradingview-mcp-jackson/scripts/python/models/ml_trainer')
COST=0.002; POS=0.10; CACHE='/tmp/egx_quality_scored.json'

def target_stop(entry, path, tgt=0.15, stp=-0.07, to=10):
    for i,(o,h,l,c) in enumerate(path[:to]):
        if entry>0 and (h-entry)/entry>=tgt: return tgt-COST
        if entry>0 and (l-entry)/entry<=stp: return stp-COST
    idx=min(to,len(path))-1
    if idx<0 or entry<=0: return None
    return (path[idx][3]-entry)/entry - COST

def get_path(conn,sym,ed,n=12):
    rows=conn.execute("SELECT open,high,low,close FROM ohlcv_history WHERE symbol=? AND date(bar_time,'unixepoch')>? ORDER BY bar_time LIMIT ?",(sym,ed,n)).fetchall()
    return [(float(o or 0),float(h or 0),float(l or 0),float(c or 0)) for o,h,l,c in rows]

def vol_ratio_at(conn,sym,ed):
    rows=conn.execute("SELECT volume FROM ohlcv_history WHERE symbol=? AND date(bar_time,'unixepoch')<=? ORDER BY bar_time DESC LIMIT 21",(sym,ed)).fetchall()
    v=[float(r[0] or 0) for r in rows]
    if len(v)<21 or sum(v[1:])==0: return 1.0
    return v[0]/(sum(v[1:21])/20)

def sharpe(rets,hold=6):
    if len(rets)<2: return 0
    sd=st.pstdev(rets); return max(-20,min(20,st.mean(rets)/sd*math.sqrt(252/hold))) if sd>1e-9 else 0
def eq_dd(rets):
    eq=1;pk=1;mdd=0
    for r in rets: eq*=(1+POS*r);pk=max(pk,eq);mdd=min(mdd,(eq-pk)/pk)
    return eq-1,mdd
def summ(rows,label):
    rs=[r['ts'] for r in rows]
    if not rs: return None
    wr=sum(1 for x in rs if x>=0.07)/len(rs); tr,dd=eq_dd(rs)
    return dict(label=label,n=len(rs),win=round(wr,3),avg=round(st.mean(rs),4),sharpe=round(sharpe(rs),2),totRet=round(tr,3),maxDD=round(dd,4))

def score_all():
    trades=json.load(open('/tmp/egx_clean_trades.json'))['trades']
    feats=json.load(open(str(M/'explosion_features_v3.json'))); nf=len(feats)
    lgbm=lgb.Booster(model_file=str(M/'explosion_lgbm_v3.txt'))
    xgbm=xgb.Booster(); xgbm.load_model(str(M/'explosion_xgb_v1.json'))
    rf=joblib.load(str(M/'explosion_rf_v1.pkl')); et=joblib.load(str(M/'explosion_et_v1.pkl'))
    conn=sqlite3.connect(str(DB)); conn.row_factory=sqlite3.Row
    cache=_build_ohlcv_cache(conn,'2025-12-31'); egx30=_load_egx30_cache(conn)
    print(f"[cache] {len(cache)} syms",flush=True)
    out=[]
    for i,t in enumerate(trades):
        sym=t['symbol'];ed=t['entry_date'];entry=t['entry_price']
        df=cache.get(sym)
        if df is None or entry<=0: continue
        row=_build_feature_row(df,ed,egx30=egx30)
        if row is None: continue
        X=np.array([row[:nf]],dtype=np.float32)
        try:
            p=(0.40*float(lgbm.predict(X)[0])+0.25*float(xgbm.predict(xgb.DMatrix(X))[0])
               +0.20*float(rf.predict_proba(X)[0,1])+0.15*float(et.predict_proba(X)[0,1]))
        except Exception: continue
        path=get_path(conn,sym,ed)
        if len(path)<3: continue
        bad=False;pc=entry
        for o,h,l,c in path:
            if pc>0 and abs(c-pc)/pc>0.50: bad=True;break
            pc=c
        if bad: continue
        ts=target_stop(entry,path)
        if ts is None: continue
        vr=vol_ratio_at(conn,sym,ed)
        out.append({'p':p,'ts':ts,'vr':vr})
        if (i+1)%1000==0: print(f"[score] {i+1}/{len(trades)} kept={len(out)}",flush=True)
    conn.close()
    json.dump(out,open(CACHE,'w'))
    return out

def analyze(scored):
    scored.sort(key=lambda x:-x['p']); n=len(scored)
    print("\n"+"="*78)
    print("SIGNAL QUALITY — precision tiers (model prob) × target_stop exit")
    print("="*78)
    print(f"  {'tier':22}{'n':>5}{'win%':>7}{'avg':>8}{'Sharpe':>8}{'maxDD':>8}")
    print("  "+"-"*62)
    rows=[]
    for frac in [0.05,0.10,0.15,0.20,0.30,1.0]:
        seg=scored[:max(1,int(n*frac))]
        s=summ(seg,f"top {int(frac*100)}%")
        rows.append(s)
        print(f"  {s['label']:22}{s['n']:>5}{s['win']*100:>6.1f}%{s['avg']*100:>+7.2f}%{s['sharpe']:>8.2f}{s['maxDD']*100:>7.1f}%")
    print("  "+"-"*62)
    # volume confirmation overlay on top-20%
    top20=scored[:int(n*0.20)]
    volconf=[x for x in top20 if x['vr']>=1.5]
    s_all=summ(top20,"top20% (all)"); s_vol=summ(volconf,"top20% + vol≥1.5×")
    print("\n  VOLUME-CONFIRMATION overlay (poor-man's order flow):")
    print(f"  {s_all['label']:22}{s_all['n']:>5}{s_all['win']*100:>6.1f}%{s_all['avg']*100:>+7.2f}%{s_all['sharpe']:>8.2f}")
    if s_vol: print(f"  {s_vol['label']:22}{s_vol['n']:>5}{s_vol['win']*100:>6.1f}%{s_vol['avg']*100:>+7.2f}%{s_vol['sharpe']:>8.2f}")
    print("="*78)
    best=max([r for r in rows if r['n']>=30], key=lambda x:x['sharpe'])
    print(f"\n  أعلى جودة (Sharpe): {best['label']} → win {best['win']*100:.0f}% Sharpe {best['sharpe']} (n={best['n']})")
    if s_vol and s_all and s_vol['win']>s_all['win']:
        print(f"  volume-confirmation يرفع win من {s_all['win']*100:.0f}% إلى {s_vol['win']*100:.0f}% ✅")
    elif s_vol:
        print(f"  volume-confirmation: {s_vol['win']*100:.0f}% vs {s_all['win']*100:.0f}% (لا يحسّن)")
    json.dump({'tiers':rows,'top20_all':s_all,'top20_vol':s_vol}, open('/tmp/egx_signal_quality.json','w'),indent=1)

if __name__=='__main__':
    import os
    if os.path.exists(CACHE) and '--rescore' not in sys.argv:
        scored=json.load(open(CACHE)); print(f"[cache] reusing {len(scored)} scored trades")
    else:
        scored=score_all()
    analyze(scored)
