# EGX Autonomous Intelligence — Development Roadmap
## خطة التطوير الشاملة | مايو 2026

**المالك:** Dr. Husam  
**تاريخ الإعداد:** 2026-05-14  
**الوضع الحالي:** Phase 16 مكتملة، 27,000+ سطر Python، 16 محرك متخصص

---

## 📊 تقييم الوضع الحالي (Baseline Assessment)

### ما يعمل بشكل ممتاز ✅
| المكوّن | الحالة | الأرقام |
|---------|--------|---------|
| OHLCV History | مكتمل | 73,866 شمعة، 253 سهم، ديسمبر 2020 → مايو 2026 |
| Explosive Moves | مكتمل | 13,462 انفجار مصنَّف |
| Counterfactual Events | مكتمل | 277,561 حدث |
| Failure Reconstructions | مكتمل | 30,000 فشل مُعاد تحليله |
| Stock DNA | مكتمل | 247 سهم × 3 archetypes |
| Knowledge Graph | مكتمل | 92 عقدة، 459 حافة |
| Cron Automation | مكتمل | 9 مهام مجدولة |
| Phases 1–16 | مكتملة كودياً | 17 محرك Python |

### نقاط الضعف الجوهرية ⚠️
| المشكلة | الأثر | الأولوية |
|---------|-------|---------|
| **6 قوانين فقط** — precision 6-12%، 1 فقط يتفوق على العشوائي | القرارات مبنية على أساس هش | 🔴 حرج |
| **Macro محدود** — 3 مؤشرات فقط (USD/EGP, CPI, CBE) | Phase 8 أعمى جزئياً | 🔴 حرج |
| **Sector ALL = LEADERSHIP** — 20 قطاع كلها LEADERSHIP | التمييز القطاعي معطل | 🟠 عالٍ |
| **لا networkx** — Graph Analysis بدون مكتبة | Phase 16 Memory Graph محدودة | 🟠 عالٍ |
| **لا tigramite** — Causal Inference بسيط جداً | Phase 5 تحت طاقتها الحقيقية | 🟠 عالٍ |
| **لا xgboost/tsfresh** — Feature Mining بطيء | اكتشاف Patterns يستغرق وقتاً أطول | 🟡 متوسط |
| **Fundamentals ضعيفة** — 267 سجل فقط | لا ربط بين السعر والأساسيات | 🟡 متوسط |
| **Phase 11 غائبة** — ثغرة في الترقيم | Arbitrage/Pairs Analysis ناقصة | 🟡 متوسط |

---

## 🗺️ خارطة الطريق (5 محاور)

---

## المحور الأول: الإصلاحات الحرجة (أسبوع 1-2)
*هذه مشاكل تعطل النتائج الحالية — تُصلح فوراً*

### 🔴 M1.1 — توسيع قاعدة القوانين (من 6 إلى 50+ قانون)

**المشكلة:** `precursor_patterns` فيها 6 أنماط فقط، والنظام يبحث في 4 features فقط  
**الحل:** توسيع `cmd_precursor_discovery` في `market_intelligence.py` لاستخدام:

```python
# الـ features الحالية (4)
['pre3_bb_width', 'pre5_bb_width', 'pre3_momentum_5d', 'pre5_rsi']

# يجب إضافة (20+ feature)
['pre1_atr_ratio', 'pre3_vol_ratio', 'pre5_adx', 'pre1_obv_change',
 'ignition_speed', 'cascade_score', 'compression_days',
 'pre1_close_to_high', 'pre3_stoch_k', 'pre5_cci',
 'pre1_macd_hist', 'pre3_rsi_slope', 'pre5_adx_slope',
 'pre1_bb_position', 'pre3_volume_ma_ratio', 'pre5_momentum_10d',
 'pre1_high_low_range', 'pre3_avg_body_size', 'sector_rank', 'regime_code']
```

**المكتبات:** `tsfresh` (تستخرج 800+ feature تلقائياً) + `scipy.stats`  
**الهدف:** 50+ قانون، 15+ منها DOMINANT أو ACTIVE  

```bash
pip3 install tsfresh
```

---

### 🔴 M1.2 — إصلاح Sector DNA Classification

**المشكلة:** 14 قطاع = LEADERSHIP بسبب sync_pct >= 75 يُحقق لكل القطاعات  
**السبب الجذري:** Finance (3,900 انفجار) دائماً تجد انفجاراً آخر خلال ±5 أيام  
**الحل:** تطبيق تصنيف نسبي (relative ranking) بدلاً من ثوابت مطلقة:

```python
# بدلاً من:
if sync_pct >= 75 and n_stocks_w_expl >= 5: s_archetype = 'LEADERSHIP'

# استخدم:
sync_rank = percentile_rank(sector['sync_pct'], all_sectors_sync)
explosion_density = n_explosions / n_stocks  # density per stock
if sync_rank >= 80 and explosion_density >= 15: s_archetype = 'LEADERSHIP'
elif sync_rank >= 60 and bull_pct >= 65:        s_archetype = 'BULL_DRIVEN'
# ... etc
```

---

### 🔴 M1.3 — تغذية Macro Pipeline بـ 22 مؤشر

**المشكلة:** `macro_data` فيها 3 مؤشرات فقط، Phase 8 تعتمد على بيانات ناقصة  
**الحل:** توسيع `fetch_economics.mjs` لتجميع:

| المصدر | البيانات | التحديث |
|--------|---------|---------|
| FRED API (مجاني) | US Fed Rate, Oil, Gold, S&P500, VIX | يومي |
| World Bank API | Egypt GDP, Unemployment, Trade Balance | شهري |
| NBE/CBE APIs | EGX30 Index, T-Bills Rate | أسبوعي |
| Yahoo Finance | EEM (EM ETF), DXY Index | يومي |

```javascript
// إضافة للـ fetch_economics.mjs
const FRED_INDICATORS = {
  'FEDFUNDS': 'fed_rate',
  'DCOILWTICO': 'oil_wti',
  'GOLDAMGBD228NLBM': 'gold_usd',
  'VIXCLS': 'vix',
  'SP500': 'sp500'
};
```

---

## المحور الثاني: تقوية المكتبات (أسبوع 2-3)
*تثبيت وتكامل المكتبات المفقودة الحرجة*

### 📦 M2.1 — تثبيت حزمة التحليل المتقدم

```bash
# 1. NetworkX — للـ Knowledge Graph والـ Contagion Analysis
pip3 install networkx

# 2. tsfresh — لاستخراج آلاف الـ Features تلقائياً من Time Series
pip3 install tsfresh

# 3. xgboost — لتصنيف الـ Patterns بدقة أعلى من sklearn
pip3 install xgboost

# 4. pyod — لـ Anomaly Detection في اكتشاف الانفجارات
pip3 install pyod

# 5. tigramite — لـ Causal Inference حقيقي (PCMCI algorithm)
pip3 install tigramite

# 6. ta-lib wrapper (بدون C compilation)
pip3 install pandas-ta  # بديل خفيف لـ TA-Lib
```

**التوافق المتوقع:** كلها تعمل مع numpy 1.23 + pandas 2.3  

---

### 📦 M2.2 — تكامل NetworkX مع Knowledge Graph (Phase 16)

**المكتبة:** `networkx` + `community` (Louvain)  
**الحالة الحالية:** الـ Graph محفوظ في SQLite كـ nodes/edges جدولين منفصلين  
**التحسين:** تحميل Graph وتشغيل خوارزميات حقيقية:

```python
import networkx as nx
from networkx.algorithms import community

def build_knowledge_graph(db):
    G = nx.DiGraph()
    nodes = db.execute("SELECT node_id, node_type, label FROM knowledge_graph_nodes").fetchall()
    edges = db.execute("SELECT source, target, relationship, weight FROM knowledge_graph_edges").fetchall()
    
    for nid, ntype, label in nodes:
        G.add_node(nid, type=ntype, label=label)
    for src, tgt, rel, w in edges:
        G.add_edge(src, tgt, relationship=rel, weight=w or 1.0)
    
    return G

def analyze_graph(G):
    return {
        'pagerank':        nx.pagerank(G.to_undirected()),
        'betweenness':     nx.betweenness_centrality(G),
        'communities':     list(community.greedy_modularity_communities(G.to_undirected())),
        'hub_nodes':       sorted(G.nodes(), key=lambda n: G.degree(n), reverse=True)[:10],
        'law_influence':   {n: G.out_degree(n) for n in G.nodes() if G.nodes[n].get('type')=='law'},
    }
```

**الإضافة للـ market_cognition.py Stage 5 (consolidate_memory)**

---

### 📦 M2.3 — تكامل tigramite مع Phase 5 (Causal Engine)

**المكتبة:** `tigramite` — أقوى مكتبة لـ Causal Inference في Time Series  
**الخوارزمية:** PCMCI (Peter-Clark Momentary Conditional Independence)  
**الحالة الحالية:** `causal_engine.py` يستخدم Granger Causality مبسطة من statsmodels  

**التحسين المقترح في `causal_engine.py`:**

```python
def cmd_causal_pcmci(params):
    """
    Phase 5 — PCMCI Causal Discovery
    يكتشف العلاقات السببية مع التأخيرات الزمنية بين:
    - القطاعات (هل Finance تقود Industrial?)
    - المؤشرات (هل compression يسبق الانفجار بـ 3 أيام؟)
    - العالم الخارجي (هل VIX يؤثر على EGX بعد يومين؟)
    """
    from tigramite import data_processing as pp
    from tigramite.pcmci import PCMCI
    from tigramite.independence_tests.parcorr import ParCorr
    
    # تحميل بيانات القطاعات كـ multivariate time series
    con = get_connection()
    df = pd.read_sql("""
        SELECT bar_time, symbol, close, volume, rsi14, bb_position
        FROM ohlcv_history oh
        JOIN indicators_cache ic USING (symbol)
        WHERE bar_time > UNIXEPOCH('now', '-500 days')
        ORDER BY bar_time
    """, con)
    
    # Pivot إلى matrix (time × features)
    sector_returns = df.groupby(['bar_time', 'sector'])['return_1d'].mean().unstack()
    
    dataframe = pp.DataFrame(
        sector_returns.values,
        var_names=sector_returns.columns.tolist()
    )
    
    pcmci = PCMCI(dataframe=dataframe, cond_ind_test=ParCorr(), verbosity=0)
    results = pcmci.run_pcmci(tau_max=5, pc_alpha=0.05)
    
    # استخراج الـ causal links
    causal_links = []
    for i, var in enumerate(sector_returns.columns):
        for j in range(len(sector_returns.columns)):
            for lag in range(1, 6):
                p_val = results['p_matrix'][i, j, lag]
                if p_val < 0.05:
                    causal_links.append({
                        'cause': sector_returns.columns[j],
                        'effect': var,
                        'lag_days': lag,
                        'p_value': round(p_val, 4),
                        'strength': round(abs(results['val_matrix'][i,j,lag]), 3)
                    })
    
    return {'causal_links': causal_links, 'n_discovered': len(causal_links)}
```

---

## المحور الثالث: Phase 17 — Graph Neural Network Contagion
*أسبوع 3-4 | المرجع: jwwthu/GNN4Fintech + shubham777/US-Stock-Market-Analysis*

### 🧠 M3.1 — تحويل Sector Contagion إلى GNN

**الهدف:** تجاوز الـ correlation matrix البسيطة إلى نموذج رسمي للعدوى  
**المكتبات:** `networkx` + `scipy.sparse` (بدون PyTorch في المرحلة الأولى)  

**الملف الجديد:** `scripts/python/graph_contagion_engine.py`

```
Phase 17 — Graph Contagion Engine
===================================
يحوّل الـ 20 قطاع و 253 سهم إلى شبكة معقدة ويكتشف:
1. Contagion Paths: أقصر مسار للعدوى من Finance إلى أي قطاع
2. Market Bridges: الأسهم التي تربط قطاعات غير مترابطة
3. Cascade Simulation: محاكاة انتشار الصدمة عبر الشبكة
4. Community Detection: اكتشاف مجموعات الأسهم المتزامنة

الخوارزميات:
- Louvain Community Detection
- PageRank لتحديد الأسهم الأكثر تأثيراً
- Betweenness Centrality للـ Market Bridges
- Epidemic SIR Model لمحاكاة الانتشار
```

**Schema جديد:**
```sql
CREATE TABLE IF NOT EXISTS contagion_network (
    source_sector TEXT,
    target_sector TEXT,
    transmission_rate REAL,   -- fraction of source explosions → target within 10d
    avg_delay_days   REAL,
    n_observations   INTEGER,
    regime           TEXT,
    updated_at       TEXT,
    PRIMARY KEY (source_sector, target_sector, regime)
);

CREATE TABLE IF NOT EXISTS stock_centrality (
    symbol           TEXT PRIMARY KEY,
    pagerank         REAL,
    betweenness      REAL,
    degree_centrality REAL,
    community_id     INTEGER,
    bridge_score     REAL,   -- كلما ارتفع كلما كان الوسيط أهم
    updated_at       TEXT
);
```

---

### 🧠 M3.2 — Momentum Spillover Detection

**المرجع:** jwwthu/GNN4Fintech — "Momentum Spillover in Networks"  
**الفكرة:** اكتشاف ما إذا كان momentum سهم ما ينتقل للأسهم المرتبطة به

```python
def momentum_spillover_matrix(db, lookback_days=252):
    """
    يحسب لكل زوج (سهم A، سهم B):
    - هل momentum A اليوم يُنبئ بـ return B غداً؟
    - ما قوة وتأخير هذا الانتقال؟
    """
    # الناتج: spillover_matrix[A][B] = (correlation, avg_lag, p_value)
```

---

## المحور الرابع: Phase 18 — Reinforcement Learning Layer
*أسبوع 4-6 | المرجع: AI4Finance-Foundation/FinRL*

### 🤖 M4.1 — بيئة EGX Trading Environment

**المكتبة:** `vectorbt` (مثبتة!) + custom RL wrapper  
**لماذا vectorbt وليس FinRL؟** لأن vectorbt مثبتة ومُختبرة، وتدعم backtesting vectorized

**الملف الجديد:** `scripts/python/rl_environment.py`

```
Phase 18 — Reinforcement Learning Environment
===============================================
بيئة تداول تستخدم كل مخرجات الـ Phases 1-16 كـ state space:

State Vector (50+ dimension):
- Phase 1: RSI distribution, Markov state
- Phase 2: Force field magnitude, attractor distance  
- Phase 3: Contagion alert level, cascade probability
- Phase 4: Energy state, accumulation/distribution ratio
- Phase 5: Causal chain active, feedback loop strength
- Phase 6: Decision state, opportunity score
- Phase 16: Law status, DNA archetype, knowledge graph centrality
- Phase 8: Macro stress, FX trend

Actions: BUY / SELL / HOLD / SCALE_UP / SCALE_DOWN
Reward: Risk-adjusted return (Sharpe) - drawdown penalty
```

**الخطوات:**
1. `rl_environment.py` — يبني state vector من DB
2. `rl_agent.py` — PPO agent بسيط (scipy فقط، بدون torch في المرحلة الأولى)
3. `rl_backtest.py` — backtesting بـ vectorbt

---

### 🤖 M4.2 — Meta-Learning للـ Threshold Adaptation

**المشكلة الحالية:** Phase 16 Self-Evolution يختبر thresholds بشكل sequential  
**الحل:** إضافة Bayesian Optimization لإيجاد أفضل threshold بسرعة

```python
from scipy.optimize import differential_evolution

def bayesian_threshold_search(pattern_id, direction, db):
    """
    بدلاً من اختبار 0.28, 0.293, 0.308 بالتسلسل،
    يبحث عن الـ threshold الأمثل في مساحة مستمرة
    """
    def objective(threshold):
        # اختبر هذا الـ threshold واحسب الـ precision
        n_hits = db.execute(...)
        precision = n_hits / n_total
        return -precision  # minimize negative = maximize precision
    
    result = differential_evolution(objective, bounds=[(0.05, 0.5)], 
                                    maxiter=50, seed=42)
    return result.x[0], -result.fun
```

---

## المحور الخامس: Phase 19 — Explainability & Reporting Layer
*أسبوع 6-8 | المرجع: mlfinlab + awesome-quant*

### 📊 M5.1 — SHAP Explainability للـ Laws

**المكتبة:** `shap` (مثبتة بالفعل!)  
**الهدف:** لكل إشارة buy/sell، نعرف لماذا بالضبط

```python
import shap
import lightgbm as lgb  # مثبتة!

def explain_explosion_prediction(symbol, db):
    """
    يشرح لماذا النظام يتوقع انفجاراً في هذا السهم
    مستخدماً SHAP values على نموذج LightGBM
    """
    # 1. بناء training data من counterfactual_events
    X, y = build_feature_matrix(db)
    
    # 2. تدريب LightGBM
    model = lgb.LGBMClassifier(n_estimators=200, learning_rate=0.05)
    model.fit(X, y)
    
    # 3. SHAP explanation
    explainer = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(X_today)
    
    return {
        'prediction': model.predict_proba(X_today)[0][1],
        'top_factors': [
            {'feature': feat, 'impact': shap_val, 'direction': 'UP' if shap_val > 0 else 'DOWN'}
            for feat, shap_val in sorted(zip(X.columns, shap_values), key=lambda x: abs(x[1]), reverse=True)[:5]
        ]
    }
```

**المخرج:** رسالة Telegram مثل:
```
📊 COMI — توقع انفجار UP (دقة: 23.4%)
السبب الرئيسي:
  ↑ BB Squeeze قوي (pre_bb_width=0.042, أقل من عتبة 0.169)
  ↑ Pre-momentum إيجابي (momentum_5d=+2.3%)
  ↑ Finance sector في حالة LEADERSHIP
  ↓ ADX منخفض (14.2) — تصاعد الزخم محتمل
```

---

### 📊 M5.2 — Walk-Forward Validation (WFV)

**المكتبات:** `vectorbt` + `statsmodels`  
**الهدف:** التحقق من أن القوانين المكتشفة تعمل خارج عينة التدريب  
**المرجع:** mlfinlab (Hudson & Thames) — Walk-Forward Testing

```
التقسيم:
├── Training: 2020-12 → 2024-06 (42 شهر)
├── Validation: 2024-06 → 2025-06 (12 شهر)  
└── Test (OOS): 2025-06 → 2026-05 (11 شهر) ← لا تلمسها إلا للتقييم النهائي
```

**إضافة للـ historical_validation.py (Phase 13):**
```python
def cmd_walk_forward_validation(params):
    """
    يُقسّم التاريخ إلى نوافذ متداخلة (rolling windows)
    ويختبر كل قانون في كل نافذة
    الهدف: اكتشاف الـ laws التي تتدهور (drift) بمرور الوقت
    """
```

---

## 🗓️ الجدول الزمني التفصيلي

```
الأسبوع 1 (14-21 مايو 2026):
├── M1.1: توسيع precursor discovery (6 → 50+ قانون)
├── M1.2: إصلاح sector DNA classification
└── M2.1: تثبيت: networkx, tsfresh, xgboost, pyod, tigramite

الأسبوع 2 (21-28 مايو):
├── M1.3: توسيع macro pipeline (3 → 22 مؤشر)
├── M2.2: NetworkX integration في Phase 16
└── M2.3: tigramite PCMCI في Phase 5

الأسبوع 3 (28 مايو - 4 يونيو):
├── M3.1: Phase 17 — Graph Contagion Engine
├── M3.2: Momentum Spillover Detection
└── M5.1: SHAP Explainability (تستخدم lightgbm المثبتة)

الأسبوع 4-5 (4-18 يونيو):
├── M4.1: RL Environment with vectorbt (مثبت!)
├── M4.2: Bayesian Threshold Search
└── M5.2: Walk-Forward Validation

الأسبوع 6-8 (18 يونيو - 2 يوليو):
├── Phase 11 (المفقودة): Pairs Trading + Statistical Arbitrage
├── LightGBM prediction model للـ daily signals
└── Multi-Strategy Portfolio Optimizer
```

---

## 🎯 مؤشرات النجاح (KPIs)

| المؤشر | الآن | الهدف بعد 8 أسابيع |
|--------|------|---------------------|
| عدد القوانين المكتشفة | 6 | 50+ |
| نسبة القوانين DOMINANT/ACTIVE | 0/6 (0%) | 15+ (30%) |
| دقة التنبؤ بالانفجارات | 6-12% (vs. 10.8% baseline) | 20-30% |
| Sharpe Ratio (backtest) | غير محسوب | > 1.5 |
| مؤشرات Macro | 3 | 22 |
| تمييز القطاعات | 14/20 LEADERSHIP | 4-5 archetypes موزعة |
| وقت تشغيل Full Cognition | ~120s | < 60s (parallelization) |

---

## 📦 قائمة المكتبات بالترتيب (ما ينصب أولاً)

```bash
# الأولوية 1 — فورياً (كلها تستخدم features موجودة)
pip3 install networkx tsfresh xgboost pyod

# الأولوية 2 — الأسبوع الثاني
pip3 install tigramite pandas-ta

# الأولوية 3 — عند البدء في Phase 18
# vectorbt مثبتة بالفعل — لا تحتاج شيئاً إضافياً
# lightgbm مثبتة بالفعل ✅
# shap مثبتة بالفعل ✅
```

---

## 🔗 ربط المشاريع المرجعية بالـ Phases

| المشروع المرجعي | الـ Phase التي تستفيد | طريقة الاستخدام |
|----------------|----------------------|-----------------|
| `wilsonfreitas/awesome-quant` | كل الـ Phases | مرجع للـ best practices والمكتبات |
| `jakobrunge/tigramite` | Phase 5 (Causal) | PCMCI بدلاً من Granger البسيط |
| `lcastri/causalflow` | Phase 5 + 14 | Causal Feature Selection |
| `jwwthu/GNN4Fintech` | Phase 3 + 17 | Graph-based Momentum Spillover |
| `timothewt/SP100AnalysisWithGNNs` | Phase 17 | GNN Clustering للقطاعات |
| `tsfresh` | Phase 12 + 16 | Feature extraction من OHLCV |
| `Kats (Facebook)` | Phase 12 | Anomaly Detection في الانفجارات |
| `AI4Finance/FinRL` | Phase 18 | RL Environment template |
| `vectorbt` (مثبتة) | Phase 13 + 18 | Walk-Forward + Backtesting |
| `mlfinlab` | Phase 13 | Scientific backtesting ضد Overfitting |
| `LangGraph/CrewAI` | Phase 9 | Multi-Agent Orchestration مستقبلاً |
| `Qlib (Microsoft)` | كل الـ Phases | مرجع للـ architecture فقط |

---

## ⚡ Quick Wins (يمكن تنفيذها اليوم)

### 1. تشغيل SHAP على البيانات الموجودة (30 دقيقة)
```bash
# lightgbm + shap مثبتتان — نضيف cmd_shap_explain لـ egx_analysis.py
npm run egx:advanced:shap  # موجود بالفعل!
```

### 2. تحليل الـ Knowledge Graph بـ networkx (بعد التثبيت)
```bash
pip3 install networkx
# ثم يمكن تشغيل PageRank على الـ 92 عقدة + 459 حافة
```

### 3. Walk-Forward باستخدام vectorbt (المثبتة)
```bash
# vectorbt مثبتة — فقط نضيف الكود لـ historical_validation.py
npm run egx:dhvd:run  # يُشغّل Phase 13
```

### 4. اختبار tigramite على بيانات القطاعات
```bash
pip3 install tigramite
python3 scripts/python/causal_engine.py pcmci_test '{}'
```

---

## 🚀 الأولوية المطلقة (هذا الأسبوع)

```
1. pip3 install networkx tsfresh xgboost pyod tigramite
2. إصلاح sector_dna classification (relative percentile)  
3. توسيع precursor_discovery (4 → 20+ features بـ tsfresh)
4. إضافة 5 مؤشرات macro جديدة (FRED API مجاني، لا يحتاج API key)
5. تكامل networkx في market_cognition.py Stage 5
```

---

*الهدف الاستراتيجي: تحويل EGX Intelligence من نظام يكتشف الأنماط  
إلى نظام يفهم السوق ويتنبأ بدقة مؤسسية (Institutional-grade precision)*

---
**المراجعة التالية:** 2026-06-14  
**الملف الأصلي:** `/Users/dr.husam/tradingview-mcp-jackson/docs/DEVELOPMENT_ROADMAP.md`
