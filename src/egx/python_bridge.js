/**
 * EGX Python Bridge
 * ==================
 * يُشغّل egx_analysis.py عبر subprocess ويُعيد نتائج JSON
 *
 * الفائدة عن JS خالص:
 *   - pandas groupby/agg أسرع بـ 50-100x على 75K صف
 *   - scipy.stats: KS test، Shapiro-Wilk، T-tests
 *   - signal_backtest على كامل التاريخ (لا يعتمد على cache)
 *   - تصدير CSV بـ utf-8-sig (يعمل مع Excel العربي)
 *
 * الأوامر المتاحة:
 *   full_stats         — ملخص شامل من indicators_cache
 *   return_analysis    — توزيع 72K+ عائد + KS/Shapiro tests
 *   signal_backtest    — T+1/T+3/T+5 لكل signal على 68K شمعة
 *   sector_momentum    — توزيع momentum + market breadth
 *   rolling_stats      — rolling mean/std/sharpe لسهم محدد
 *   export_csv         — تصدير جدول كـ CSV
 *
 * المالك: Dr. Husam | إنشاء: مايو 2026
 */

import { spawn }           from 'child_process';
import { join, dirname }   from 'path';
import { fileURLToPath }   from 'url';
import { existsSync }      from 'fs';

const __dirname              = dirname(fileURLToPath(import.meta.url));
const SCRIPT_PATH            = join(__dirname, '../../scripts/python/egx_analysis.py');
const LATENT_SCRIPT          = join(__dirname, '../../scripts/python/latent_engine.py');
const FORCE_FIELD_SCRIPT     = join(__dirname, '../../scripts/python/force_field_engine.py');
const PROPAGATION_SCRIPT     = join(__dirname, '../../scripts/python/propagation_engine.py');
const PYTHON_BIN             = process.env.PYTHON_BIN ?? 'python3';
const TIMEOUT_MS      = process.env.PY_TIMEOUT ? +process.env.PY_TIMEOUT : 120_000; // 2 دقيقة
const TG_TIMEOUT_MS   = process.env.TG_TIMEOUT ? +process.env.TG_TIMEOUT : 180_000; // telegram_report can be slow on cold import
const ROOT_DIR        = join(__dirname, '../..');

// ─── التحقق من وجود السكريبت ────────────────────────────────────────────

if (!existsSync(SCRIPT_PATH)) {
  console.warn(`[python_bridge] ⚠️  السكريبت غير موجود: ${SCRIPT_PATH}`);
}

// ─── الدالة الرئيسية ─────────────────────────────────────────────────────

/**
 * تشغيل أمر Python وإرجاع النتيجة كـ Object
 *
 * @param {string} command - اسم الأمر ('full_stats' | 'signal_backtest' | ...)
 * @param {Object} params  - معاملات الأمر
 * @returns {Promise<Object>} - نتيجة JSON
 *
 * @example
 * const stats  = await runPythonAnalysis('full_stats');
 * const bt     = await runPythonAnalysis('signal_backtest', { rsi_threshold: 30 });
 * const sector = await runPythonAnalysis('sector_momentum');
 */
export async function runPythonAnalysis(command, params = {}) {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify({ command, params });
    const child   = spawn(PYTHON_BIN, [SCRIPT_PATH], {
      stdio: ['pipe', 'pipe', 'pipe'],
    });

    let stdout = '';
    let stderr = '';

    child.stdout.on('data', chunk => { stdout += chunk.toString(); });
    child.stderr.on('data', chunk => { stderr += chunk.toString(); });

    const timer = setTimeout(() => {
      child.kill('SIGTERM');
      reject(new Error(`Python timeout بعد ${TIMEOUT_MS / 1000}s — command: ${command}`));
    }, TIMEOUT_MS);

    child.on('close', code => {
      clearTimeout(timer);
      if (code !== 0 && !stdout.trim()) {
        return reject(new Error(`Python process خرج بـ code ${code}: ${stderr.slice(-300)}`));
      }
      try {
        const result = JSON.parse(stdout.trim());
        if (result.error) {
          // نُرجع الخطأ كـ object لا كـ exception (يسمح بـ fallback في المستدعي)
          resolve({ success: false, error: result.error, raw: result });
        } else {
          resolve({ success: true, ...result });
        }
      } catch (e) {
        reject(new Error(`فشل parsing JSON من Python: ${e.message}\nStdout: ${stdout.slice(0, 200)}`));
      }
    });

    child.stdin.write(payload);
    child.stdin.end();
  });
}

// ─── Shortcut functions ──────────────────────────────────────────────────

/**
 * ملخص شامل من indicators_cache (سريع ~1s)
 */
export async function pythonFullStats() {
  return runPythonAnalysis('full_stats');
}

/**
 * تحليل توزيع العوائد الكامل + KS test + Shapiro (~3s)
 */
export async function pythonReturnAnalysis(limit = 75000) {
  return runPythonAnalysis('return_analysis', { limit });
}

/**
 * Signal backtest على كامل التاريخ (~10-15s)
 * الأقوى: RSI/BB/OBV محسوبة مباشرة بـ pandas
 *
 * @param {number} rsiThreshold - حد RSI (افتراضي 35)
 */
export async function pythonSignalBacktest(rsiThreshold = 35) {
  return runPythonAnalysis('signal_backtest', {
    rsi_threshold: rsiThreshold,
    limit:         75000,
  });
}

/**
 * تحليل momentum السوق + market breadth (~2s)
 */
export async function pythonSectorMomentum() {
  return runPythonAnalysis('sector_momentum');
}

/**
 * Rolling stats لسهم محدد (~1s)
 * @param {string} symbol - رمز السهم
 * @param {number} window - نافذة rolling (افتراضي 20)
 */
export async function pythonRollingStats(symbol, window = 20) {
  return runPythonAnalysis('rolling_stats', { symbol, window });
}

/**
 * تصدير جدول إلى CSV
 * @param {string} table    - اسم الجدول
 * @param {string} output   - مسار الملف
 */
export async function pythonExportCSV(table = 'indicators_cache', output = '/tmp/egx_export.csv') {
  return runPythonAnalysis('export_csv', { table, output, limit: 200000 });
}

// ─── Discovery & ML shortcuts ────────────────────────────────────────────

/**
 * Grid Search موازي: RSI × ADX × Hold Days على 73K شمعة (~20-30s)
 * يُعيد أفضل 20 تركيبة بـ Sharpe + WR
 */
export async function pythonParamSweep() {
  return runPythonAnalysis('param_sweep', {});
}

/**
 * Walk-Forward Validation: 5 نوافذ train/test موازية (~10s)
 * يتحقق أن الاستراتيجية لم تكن overfit على بيانات التاريخ
 */
export async function pythonWalkForward() {
  return runPythonAnalysis('walk_forward', {});
}

/**
 * ML Signal: Random Forest + HistGradientBoosting على 66K عينة (~60s)
 * يُعيد feature importance + CV precision + عدد الإشارات عالية الثقة
 *
 * @param {number} targetPct  - هدف العائد T+5 (افتراضي 3%)
 * @param {number} nSplits    - عدد CV folds (افتراضي 5)
 */
export async function pythonMLSignal(targetPct = 3.0, nSplits = 5) {
  return runPythonAnalysis('ml_signal', { target_pct: targetPct, n_splits: nSplits });
}

/**
 * EGX Patterns: اكتشاف patterns خاصة بالسوق المصري (~5s)
 * Circuit Breaker | Gap Fill | Earnings Season | Thin Volume | Ramadan | Day-of-Week
 *
 * @param {number} cbThreshold - حد Circuit Breaker (افتراضي 9%)
 */
export async function pythonEgxPatterns(cbThreshold = 9.0) {
  return runPythonAnalysis('egx_patterns', { cb_threshold: cbThreshold });
}

// ─── Sector & Pairs shortcuts ────────────────────────────────────────────

/**
 * Sector Rotation Alpha — يُصنّف القطاعات: LEADING/IMPROVING/LAGGING/DECLINING (~5s)
 * يجمع: Relative Strength + Momentum Acceleration
 */
export async function pythonSectorRotation() {
  return runPythonAnalysis('sector_rotation', {});
}

/**
 * Pairs Trading Candidates — Engle-Granger Cointegration test على أسهم EGX (~15-30s)
 * يُعيد أزواج متكاملة مع z-score وإشارة LONG/SHORT spread
 *
 * @param {number} minBars   - عدد الشمعات الدنية (افتراضي 120)
 * @param {number} minCorr   - حد الـ correlation (افتراضي 0.65)
 * @param {number} cointPval - p-value حد الـ cointegration (افتراضي 0.10)
 */
export async function pythonPairsTrading(minBars = 120, minCorr = 0.65, cointPval = 0.10) {
  return runPythonAnalysis('pairs_trading', {
    min_bars: minBars, min_corr: minCorr, coint_pval: cointPval,
  });
}

// ─── Advanced Analysis shortcuts ─────────────────────────────────────────

/**
 * SHAP Values لنموذج Random Forest مع features متقدمة:
 * Garman-Klass Vol + Amihud Illiquidity + ATR Rank + BB Width (~90s)
 * أدق بكثير من feature_importances_ العادية
 *
 * @param {number} targetPct  - هدف العائد T+5 (افتراضي 3%)
 * @param {number} sampleSize - حجم العينة (افتراضي 8000)
 */
export async function pythonShapAnalysis(targetPct = 3.0, sampleSize = 8000) {
  return runPythonAnalysis('shap_analysis', { target_pct: targetPct, sample_size: sampleSize });
}

/**
 * Regime Detection لكل سهم + aggregate market regime (~15s)
 * Regimes: TRENDING_UP | TRENDING_DOWN | RANGING | HIGH_VOL | NEUTRAL
 * يُساعد في تفادي Trend-Following في RANGING market
 */
export async function pythonRegimeDetection() {
  return runPythonAnalysis('regime_detection', {});
}

/**
 * Ensemble Signal — Meta-signal يجمع Rules(50%) + ML_Proxy(35%) + Calendar(15%) (~3s)
 * Output: composite_score (0-100), STRONG_BUY / BUY / WATCH / NEUTRAL
 *
 * @param {number} minConfidence - الحد الأدنى للثقة (افتراضي 0.55)
 */
export async function pythonEnsembleSignal(minConfidence = 0.55) {
  return runPythonAnalysis('ensemble_signal', { min_confidence: minConfidence });
}

/**
 * Active Universe Filter — يحسب مقاييس السيولة من OHLCV (~5s)
 * يُصنّف كل سهم: LIQUID / ILLIQUID / THIN / DEAD
 * يُقترح EGX_UNIVERSE_CORE المحدّث
 *
 * @param {number} minValue - الحد الأدنى لقيمة التداول اليومية EGP (افتراضي 500,000)
 */
export async function pythonActiveUniverse(minValue = 500_000) {
  return runPythonAnalysis('active_universe', { min_value: minValue });
}

// ─── Macro Data ──────────────────────────────────────────────────────────────

/**
 * بيانات الاقتصاد الكلي المصري من APIs المجانية (~5s)
 * USD/EGP + Egypt Inflation + CBE Lending Rate
 * يحفظ تلقائياً في جدول macro_data بالـ SQLite
 */
export async function pythonMacroData() {
  return runPythonAnalysis('macro_data', {});
}

/**
 * Event-Based Engine — كشف تسلسل الأحداث لكل سهم (Path-Dependent)
 * يُعيد HIGH_PROB_REVERSAL / LIKELY_REVERSAL / POSSIBLE_REVERSAL مع Hybrid Regime v2
 */
export async function eventSignals({ minScore = 45, topN = 20, onlyGold = false } = {}) {
  return runPythonAnalysis('event_signals', {
    min_score:  minScore,
    top_n:      topN,
    only_gold:  onlyGold,
  });
}

/**
 * Adaptive Market Memory System — ذاكرة السوق التكيُّفية
 *
 * Bayesian Beta-Binomial posteriors مع Temporal Decay.
 * بدلاً من الاحتماليات الثابتة، يُحافظ على:
 *   - posterior_p_tr + CI_80  (ليس مجرد point estimate)
 *   - drift: STRENGTHENING / WEAKENING / STABLE  (هل الـ edge ينمو أم يتدهور؟)
 *   - failure_memory: IMPROVING / NORMAL / CAUTION / DANGER
 *   - hierarchical_adjustments: regime × duration × breadth × ATR × sector
 *   - regime_shifts: هل بنية السوق تغيَّرت مؤخراً؟
 *   - market physics: exhaustion signature + volatility release + liquidity absorption
 *
 * @param {number} fwdBars      - نافذة التحقق (افتراضي 5)
 * @param {number} decayLambda  - معدل التدهور الزمني (افتراضي 0.7 = 50% كل سنة)
 * @param {number} recentDays   - تعريف "حديث" بالأيام (افتراضي 90)
 */
export async function pythonAdaptiveMemory(fwdBars = 5, decayLambda = 0.7, recentDays = 90) {
  return runPythonAnalysis('adaptive_memory', {
    fwd_bars:    fwdBars,
    decay_lambda: decayLambda,
    recent_days: recentDays,
  });
}

/**
 * Conditional Market State Evolution — نموذج الانتقال الشرطي متعدد الأبعاد
 * P(REVERSAL | state, regime, sector, ATR_tier, liquidity, duration, breadth)
 *
 * يتضمن:
 *   - Duration Surface       — متى يصل الـ exhaustion threshold؟
 *   - Sector Conditionality  — هل الانتقال ثابت عبر القطاعات؟
 *   - Regime Stability       — هل يصمد عبر BULL/DOWN/CRASH؟
 *   - Failure Engine         — تشريح الانعكاسات الفاشلة
 *   - Full 5D Surface        — المصفوفة الكاملة
 *   - Self-Learning Loop     — اكتشاف المتغيرات الخفية
 *
 * @param {number} fwdBars     - نافذة التحقق (افتراضي 5)
 * @param {number} trueRevThr  - عتبة الانعكاس الحقيقي (افتراضي 0.03 = 3%)
 */
export async function pythonConditionalTransitions(fwdBars = 5, trueRevThr = 0.03) {
  return runPythonAnalysis('conditional_transitions', {
    fwd_bars:      fwdBars,
    true_rev_thr:  trueRevThr,
  });
}

/**
 * Market State Transition Engine — Markov Chain على 68K+ شمعة
 * يُعيد:
 *   - مصفوفة انتقال الحالات P(next_state | current_state)
 *   - P(TRUE_REVERSAL | state + market_regime)
 *   - Discriminant conditions (GOLDEN_SEQUENCE → 51-60% TR)
 *   - مدة كل حالة (state duration)
 *   - الحالة الحالية لكل سهم
 *
 * @param {number} fwdBars - نافذة التحقق الأمامية (افتراضي 5)
 */
export async function pythonStateTransitions(fwdBars = 5) {
  return runPythonAnalysis('state_transitions', { fwd_bars: fwdBars });
}

/**
 * Evolving Market Structure Engine — خريطة إدراكية حيّة لـ EGX
 *
 * يتتبع:
 *   - Alpha Decay per edge (3 أفق: short/medium/long)
 *   - Structural Drift (transition matrix drift + regime frequency shift)
 *   - Failure Typing (continuation trap / dead cat / drift failure / fake reversal)
 *   - Adaptive Cognitive Map (P_TR مُجمَّع بأوزان تكيُّفية)
 *   - Market Physics (pressure / exhaustion timing / behavioral persistence)
 *   - Self-Evolution Loop (alerts, suppressed/promoted edges, structure stability)
 *
 * @param {number} fwdBars     - نافذة التحقق (افتراضي 5)
 * @param {number} shortDays   - تعريف "قصير المدى" (افتراضي 30 يوم)
 * @param {number} mediumDays  - تعريف "متوسط المدى" (افتراضي 180 يوم)
 * @param {number} decayLambda - معدل التدهور الزمني للأوزان (افتراضي 0.7)
 */
export async function pythonEvolvingStructure(fwdBars = 5, shortDays = 30, mediumDays = 180, decayLambda = 0.7) {
  return runPythonAnalysis('evolving_structure', {
    fwd_bars:     fwdBars,
    short_days:   shortDays,
    medium_days:  mediumDays,
    decay_lambda: decayLambda,
  });
}

export async function pythonMarketEvolution(fwdBars = 5, shortDays = 30, mediumDays = 180) {
  return runPythonAnalysis('market_evolution', {
    fwd_bars:    fwdBars,
    short_days:  shortDays,
    medium_days: mediumDays,
  });
}

export async function pythonMacroRegime(maxAgeHours = 168) {
  return runPythonAnalysis('macro_regime', { max_age_hours: maxAgeHours });
}

// ═══════════════════════════════════════════════════════════════════════════
// LATENT ENGINE BRIDGE — يُشغّل latent_engine.py
// ═══════════════════════════════════════════════════════════════════════════

/**
 * تشغيل أمر من latent_engine.py
 */
export async function runLatentAnalysis(command, params = {}) {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify({ command, params });
    const child   = spawn(PYTHON_BIN, [LATENT_SCRIPT], {
      stdio: ['pipe', 'pipe', 'pipe'],
    });

    let stdout = '';
    let stderr = '';
    child.stdout.on('data', chunk => { stdout += chunk.toString(); });
    child.stderr.on('data', chunk => { stderr += chunk.toString(); });

    const timer = setTimeout(() => {
      child.kill('SIGTERM');
      reject(new Error(`Latent engine timeout بعد ${TIMEOUT_MS/1000}s — command: ${command}`));
    }, TIMEOUT_MS);

    child.on('close', code => {
      clearTimeout(timer);
      if (code !== 0 && !stdout.trim()) {
        return reject(new Error(`Latent engine code ${code}: ${stderr.slice(-300)}`));
      }
      try {
        const result = JSON.parse(stdout.trim());
        resolve(result.error ? { success: false, error: result.error, raw: result } : { success: true, ...result });
      } catch (e) {
        reject(new Error(`JSON parse error: ${e.message}\nStdout: ${stdout.slice(0, 200)}`));
      }
    });

    child.stdin.write(payload);
    child.stdin.end();
  });
}

/**
 * 6-force behavioral decomposition of all stocks NOW (~1s)
 */
export async function pythonBehavioralForces() {
  return runLatentAnalysis('behavioral_forces', {});
}

/**
 * P(TR) evolution by state duration (~8s)
 * @param {number} horizon - forward bars (default 3)
 */
export async function pythonDurationAnalysis(horizon = 3) {
  return runLatentAnalysis('duration_analysis', { horizon });
}

/**
 * Sector-conditioned Markov transition matrices (~10s)
 */
export async function pythonSectorMarkov() {
  return runLatentAnalysis('sector_markov', {});
}

/**
 * PCA latent compression of behavioral space (~2s)
 */
export async function pythonLatentCompress() {
  return runLatentAnalysis('latent_compress', {});
}

/**
 * Behavioral invariants — cross-time stable edges (~10s)
 */
export async function pythonInvariantDiscovery(horizon = 3) {
  return runLatentAnalysis('invariant_discovery', { horizon });
}

/**
 * Failure precursor analysis — what distinguishes TR from failed reversal (~1s)
 */
export async function pythonFailurePrecursors(horizon = 5) {
  return runLatentAnalysis('failure_precursors', { horizon });
}

/**
 * Temporal stability test — rolling window edge stability (~15s)
 */
export async function pythonTemporalStability(windowDays = 180) {
  return runLatentAnalysis('temporal_stability', { window_days: windowDays });
}

/**
 * Full autonomous quant research loop — runs ALL analyses and synthesizes (~30s)
 * Returns 13-section structured research report
 */
export async function pythonQuantLoop() {
  return runLatentAnalysis('quant_loop', {});
}

// ═══════════════════════════════════════════════════════════════════════════
// FORCE FIELD ENGINE BRIDGE — يُشغّل force_field_engine.py
// ═══════════════════════════════════════════════════════════════════════════

/**
 * تشغيل أمر من force_field_engine.py
 */
export async function runForceFieldAnalysis(command, params = {}) {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify({ command, params });
    const child   = spawn(PYTHON_BIN, [FORCE_FIELD_SCRIPT], {
      stdio: ['pipe', 'pipe', 'pipe'],
    });

    let stdout = '';
    let stderr = '';
    child.stdout.on('data', chunk => { stdout += chunk.toString(); });
    child.stderr.on('data', chunk => { stderr += chunk.toString(); });

    const timer = setTimeout(() => {
      child.kill('SIGTERM');
      reject(new Error(`Force field engine timeout بعد ${TIMEOUT_MS/1000}s — command: ${command}`));
    }, TIMEOUT_MS);

    child.on('close', code => {
      clearTimeout(timer);
      if (code !== 0 && !stdout.trim()) {
        return reject(new Error(`Force field engine code ${code}: ${stderr.slice(-300)}`));
      }
      try {
        const result = JSON.parse(stdout.trim());
        resolve(result.error ? { success: false, error: result.error, raw: result } : { success: true, ...result });
      } catch (e) {
        reject(new Error(`JSON parse error: ${e.message}\nStdout: ${stdout.slice(0, 200)}`));
      }
    });

    child.stdin.write(payload);
    child.stdin.end();
  });
}

/**
 * Current 9-force field snapshot from indicators_cache (~2s)
 * Returns force vectors, archetypes, and top stocks per archetype
 */
export async function pythonForceFieldNow() {
  return runForceFieldAnalysis('force_field_now', {});
}

/**
 * Force coupling matrix — P(TR|f1_high AND f2_high) vs individual forces (~15s)
 * Classifies each pair: SUPER_ADDITIVE / REINFORCING / INDEPENDENT / SUPPRESSION / CANCELLATION
 */
export async function pythonForceInteractions() {
  return runForceFieldAnalysis('force_interactions', {});
}

/**
 * Force evolution dynamics — acceleration, decay, half-life per force (~10s)
 */
export async function pythonForceEvolution() {
  return runForceFieldAnalysis('force_evolution', {});
}

/**
 * Market memory analysis — alpha decay via 1st/2nd/3rd+ occurrence P(TR) (~10s)
 * Detects if forces lose predictability after repeated activation
 */
export async function pythonMarketMemory() {
  return runForceFieldAnalysis('market_memory', {});
}

/**
 * Failure physics — Cohen's d discriminant analysis between true and failed reversals (~8s)
 * Returns blocking force, failure type, and discriminant signatures
 */
export async function pythonFailurePhysics() {
  return runForceFieldAnalysis('failure_physics', {});
}

/**
 * Force attractors — K-Means(k=6) on 9D force space to find stability basins (~5s)
 * Requires numpy. Returns attractor centers, sizes, and types
 */
export async function pythonForceAttractors() {
  return runForceFieldAnalysis('force_attractors', {});
}

/**
 * Full force field analysis — runs all sub-analyses and synthesizes (~60s)
 * Returns: force_field_now + interactions + evolution + memory + failure_physics + attractors
 */
export async function pythonForceFieldFull() {
  return runForceFieldAnalysis('force_field_full', {});
}

// ═══════════════════════════════════════════════════════════════════════════
// PROPAGATION ENGINE BRIDGE — يُشغّل propagation_engine.py
// ═══════════════════════════════════════════════════════════════════════════

/**
 * تشغيل أمر من propagation_engine.py
 */
export async function runPropagationAnalysis(command, params = {}) {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify({ command, params });
    const child   = spawn(PYTHON_BIN, [PROPAGATION_SCRIPT], {
      stdio: ['pipe', 'pipe', 'pipe'],
    });

    let stdout = '';
    let stderr = '';
    child.stdout.on('data', chunk => { stdout += chunk.toString(); });
    child.stderr.on('data', chunk => { stderr += chunk.toString(); });

    const timer = setTimeout(() => {
      child.kill('SIGTERM');
      reject(new Error(`Propagation engine timeout بعد ${TIMEOUT_MS/1000}s — command: ${command}`));
    }, TIMEOUT_MS);

    child.on('close', code => {
      clearTimeout(timer);
      if (code !== 0 && !stdout.trim()) {
        return reject(new Error(`Propagation engine code ${code}: ${stderr.slice(-300)}`));
      }
      try {
        const result = JSON.parse(stdout.trim());
        resolve(result.error ? { success: false, error: result.error, raw: result } : { success: true, ...result });
      } catch (e) {
        reject(new Error(`JSON parse error: ${e.message}\nStdout: ${stdout.slice(0, 200)}`));
      }
    });

    child.stdin.write(payload);
    child.stdin.end();
  });
}

/**
 * Current propagation snapshot from indicators_cache (~2s)
 * Returns force sources, sector coordination, transmission alerts
 */
export async function pythonPropagationNow() {
  return runPropagationAnalysis('propagation_now', {});
}

/**
 * Historical contagion chains — P(sector B follows A | lag L) (~25s)
 * Identifies which sectors spread stress to which, with transmission probabilities
 */
export async function pythonContagionChains() {
  return runPropagationAnalysis('contagion_chains', {});
}

/**
 * Sector transmission matrix — LEAD / FOLLOW / ABSORBER / AMPLIFIER roles (~15s)
 * Computes cross-sector lag structure and market leadership ranking
 */
export async function pythonSectorTransmission() {
  return runPropagationAnalysis('sector_transmission', {});
}

/**
 * Instability cascade detection — historical panic propagation events (~15s)
 * Returns cascade triggers, breakers, speed, and severity distribution
 */
export async function pythonInstabilityCascades() {
  return runPropagationAnalysis('instability_cascades', {});
}

/**
 * Stock role classification — SOURCE/AMPLIFIER/ABSORBER/ANCHOR/REACTOR/GENERATOR (~12s)
 * Scores every stock on 4 dimensions: lead, absorb, anchor, amplify
 */
export async function pythonRoleClassification() {
  return runPropagationAnalysis('role_classification', {});
}

/**
 * Force diffusion analysis — half-life, propagation radius, amplification (~15s)
 * Measures how fast and far stress diffuses through the sector network
 */
export async function pythonDiffusionAnalysis() {
  return runPropagationAnalysis('diffusion_analysis', {});
}

/**
 * Regime-conditioned networks — how topology changes across CRISIS/STRESS/CALM (~20s)
 * Builds separate propagation structures per market regime
 */
export async function pythonRegimeNetworks() {
  return runPropagationAnalysis('regime_networks', {});
}

/**
 * Full propagation analysis — runs all 7 sub-analyses (~90s)
 */
export async function pythonPropagationFull() {
  return runPropagationAnalysis('propagation_full', {});
}

// ─── Health check ────────────────────────────────────────────────────────

// ─── Phase 4: Energy Flow Engine ─────────────────────────────────────────────

const ENERGY_SCRIPT = join(__dirname, '../../scripts/python/energy_flow_engine.py');

/**
 * تشغيل أمر من energy_flow_engine.py
 */
export async function runEnergyAnalysis(command, params = {}) {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify({ command, params });
    const child   = spawn(PYTHON_BIN, [ENERGY_SCRIPT], {
      stdio: ['pipe', 'pipe', 'pipe'],
    });

    let stdout = '';
    let stderr = '';
    child.stdout.on('data', chunk => { stdout += chunk.toString(); });
    child.stderr.on('data', chunk => { stderr += chunk.toString(); });

    const timer = setTimeout(() => {
      child.kill('SIGTERM');
      reject(new Error(`Energy engine timeout بعد ${TIMEOUT_MS/1000}s — command: ${command}`));
    }, TIMEOUT_MS);

    child.on('close', code => {
      clearTimeout(timer);
      if (code !== 0 && !stdout.trim()) {
        return reject(new Error(`Energy engine code ${code}: ${stderr.slice(-300)}`));
      }
      try {
        const result = JSON.parse(stdout.trim());
        resolve(result.error ? { success: false, error: result.error, raw: result } : { success: true, ...result });
      } catch (e) {
        reject(new Error(`JSON parse error: ${e.message}\nStdout: ${stdout.slice(0, 200)}`));
      }
    });

    child.stdin.write(payload);
    child.stdin.end();
  });
}

/** Current energy state from indicators_cache (~2s) */
export async function pythonEnergyNow()           { return runEnergyAnalysis('energy_now', {}); }

/** Sector-to-sector energy flow analysis (~15s) */
export async function pythonEnergyFlow()          { return runEnergyAnalysis('energy_flow', {}); }

/** Energy buildup zones: coiled springs & instability reservoirs (~15s) */
export async function pythonEnergyAccumulation()  { return runEnergyAnalysis('energy_accumulation', {}); }

/** Energy transformation dynamics — Markov transition matrix (~20s) */
export async function pythonEnergyTransformation(){ return runEnergyAnalysis('energy_transformation', {}); }

/** Energy half-life, persistence decay, release probability (~15s) */
export async function pythonEnergyPersistence()   { return runEnergyAnalysis('energy_persistence', {}); }

/** Regime-dependent energy dynamics: CRISIS/STRESS/BULL/CALM (~20s) */
export async function pythonRegimeEnergy()        { return runEnergyAnalysis('regime_energy', {}); }

/** Why did energy fail to release? Structural dampeners (~15s) */
export async function pythonFailurePhysicsEnergy(){ return runEnergyAnalysis('failure_physics', {}); }

/** Universal energy laws and invariant thresholds (~20s) */
export async function pythonEnergyInvariants()    { return runEnergyAnalysis('energy_invariants', {}); }

/** Complete energy thermodynamics report (~2min) */
export async function pythonEnergyFull()          { return runEnergyAnalysis('energy_full', {}); }

// ═══════════════════════════════════════════════════════════════════════════
// CAUSAL ENGINE BRIDGE — يُشغّل causal_engine.py (Phase 5)
// ═══════════════════════════════════════════════════════════════════════════

const CAUSAL_SCRIPT = join(__dirname, '../../scripts/python/causal_engine.py');

/**
 * تشغيل أمر من causal_engine.py
 */
export async function runCausalAnalysis(command, params = {}) {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify({ command, params });
    const child   = spawn(PYTHON_BIN, [CAUSAL_SCRIPT], {
      stdio: ['pipe', 'pipe', 'pipe'],
    });

    let stdout = '';
    let stderr = '';
    child.stdout.on('data', chunk => { stdout += chunk.toString(); });
    child.stderr.on('data', chunk => { stderr += chunk.toString(); });

    const timer = setTimeout(() => {
      child.kill('SIGTERM');
      reject(new Error(`Causal engine timeout بعد ${TIMEOUT_MS/1000}s — command: ${command}`));
    }, TIMEOUT_MS);

    child.on('close', code => {
      clearTimeout(timer);
      if (code !== 0 && !stdout.trim()) {
        return reject(new Error(`Causal engine code ${code}: ${stderr.slice(-300)}`));
      }
      try {
        const result = JSON.parse(stdout.trim());
        resolve(result.error ? { success: false, error: result.error, raw: result } : { success: true, ...result });
      } catch (e) {
        reject(new Error(`JSON parse error: ${e.message}\nStdout: ${stdout.slice(0, 200)}`));
      }
    });

    child.stdin.write(payload);
    child.stdin.end();
  });
}

/** Current causal state from indicators_cache — active events + predictions (~2s) */
export async function pythonCausalNow()         { return runCausalAnalysis('causal_now', {}); }

/** Multi-step causal chains: A→B→C patterns with conditional lift (~20s) */
export async function pythonCausalChains()      { return runCausalAnalysis('causal_chains', {}); }

/** Feedback loop detection: A→B and B→A cycles, amplifying vs dampening (~15s) */
export async function pythonFeedbackLoops()     { return runCausalAnalysis('feedback_loops', {}); }

/** Temporal memory: how long causal effects persist after event onset (~15s) */
export async function pythonTemporalMemory()    { return runCausalAnalysis('temporal_memory', {}); }

/** Sector causal roles: TRIGGER/PROPAGATOR/REACTOR/AMPLIFIER/ABSORBER (~15s) */
export async function pythonSectorCausalRoles() { return runCausalAnalysis('sector_causal_roles', {}); }

/** Causal failure analysis: why chains broke, structural blockers (~15s) */
export async function pythonCausalFailure()     { return runCausalAnalysis('causal_failure', {}); }

/** Regime-dependent causal graphs: separate BULL/STRESS/CRISIS structures (~20s) */
export async function pythonRegimeCausality()   { return runCausalAnalysis('regime_causality', {}); }

/** Universal causal invariants: laws that hold across all regimes (~15s) */
export async function pythonCausalInvariants()  { return runCausalAnalysis('causal_invariants', {}); }

/** Complete causal engine report — all 8 sub-analyses (~2min) */
export async function pythonCausalFull()        { return runCausalAnalysis('causal_full', {}); }

// ═══════════════════════════════════════════════════════════════════════════
// DECISION ENGINE BRIDGE — يُشغّل decision_engine.py (Phase 6)
// ═══════════════════════════════════════════════════════════════════════════

const DECISION_SCRIPT = join(__dirname, '../../scripts/python/decision_engine.py');

/**
 * تشغيل أمر من decision_engine.py
 */
export async function runDecisionAnalysis(command, params = {}) {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify({ command, params });
    const child   = spawn(PYTHON_BIN, [DECISION_SCRIPT], {
      stdio: ['pipe', 'pipe', 'pipe'],
    });

    let stdout = '';
    let stderr = '';
    child.stdout.on('data', chunk => { stdout += chunk.toString(); });
    child.stderr.on('data', chunk => { stderr += chunk.toString(); });

    const timer = setTimeout(() => {
      child.kill('SIGTERM');
      reject(new Error(`Decision engine timeout بعد ${TIMEOUT_MS/1000}s — command: ${command}`));
    }, TIMEOUT_MS);

    child.on('close', code => {
      clearTimeout(timer);
      if (code !== 0 && !stdout.trim()) {
        return reject(new Error(`Decision engine code ${code}: ${stderr.slice(-300)}`));
      }
      try {
        const result = JSON.parse(stdout.trim());
        resolve(result.error ? { success: false, error: result.error, raw: result } : { success: true, ...result });
      } catch (e) {
        reject(new Error(`JSON parse error: ${e.message}\nStdout: ${stdout.slice(0, 200)}`));
      }
    });

    child.stdin.write(payload);
    child.stdin.end();
  });
}

/** Current decision state + top opportunities from indicators_cache (~2s) */
export async function pythonDecisionNow()         { return runDecisionAnalysis('decision_now', {}); }

/** Deep opportunity scan: full EBP decomposition ranked by score (~20s) */
export async function pythonOpportunityScan()     { return runDecisionAnalysis('opportunity_scan', {}); }

/** Portfolio optimization: Kelly sizing + diversification constraints (~20s) */
export async function pythonPortfolioOptimize()   { return runDecisionAnalysis('portfolio_optimize', {}); }

/** Uncertainty map: confidence decay, instability, regime ambiguity per sector (~10s) */
export async function pythonUncertaintyMap()      { return runDecisionAnalysis('uncertainty_map', {}); }

/** Regime-dependent decision policies + qualifying stocks under current regime (~15s) */
export async function pythonRegimeDecisions()     { return runDecisionAnalysis('regime_decisions', {}); }

/** Inaction intelligence: when NOT to act — detect noise/panic/conflict (~5s) */
export async function pythonInactionAnalysis()    { return runDecisionAnalysis('inaction_analysis', {}); }

/** Why did good setups fail? Discriminant analysis of decision failures (~20s) */
export async function pythonDecisionFailure()     { return runDecisionAnalysis('failure_analysis', {}); }

/** Adaptive threshold calibration: optimal EBP/uncertainty cutoffs from data (~20s) */
export async function pythonAdaptiveThresholds()  { return runDecisionAnalysis('adaptive_thresholds', {}); }

/** Complete decision intelligence report — all 8 sub-analyses (~2min) */
export async function pythonDecisionFull()        { return runDecisionAnalysis('decision_full', {}); }

// ─── Phase 7: Self-Evolving Market Intelligence Engine ───────────────────

const EVOLUTION_SCRIPT = join(__dirname, '../../scripts/python/evolution_engine.py');

export async function runEvolutionAnalysis(command, params = {}) {
  return new Promise((resolve, reject) => {
    const child   = spawn(PYTHON_BIN, [EVOLUTION_SCRIPT, command], {
      cwd:   join(__dirname, '../../'),
      stdio: ['pipe', 'pipe', 'pipe'],
      env:   { ...process.env },
    });
    let stdout = '', stderr = '';
    child.stdin.write(JSON.stringify(params));
    child.stdin.end();
    child.stdout.on('data', d => stdout += d.toString());
    child.stderr.on('data', d => stderr += d.toString());
    child.on('close', code => {
      try {
        resolve(JSON.parse(stdout));
      } catch {
        reject(new Error(`evolution_engine parse error [${command}]: ${stderr.slice(0,300)}`));
      }
    });
    child.on('error', reject);
  });
}

/** Phase 7 — System health dashboard: trust level of all 6 phases (~7s) */
export async function pythonMetaStatus()         { return runEvolutionAnalysis('meta_status', {}); }

/** Phase 7 — Model decay detection: alpha drift, invariant breakdown, regime drift (~8s) */
export async function pythonDecayScan()          { return runEvolutionAnalysis('decay_scan', {}); }

/** Phase 7 — Auto-generate new market hypotheses and challenge prior assumptions (~10s) */
export async function pythonHypothesisGen()      { return runEvolutionAnalysis('hypothesis_gen', {}); }

/** Phase 7 — Multi-architecture competition: which regime classifier wins? (~9s) */
export async function pythonArchCompete()        { return runEvolutionAnalysis('arch_compete', {}); }

/** Phase 7 — Taxonomy audit: event firing rates, force redundancy, state reachability (~6s) */
export async function pythonTaxonomyAudit()      { return runEvolutionAnalysis('taxonomy_audit', {}); }

/** Phase 7 — Regime-specific phase reliability: which phases trust per regime (~10s) */
export async function pythonRegimeIntelligence() { return runEvolutionAnalysis('regime_intelligence', {}); }

/** Phase 7 — Evolutionary memory: read/write architecture history log (~0.1s) */
export async function pythonEvolutionMemory()    { return runEvolutionAnalysis('evolution_memory', {}); }

/** Phase 7 — Meta-decision: TRUST / REDUCE_CONFIDENCE / REBUILD / INVALIDATE (~5s) */
export async function pythonMetaDecision()       { return runEvolutionAnalysis('meta_decision', {}); }

/** Phase 7 — Self-rewrite: concrete redesign proposals, merge/split/new-dim candidates (~8s) */
export async function pythonSelfRewrite()        { return runEvolutionAnalysis('self_rewrite', {}); }

/** Phase 7 — Full evolution report: all 9 analyses synthesized (~60s) */
export async function pythonEvolutionFull()      { return runEvolutionAnalysis('evolution_full', {}); }

// ─── Phase 8: World–Market Coupling Engine ───────────────────────────────

const COUPLING_SCRIPT = join(__dirname, '../../scripts/python/world_coupling_engine.py');

export async function runWorldCoupling(command, params = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(PYTHON_BIN, [COUPLING_SCRIPT, command], {
      cwd:   join(__dirname, '../../'),
      stdio: ['pipe', 'pipe', 'pipe'],
      env:   { ...process.env },
    });
    let stdout = '', stderr = '';
    child.stdin.write(JSON.stringify(params));
    child.stdin.end();
    child.stdout.on('data', d => { stdout += d.toString(); });
    child.stderr.on('data', d => { stderr += d.toString(); });
    child.on('close', code => {
      try { resolve(JSON.parse(stdout)); }
      catch { reject(new Error(`world_coupling_engine parse error [${command}]: ${stderr.slice(0, 300)}`)); }
    });
    child.on('error', reject);
  });
}

/** Phase 8 — Live coupling snapshot: all macro dimensions (~5s) */
export async function pythonCouplingNow()        { return runWorldCoupling('coupling_now', {}); }

/** Phase 8 — FX stress → sector sensitivity + behavioural modifiers (~6s) */
export async function pythonFxImpact()           { return runWorldCoupling('fx_impact', {}); }

/** Phase 8 — Macro regime detection + behavioural effects on all phases (~6s) */
export async function pythonWorldMacroRegimes()  { return runWorldCoupling('macro_regimes', {}); }

/** Phase 8 — Liquidity cycle → propagation speed, recovery lag, energy drain (~5s) */
export async function pythonLiquidityCycle()     { return runWorldCoupling('liquidity_cycle', {}); }

/** Phase 8 — Sector-specific macro coupling maps: beta, sensitivity, direction (~8s) */
export async function pythonSectorCoupling()     { return runWorldCoupling('sector_coupling', {}); }

/** Phase 8 — External shock memory: half-life, decay pattern, frequency trend (~5s) */
export async function pythonShockMemory()        { return runWorldCoupling('shock_memory', {}); }

/** Phase 8 — Cross-sector contagion scan: synchronisation, imported stress (~5s) */
export async function pythonContagionScan()      { return runWorldCoupling('contagion_scan', {}); }

/** Phase 8 — Coupling stability: structural breaks in macro–market relationships (~6s) */
export async function pythonCouplingStability()  { return runWorldCoupling('coupling_stability', {}); }

/** Phase 8 — Adaptive world model: evolution of coupling strengths over time (~5s) */
export async function pythonAdaptiveWorldModel() { return runWorldCoupling('adaptive_world', {}); }

/** Phase 8 — Full world-market coupling synthesis (~55s) */
export async function pythonCouplingFull()       { return runWorldCoupling('coupling_full', {}); }

// ─── Phase 9 — Cognitive Orchestrator ────────────────────────────────────

const ORCH_SCRIPT = join(__dirname, '../../scripts/python/cognitive_orchestrator.py');

export async function runOrchestrator(command, params = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(PYTHON_BIN, [ORCH_SCRIPT, command], {
      cwd:   join(__dirname, '../../'),
      stdio: ['pipe', 'pipe', 'pipe'],
      env:   { ...process.env },
    });
    let stdout = '', stderr = '';
    child.stdin.end(JSON.stringify(params));
    child.stdout.on('data', d => { stdout += d.toString(); });
    child.stderr.on('data', d => { stderr += d.toString(); });
    child.on('close', code => {
      try {
        const result = JSON.parse(stdout);
        if (result.error) reject(new Error(result.error));
        else resolve(result);
      } catch {
        reject(new Error(stderr || stdout || `exit ${code}`));
      }
    });
  });
}

export async function pythonOrchHealth()     { return runOrchestrator('data_health',       {}); }
export async function pythonOrchNow()        { return runOrchestrator('orchestrate_now',   {}); }
export async function pythonOrchArbitrate()  { return runOrchestrator('arbitrate',         {}); }
export async function pythonOrchConfidence() { return runOrchestrator('confidence_map',    {}); }
export async function pythonOrchConflicts()  { return runOrchestrator('conflict_scan',     {}); }
export async function pythonOrchPosture()    { return runOrchestrator('posture',           {}); }
export async function pythonOrchWatch()      { return runOrchestrator('instability_watch', {}); }
export async function pythonOrchSync()       { return runOrchestrator('evolution_sync',    {}); }
export async function pythonOrchReport()     { return runOrchestrator('daily_report',      {}); }
export async function pythonOrchFull()       { return runOrchestrator('orchestrate_full',  {}); }

// ─── Health check ────────────────────────────────────────────────────────

/**
 * تحقق من أن Python وpandas متاحان
 */
export async function checkPythonBridge() {
  return new Promise((resolve) => {
    const child = spawn(PYTHON_BIN, ['-c', 'import pandas, numpy, scipy; print("ok")'], {
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let out = '';
    child.stdout.on('data', d => { out += d.toString(); });
    child.on('close', code => {
      resolve({
        available:    code === 0 && out.trim() === 'ok',
        pythonBin:    PYTHON_BIN,
        scriptExists: existsSync(SCRIPT_PATH),
        message:      code === 0 ? '✅ Python bridge جاهز' : '❌ Python/pandas غير متاح',
      });
    });
  });
}

// ─── Phase 10 — Market Operating System ──────────────────────────────────────

const MOS_SCRIPT = join(__dirname, '../../scripts/python/market_os.py');

export async function runMarketOS(command, params = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(PYTHON_BIN, [MOS_SCRIPT, command], {
      cwd:   join(__dirname, '../../'),
      stdio: ['pipe', 'pipe', 'pipe'],
      env:   { ...process.env },
    });
    let stdout = '', stderr = '';
    child.stdin.end(JSON.stringify(params));
    child.stdout.on('data', d => { stdout += d.toString(); });
    child.stderr.on('data', d => { stderr += d.toString(); });
    child.on('close', code => {
      try {
        const result = JSON.parse(stdout);
        if (result.error) reject(new Error(result.error));
        else resolve(result);
      } catch {
        reject(new Error(stderr || stdout || `exit ${code}`));
      }
    });
  });
}

export async function pythonOsPipelineRun()    { return runMarketOS('pipeline_run',      {}); }
export async function pythonOsPipelineStatus() { return runMarketOS('pipeline_status',   {}); }
export async function pythonOsDashboard()      { return runMarketOS('dashboard',         {}); }
export async function pythonOsAlertScan()      { return runMarketOS('alert_scan',        {}); }
export async function pythonOsArchive()        { return runMarketOS('archive_snapshot',  {}); }
export async function pythonOsHealth()         { return runMarketOS('health_monitor',    {}); }
export async function pythonOsResilience()     { return runMarketOS('resilience_check',  {}); }
export async function pythonOsObservability()  { return runMarketOS('observability',     {}); }
export async function pythonOsReplay()         { return runMarketOS('historical_replay', {}); }
export async function pythonOsFull()           { return runMarketOS('os_full',           {}); }

// ── Phase 11 — Telegram Report Formatter ─────────────────────────────────────
const TG_REPORT_SCRIPT = join(__dirname, '../../scripts/python/telegram_report.py');

export async function runTelegramReport(command, params = {}) {
  return new Promise((resolve) => {
    const args = [TG_REPORT_SCRIPT, command];
    if (params && Object.keys(params).length > 0) {
      args.push(JSON.stringify(params));
    }
    const child = spawn(PYTHON_BIN, args, {
      cwd: ROOT_DIR,
      env: { ...process.env, PYTHONUNBUFFERED: '1' },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let out = '', err = '';
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(value);
    };
    child.stdout.on('data', d => { out += d.toString(); });
    child.stderr.on('data', d => { err += d.toString(); });
    child.on('close', () => {
      try {
        const parsed = JSON.parse(out.trim());
        finish(parsed);
      } catch {
        finish({ error: 'parse_error', stderr: err.slice(0, 500), raw: out.slice(0, 500) });
      }
    });
    child.on('error', e => finish({ error: e.message }));
    const timer = setTimeout(() => { child.kill(); finish({ error: 'timeout' }); }, TG_TIMEOUT_MS);
  });
}

export async function pythonTgFormatDaily(params = {}) { return runTelegramReport('format_daily', params); }
export async function pythonTgFormatAlert()   { return runTelegramReport('format_alert',   {}); }
export async function pythonTgFormatPosture() { return runTelegramReport('format_posture', {}); }
export async function pythonTgFormatDelta()   { return runTelegramReport('format_delta',   {}); }
export async function pythonTgTestFormat()    { return runTelegramReport('test_format',    {}); }

// ── Phase 12 — Market Intelligence Discovery System (DMIDS) ───────────────────
const DMIDS_SCRIPT = join(__dirname, '../../scripts/python/market_intelligence.py');

export async function runDMIDS(command, params = {}) {
  return new Promise((resolve) => {
    const args = [DMIDS_SCRIPT, command];
    if (params && Object.keys(params).length > 0) args.push(JSON.stringify(params));
    const child = spawn(PYTHON_BIN, args, {
      env: { ...process.env },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let out = '', err = '';
    child.stdout.on('data', d => { out += d.toString(); });
    child.stderr.on('data', d => { err += d.toString(); });
    child.on('close', () => {
      try { resolve(JSON.parse(out.trim())); }
      catch { resolve({ error: 'parse_error', stderr: err.slice(0,500), raw: out.slice(0,300) }); }
    });
    child.on('error', e => resolve({ error: e.message }));
    setTimeout(() => { child.kill(); resolve({ error: 'timeout' }); }, 600_000);
  });
}

export async function pythonDmidsStatus()    { return runDMIDS('status',             {}); }
export async function pythonDmidsProfiles()  { return runDMIDS('stock_profiles',     {}); }
export async function pythonDmidsExplode()   { return runDMIDS('explosion_scan',     {}); }
export async function pythonDmidsPrecursors(){ return runDMIDS('precursor_discovery',{}); }
export async function pythonDmidsSectors()   { return runDMIDS('sector_cycles',      {}); }
export async function pythonDmidsKnowledge() { return runDMIDS('knowledge_update',   {}); }
export async function pythonDmidsReport()    { return runDMIDS('research_report',    {}); }
export async function pythonDmidsFull()      { return runDMIDS('full_discovery',     {}); }

// ── Phase 13 — Deep Historical Validation & Market Law Discovery ──────────────
const DHVD_SCRIPT = join(__dirname, '../../scripts/python/historical_validation.py');

async function runDHVD(command, params = {}) {
  return new Promise((resolve) => {
    const args = [DHVD_SCRIPT, command, JSON.stringify(params)];
    const py   = spawn(PYTHON_BIN, args, { cwd: join(__dirname, '../..') });
    let out = '', err = '';
    const timer = setTimeout(() => {
      py.kill('SIGTERM');
      resolve({ error: `DHVD timeout (${command})` });
    }, 600_000);  // 10 min for full validation
    py.stdout.on('data', d => { out += d.toString(); });
    py.stderr.on('data', d => { err += d.toString(); });
    py.on('close', () => {
      clearTimeout(timer);
      const jsonStart = out.lastIndexOf('\n{');
      const jsonLine  = jsonStart >= 0 ? out.slice(jsonStart + 1) : out.trim();
      try {
        resolve(JSON.parse(jsonLine));
      } catch {
        resolve({ error: err || 'Invalid JSON from DHVD', raw: out.slice(-500) });
      }
    });
  });
}

export async function pythonDhvdStatus()          { return runDHVD('status',                     {}); }
export async function pythonDhvdValidateLaws()    { return runDHVD('validate_laws',              {}); }
export async function pythonDhvdFamilies()        { return runDHVD('precursor_families',         {}); }
export async function pythonDhvdRegimes()         { return runDHVD('regime_history',             {}); }
export async function pythonDhvdFalseBreakouts()  { return runDHVD('false_breakouts',            {}); }
export async function pythonDhvdHypotheses()      { return runDHVD('hypothesis_status',          {}); }
export async function pythonDhvdReport()          { return runDHVD('validation_report',          {}); }
export async function pythonDhvdFull()            { return runDHVD('full_historical_validation', {}); }

// ── Phase 14 — Law Synthesis Engine ──────────────────────────────────────────
const LS_SCRIPT = join(__dirname, '../../scripts/python/law_synthesis.py');

async function runLS(command, params = {}) {
  return new Promise((resolve) => {
    const args = [LS_SCRIPT, command, JSON.stringify(params)];
    const py   = spawn(PYTHON_BIN, args, { cwd: join(__dirname, '../..') });
    let out = '', err = '';
    const timer = setTimeout(() => {
      py.kill('SIGTERM');
      resolve({ error: `LS timeout (${command})` });
    }, 600_000);  // 10 min for full synthesis
    py.stdout.on('data', d => { out += d.toString(); });
    py.stderr.on('data', d => { err += d.toString(); });
    py.on('close', () => {
      clearTimeout(timer);
      const jsonStart = out.lastIndexOf('\n{');
      const jsonLine  = jsonStart >= 0 ? out.slice(jsonStart + 1) : out.trim();
      try {
        resolve(JSON.parse(jsonLine));
      } catch {
        resolve({ error: err || 'Invalid JSON from LS', raw: out.slice(-500) });
      }
    });
  });
}

export async function pythonLsStatus()        { return runLS('status',           {}); }
export async function pythonLsStability()     { return runLS('stability_curves', {}); }
export async function pythonLsCounterfactuals(){ return runLS('counterfactuals',  {}); }
export async function pythonLsMutations()     { return runLS('mutations',        {}); }
export async function pythonLsInteractions()  { return runLS('interactions',     {}); }
export async function pythonLsNetwork()       { return runLS('network',          {}); }
export async function pythonLsPhysics()       { return runLS('physics',          {}); }
export async function pythonLsRegimeSystems() { return runLS('regime_systems',   {}); }
export async function pythonLsReport()        { return runLS('synthesis_report', {}); }
export async function pythonLsFull()          { return runLS('full_synthesis',   {}); }

// ── Phase 15 — Self-Learning Market Evolution ────────────────────────────────
const EVO_SCRIPT = join(__dirname, '../../scripts/python/market_evolution.py');

async function runEvo(command, params = {}) {
  return new Promise((resolve) => {
    const args = [EVO_SCRIPT, command, JSON.stringify(params)];
    const py   = spawn(PYTHON_BIN, args, { cwd: join(__dirname, '../..') });
    let out = '', err = '';
    const timer = setTimeout(() => {
      py.kill('SIGTERM');
      resolve({ error: `Evo timeout (${command})` });
    }, 600_000);
    py.stdout.on('data', d => { out += d.toString(); });
    py.stderr.on('data', d => { err += d.toString(); });
    py.on('close', () => {
      clearTimeout(timer);
      const jsonStart = out.lastIndexOf('\n{');
      const jsonLine  = jsonStart >= 0 ? out.slice(jsonStart + 1) : out.trim();
      try {
        resolve(JSON.parse(jsonLine));
      } catch {
        resolve({ error: err || 'Invalid JSON from Evo', raw: out.slice(-500) });
      }
    });
  });
}

export async function pythonEvoStatus()     { return runEvo('status',             {}); }
export async function pythonEvoExperience() { return runEvo('experience',         {}); }
export async function pythonEvoConfidence() { return runEvo('confidence',         {}); }
export async function pythonEvoReinforce()  { return runEvo('reinforcement',      {}); }
export async function pythonEvoFailures()   { return runEvo('failures',           {}); }
export async function pythonEvoStocks()     { return runEvo('stocks',             {}); }
export async function pythonEvoHypotheses() { return runEvo('hypotheses',         {}); }
export async function pythonEvoRegimes()    { return runEvo('regime_calibration', {}); }
export async function pythonEvoFull(params = {}) { return runEvo('full_evolution', params); }

// ── Phase 16 — Autonomous Market Cognition Engine ────────────────────────────
const COG_SCRIPT = join(__dirname, '../../scripts/python/market_cognition.py');

async function runCog(command, params = {}) {
  return new Promise((resolve) => {
    const args = [COG_SCRIPT, command, JSON.stringify(params)];
    const py   = spawn(PYTHON_BIN, args, { cwd: join(__dirname, '../..') });
    let out = '', err = '';
    const timer = setTimeout(() => {
      py.kill('SIGTERM');
      resolve({ error: `Cognition timeout (${command})` });
    }, 600_000);
    py.stdout.on('data', d => { out += d.toString(); });
    py.stderr.on('data', d => { err += d.toString(); });
    py.on('close', () => {
      clearTimeout(timer);
      const jsonStart = out.lastIndexOf('\n{');
      const jsonLine  = jsonStart >= 0 ? out.slice(jsonStart + 1) : out.trim();
      try {
        resolve(JSON.parse(jsonLine));
      } catch {
        resolve({ error: err || 'Invalid JSON from Cognition', raw: out.slice(-500) });
      }
    });
  });
}

export async function pythonCogStatus()      { return runCog('status',             {}); }
export async function pythonCogStockDNA()    { return runCog('stock_dna',          {}); }
export async function pythonCogSectorDNA()   { return runCog('sector_dna',         {}); }
export async function pythonCogExplosions()  { return runCog('explosion_anatomy',  {}); }
export async function pythonCogLaws()        { return runCog('universal_laws',     {}); }
export async function pythonCogMemory()      { return runCog('consolidate_memory', {}); }
export async function pythonCogEvolve()      { return runCog('self_evolve',        {}); }
export async function pythonCogReport()      { return runCog('generate_report',    {}); }
export async function pythonCogFull(params = {}) { return runCog('full_cognition', params); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 17 — Graph Contagion Engine
// ══════════════════════════════════════════════════════════════════════════════
const GRAPH_SCRIPT = join(__dirname, '../../scripts/python/graph_contagion_engine.py');

async function runGraph(command, params = {}) {
  return new Promise((resolve) => {
    const args  = [GRAPH_SCRIPT, command, JSON.stringify(params)];
    const py    = spawn(PYTHON_BIN, args, { cwd: join(__dirname, '../..') });
    let out = '', err = '';
    const timer = setTimeout(() => { py.kill('SIGTERM'); resolve({ error: `Graph timeout (${command})` }); }, 600_000);
    py.stdout.on('data', d => { out += d.toString(); });
    py.stderr.on('data', d => { err += d.toString(); });
    py.on('close', () => {
      clearTimeout(timer);
      const jsonStart = out.lastIndexOf('\n{');
      const jsonLine  = jsonStart >= 0 ? out.slice(jsonStart + 1) : out.trim();
      try { resolve(JSON.parse(jsonLine)); }
      catch { resolve({ error: err || 'Invalid JSON from Graph', raw: out.slice(-500) }); }
    });
  });
}

export async function pythonGraphBuild(p={})      { return runGraph('build_network',       p); }
export async function pythonGraphPagerank(p={})   { return runGraph('pagerank',            p); }
export async function pythonGraphCommunity(p={})  { return runGraph('communities',         p); }
export async function pythonGraphContagion(p={})  { return runGraph('contagion_paths',     p); }
export async function pythonGraphCascade(p={})    { return runGraph('cascade_simulation',  p); }
export async function pythonGraphCentrality(p={}) { return runGraph('centrality_analysis', p); }
export async function pythonGraphSpillover(p={})  { return runGraph('momentum_spillover',  p); }
export async function pythonGraphFull(p={})       { return runGraph('full_analysis',       p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 18 — RL Environment & Walk-Forward Backtesting
// ══════════════════════════════════════════════════════════════════════════════
const RL_SCRIPT = join(__dirname, '../../scripts/python/rl_environment.py');

async function runRL(command, params = {}) {
  return new Promise((resolve) => {
    const args  = [RL_SCRIPT, command, JSON.stringify(params)];
    const py    = spawn(PYTHON_BIN, args, { cwd: join(__dirname, '../..') });
    let out = '', err = '';
    const timer = setTimeout(() => { py.kill('SIGTERM'); resolve({ error: `RL timeout (${command})` }); }, 600_000);
    py.stdout.on('data', d => { out += d.toString(); });
    py.stderr.on('data', d => { err += d.toString(); });
    py.on('close', () => {
      clearTimeout(timer);
      const jsonStart = out.lastIndexOf('\n{');
      const jsonLine  = jsonStart >= 0 ? out.slice(jsonStart + 1) : out.trim();
      try { resolve(JSON.parse(jsonLine)); }
      catch { resolve({ error: err || 'Invalid JSON from RL', raw: out.slice(-500) }); }
    });
  });
}

export async function pythonRLStateVector(p={})  { return runRL('build_state_vector',  p); }
export async function pythonRLBacktest(p={})     { return runRL('backtest_strategy',   p); }
export async function pythonRLWalkForward(p={})  { return runRL('walk_forward',        p); }
export async function pythonRLOptimize(p={})     { return runRL('optimize_thresholds', p); }
export async function pythonRLReport(p={})       { return runRL('performance_report',  p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 19 — Explainability Engine (SHAP + LightGBM)
// ══════════════════════════════════════════════════════════════════════════════
const EXPLAIN_SCRIPT = join(__dirname, '../../scripts/python/explainability_engine.py');

async function runExplain(command, params = {}) {
  return new Promise((resolve) => {
    const args  = [EXPLAIN_SCRIPT, command, JSON.stringify(params)];
    const py    = spawn(PYTHON_BIN, args, { cwd: join(__dirname, '../..') });
    let out = '', err = '';
    const timer = setTimeout(() => { py.kill('SIGTERM'); resolve({ error: `Explain timeout (${command})` }); }, 600_000);
    py.stdout.on('data', d => { out += d.toString(); });
    py.stderr.on('data', d => { err += d.toString(); });
    py.on('close', () => {
      clearTimeout(timer);
      const jsonStart = out.lastIndexOf('\n{');
      const jsonLine  = jsonStart >= 0 ? out.slice(jsonStart + 1) : out.trim();
      try { resolve(JSON.parse(jsonLine)); }
      catch { resolve({ error: err || 'Invalid JSON from Explain', raw: out.slice(-500) }); }
    });
  });
}

export async function pythonExplainTrain(p={})      { return runExplain('train_model',         p); }
export async function pythonExplainStock(p={})      { return runExplain('explain_stock',       p); }
export async function pythonExplainImportance(p={}) { return runExplain('feature_importance',  p); }
export async function pythonExplainDaily(p={})      { return runExplain('daily_explanations',  p); }
export async function pythonExplainReport(p={})     { return runExplain('model_report',        p); }
export async function pythonExplainRetrain(p={})    { return runExplain('retrain',             p); }

// ── Phase 5 Enhancement — tigramite PCMCI Causal Discovery ───────────────────
const CAUSAL_SCRIPT_PATH = join(__dirname, '../../scripts/python/causal_engine.py');
export async function pythonCausalPCMCI(p = {}) {
  return new Promise((resolve) => {
    const py = spawn(PYTHON_BIN, [CAUSAL_SCRIPT_PATH, 'pcmci_sectors', JSON.stringify(p)], { cwd: join(__dirname, '../..') });
    let out = '', err = '';
    const timer = setTimeout(() => { py.kill('SIGTERM'); resolve({ error: 'PCMCI timeout' }); }, 600_000);
    py.stdout.on('data', d => { out += d.toString(); });
    py.stderr.on('data', d => { err += d.toString(); });
    py.on('close', () => {
      clearTimeout(timer);
      const jsonStart = out.lastIndexOf('\n{');
      const jsonLine  = jsonStart >= 0 ? out.slice(jsonStart + 1) : out.trim();
      try { resolve(JSON.parse(jsonLine)); }
      catch { resolve({ error: err || 'Invalid JSON from PCMCI', raw: out.slice(-200) }); }
    });
  });
}

// ══════════════════════════════════════════════════════════════════════════════
// Global Macro Fetcher — 22 indicators via yfinance/stooq
// ══════════════════════════════════════════════════════════════════════════════
const MACRO_FETCH_SCRIPT = join(__dirname, '../../scripts/python/fetch_global_macro.py');

async function runMacroFetch(command, params = {}) {
  return new Promise((resolve) => {
    const args  = [MACRO_FETCH_SCRIPT, command, JSON.stringify(params)];
    const py    = spawn(PYTHON_BIN, args, { cwd: join(__dirname, '../..') });
    let out = '', err = '';
    const timer = setTimeout(() => { py.kill('SIGTERM'); resolve({ error: `MacroFetch timeout (${command})` }); }, 300_000);
    py.stdout.on('data', d => { out += d.toString(); });
    py.stderr.on('data', d => { err += d.toString(); });
    py.on('close', () => {
      clearTimeout(timer);
      const jsonStart = out.lastIndexOf('\n{');
      const jsonLine  = jsonStart >= 0 ? out.slice(jsonStart + 1) : out.trim();
      try { resolve(JSON.parse(jsonLine)); }
      catch { resolve({ error: err || 'Invalid JSON from MacroFetch', raw: out.slice(-500) }); }
    });
  });
}

export async function pythonMacroFetchAll(p={})    { return runMacroFetch('fetch_all', p); }
export async function pythonMacroFetchNow(p={})    { return runMacroFetch('fetch_now', p); }
export async function pythonMacroFetchStatus(p={}) { return runMacroFetch('status',    p); }
export async function pythonMacroFetchReport(p={}) { return runMacroFetch('report',    p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 20 — Historical Integrity Engine
// ══════════════════════════════════════════════════════════════════════════════
const INTEGRITY_SCRIPT = join(__dirname, '../../scripts/python/historical_integrity_engine.py');

async function runIntegrity(command, params = {}) {
  return new Promise((resolve) => {
    const args  = [INTEGRITY_SCRIPT, command, JSON.stringify(params)];
    const py    = spawn(PYTHON_BIN, args, { cwd: join(__dirname, '../..') });
    let out = '', err = '';
    const timer = setTimeout(() => { py.kill('SIGTERM'); resolve({ error: `Integrity timeout (${command})` }); }, 600_000);
    py.stdout.on('data', d => { out += d.toString(); });
    py.stderr.on('data', d => { err += d.toString(); });
    py.on('close', () => {
      clearTimeout(timer);
      const jsonStart = out.lastIndexOf('\n{');
      const jsonLine  = jsonStart >= 0 ? out.slice(jsonStart + 1) : out.trim();
      try { resolve(JSON.parse(jsonLine)); }
      catch { resolve({ error: err || 'Invalid JSON from Integrity', raw: out.slice(-500) }); }
    });
  });
}

export async function pythonIntegrityScanAll(p={})     { return runIntegrity('scan_all',       p); }
export async function pythonIntegrityScanSymbol(p={})  { return runIntegrity('scan_symbol',    p); }
export async function pythonIntegrityBreadth(p={})     { return runIntegrity('compute_breadth',p); }
export async function pythonIntegrityReport(p={})      { return runIntegrity('get_report',     p); }
export async function pythonIntegrityConfidence(p={})  { return runIntegrity('get_confidence', p); }
export async function pythonIntegrityAnomalies(p={})   { return runIntegrity('flag_anomalies', p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 21 — Unified Market Cognition Graph (UMCG)
// ══════════════════════════════════════════════════════════════════════════════
const UMCG_SCRIPT = join(__dirname, '../../scripts/python/unified_market_graph.py');

async function runUMCG(command, params = {}) {
  return new Promise((resolve) => {
    const args  = [UMCG_SCRIPT, command, JSON.stringify(params)];
    const py    = spawn(PYTHON_BIN, args, { cwd: join(__dirname, '../..') });
    let out = '', err = '';
    const timer = setTimeout(() => { py.kill('SIGTERM'); resolve({ error: `UMCG timeout (${command})` }); }, 600_000);
    py.stdout.on('data', d => { out += d.toString(); });
    py.stderr.on('data', d => { err += d.toString(); });
    py.on('close', () => {
      clearTimeout(timer);
      const jsonStart = out.lastIndexOf('\n{');
      const jsonLine  = jsonStart >= 0 ? out.slice(jsonStart + 1) : out.trim();
      try { resolve(JSON.parse(jsonLine)); }
      catch { resolve({ error: err || 'Invalid JSON from UMCG', raw: out.slice(-500) }); }
    });
  });
}

export async function pythonUMCGBuild(p={})         { return runUMCG('build_full',          p); }
export async function pythonUMCGMetrics(p={})       { return runUMCG('compute_metrics',     p); }
export async function pythonUMCGCommunities(p={})   { return runUMCG('detect_communities',  p); }
export async function pythonUMCGFragility(p={})     { return runUMCG('find_fragility',      p); }
export async function pythonUMCGSnapshot(p={})      { return runUMCG('weekly_snapshot',     p); }
export async function pythonUMCGPaths(p={})         { return runUMCG('query_paths',         p); }
export async function pythonUMCGGetSnapshot(p={})   { return runUMCG('get_snapshot',        p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 22 — Causal Discovery Engine
// ══════════════════════════════════════════════════════════════════════════════
const CAUSAL_DISC_SCRIPT = join(__dirname, '../../scripts/python/causal_discovery_engine.py');

async function runCausalDisc(command, params = {}) {
  return new Promise((resolve) => {
    const args  = [CAUSAL_DISC_SCRIPT, command, JSON.stringify(params)];
    const py    = spawn(PYTHON_BIN, args, { cwd: join(__dirname, '../..') });
    let out = '', err = '';
    const timer = setTimeout(() => { py.kill('SIGTERM'); resolve({ error: `CausalDisc timeout (${command})` }); }, 600_000);
    py.stdout.on('data', d => { out += d.toString(); });
    py.stderr.on('data', d => { err += d.toString(); });
    py.on('close', () => {
      clearTimeout(timer);
      const jsonStart = out.lastIndexOf('\n{');
      const jsonLine  = jsonStart >= 0 ? out.slice(jsonStart + 1) : out.trim();
      try { resolve(JSON.parse(jsonLine)); }
      catch { resolve({ error: err || 'Invalid JSON from CausalDisc', raw: out.slice(-500) }); }
    });
  });
}

export async function pythonCausalTransferEntropy(p={}) { return runCausalDisc('transfer_entropy',   p); }
export async function pythonCausalLaggedInference(p={}) { return runCausalDisc('lagged_inference',   p); }
export async function pythonCausalStability(p={})       { return runCausalDisc('causal_stability',   p); }
export async function pythonCausalRegime(p={})          { return runCausalDisc('regime_causality',   p); }
export async function pythonCausalMacroTransmission(p={}) { return runCausalDisc('macro_transmission', p); }
export async function pythonCausalBuildFull(p={})       { return runCausalDisc('build_full',          p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 23 — Failure Memory Engine
// ══════════════════════════════════════════════════════════════════════════════
const FAILURE_SCRIPT = join(__dirname, '../../scripts/python/failure_memory_engine.py');

async function runFailure(command, params = {}) {
  return new Promise((resolve) => {
    const args  = [FAILURE_SCRIPT, command, JSON.stringify(params)];
    const py    = spawn(PYTHON_BIN, args, { cwd: join(__dirname, '../..') });
    let out = '', err = '';
    const timer = setTimeout(() => { py.kill('SIGTERM'); resolve({ error: `Failure timeout (${command})` }); }, 600_000);
    py.stdout.on('data', d => { out += d.toString(); });
    py.stderr.on('data', d => { err += d.toString(); });
    py.on('close', () => {
      clearTimeout(timer);
      const jsonStart = out.lastIndexOf('\n{');
      const jsonLine  = jsonStart >= 0 ? out.slice(jsonStart + 1) : out.trim();
      try { resolve(JSON.parse(jsonLine)); }
      catch { resolve({ error: err || 'Invalid JSON from Failure', raw: out.slice(-500) }); }
    });
  });
}

export async function pythonFailureAnalyzeAll(p={})     { return runFailure('analyze_all',        p); }
export async function pythonFailureClassify(p={})       { return runFailure('classify_failures',  p); }
export async function pythonFailureFamilies(p={})       { return runFailure('build_families',     p); }
export async function pythonFailurePredictive(p={})     { return runFailure('find_predictive',    p); }
export async function pythonFailureRecurrence(p={})     { return runFailure('build_recurrence',   p); }
export async function pythonFailureDailyScan(p={})      { return runFailure('daily_failure_scan', p); }
export async function pythonFailureReport(p={})         { return runFailure('report',             p); }
export async function pythonFailureBuildFull(p={})      { return runFailure('build_full',         p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 24 — Explosion Physics Engine
// ══════════════════════════════════════════════════════════════════════════════
const EXPLOSION_SCRIPT = join(__dirname, '../../scripts/python/explosion_physics_engine.py');

async function runExplosion(command, params = {}) {
  return new Promise((resolve) => {
    const args  = [EXPLOSION_SCRIPT, command, JSON.stringify(params)];
    const py    = spawn(PYTHON_BIN, args, { cwd: join(__dirname, '../..') });
    let out = '', err = '';
    const timer = setTimeout(() => { py.kill('SIGTERM'); resolve({ error: `Explosion timeout (${command})` }); }, 600_000);
    py.stdout.on('data', d => { out += d.toString(); });
    py.stderr.on('data', d => { err += d.toString(); });
    py.on('close', () => {
      clearTimeout(timer);
      const jsonStart = out.lastIndexOf('\n{');
      const jsonLine  = jsonStart >= 0 ? out.slice(jsonStart + 1) : out.trim();
      try { resolve(JSON.parse(jsonLine)); }
      catch { resolve({ error: err || 'Invalid JSON from Explosion', raw: out.slice(-500) }); }
    });
  });
}

export async function pythonExplosionReadiness(p={})    { return runExplosion('compute_readiness',      p); }
export async function pythonExplosionSignatures(p={})   { return runExplosion('analyze_signatures',     p); }
export async function pythonExplosionFalseAnatomy(p={}) { return runExplosion('false_explosion_anatomy',p); }
export async function pythonExplosionSectorPhysics(p={}) { return runExplosion('sector_physics',        p); }
export async function pythonExplosionWatchlist(p={})    { return runExplosion('daily_watchlist',        p); }
export async function pythonExplosionBuildFull(p={})    { return runExplosion('build_full',             p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 25 — Market DNA Engine
// ══════════════════════════════════════════════════════════════════════════════
const DNA_SCRIPT = join(__dirname, '../../scripts/python/market_dna_engine.py');

async function runDNA(command, params = {}) {
  return new Promise((resolve) => {
    const args  = [DNA_SCRIPT, command, JSON.stringify(params)];
    const py    = spawn(PYTHON_BIN, args, { cwd: join(__dirname, '../..') });
    let out = '', err = '';
    const timer = setTimeout(() => { py.kill('SIGTERM'); resolve({ error: `DNA timeout (${command})` }); }, 600_000);
    py.stdout.on('data', d => { out += d.toString(); });
    py.stderr.on('data', d => { err += d.toString(); });
    py.on('close', () => {
      clearTimeout(timer);
      const jsonStart = out.lastIndexOf('\n{');
      const jsonLine  = jsonStart >= 0 ? out.slice(jsonStart + 1) : out.trim();
      try { resolve(JSON.parse(jsonLine)); }
      catch { resolve({ error: err || 'Invalid JSON from DNA', raw: out.slice(-500) }); }
    });
  });
}

export async function pythonDNABuild(p={})           { return runDNA('build_dna',           p); }
export async function pythonDNAMutations(p={})       { return runDNA('detect_mutations',    p); }
export async function pythonDNAClusters(p={})        { return runDNA('cluster_communities', p); }
export async function pythonDNAProfile(p={})         { return runDNA('get_profile',         p); }
export async function pythonDNASectorRefresh(p={})   { return runDNA('sector_dna_refresh',  p); }
export async function pythonDNABuildFull(p={})       { return runDNA('build_full',          p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 26 — Adaptive Research Loop
// ══════════════════════════════════════════════════════════════════════════════
const RESEARCH_SCRIPT = join(__dirname, '../../scripts/python/adaptive_research_loop.py');

async function runResearch(command, params = {}) {
  return new Promise((resolve) => {
    const args  = [RESEARCH_SCRIPT, command, JSON.stringify(params)];
    const py    = spawn(PYTHON_BIN, args, { cwd: join(__dirname, '../..') });
    let out = '', err = '';
    const timer = setTimeout(() => { py.kill('SIGTERM'); resolve({ error: `Research timeout (${command})` }); }, 600_000);
    py.stdout.on('data', d => { out += d.toString(); });
    py.stderr.on('data', d => { err += d.toString(); });
    py.on('close', () => {
      clearTimeout(timer);
      const jsonStart = out.lastIndexOf('\n{');
      const jsonLine  = jsonStart >= 0 ? out.slice(jsonStart + 1) : out.trim();
      try { resolve(JSON.parse(jsonLine)); }
      catch { resolve({ error: err || 'Invalid JSON from Research', raw: out.slice(-500) }); }
    });
  });
}

export async function pythonResearchAssessLaws(p={})    { return runResearch('assess_laws',          p); }
export async function pythonResearchDiscover(p={})      { return runResearch('discover_new_laws',    p); }
export async function pythonResearchMutate(p={})        { return runResearch('mutate_weak_laws',     p); }
export async function pythonResearchDirectives(p={})    { return runResearch('generate_directives',  p); }
export async function pythonResearchEvolution(p={})     { return runResearch('run_evolution_cycle',  p); }
export async function pythonResearchLawTree(p={})       { return runResearch('get_law_tree',         p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 27 — Execution Reality Engine
// ══════════════════════════════════════════════════════════════════════════════
const EXECUTION_SCRIPT = join(__dirname, '../../scripts/python/execution_reality_engine.py');

async function runExecution(command, params = {}) {
  return new Promise((resolve) => {
    const args  = [EXECUTION_SCRIPT, command, JSON.stringify(params)];
    const py    = spawn(PYTHON_BIN, args, { cwd: join(__dirname, '../..') });
    let out = '', err = '';
    const timer = setTimeout(() => { py.kill('SIGTERM'); resolve({ error: `Execution timeout (${command})` }); }, 600_000);
    py.stdout.on('data', d => { out += d.toString(); });
    py.stderr.on('data', d => { err += d.toString(); });
    py.on('close', () => {
      clearTimeout(timer);
      const jsonStart = out.lastIndexOf('\n{');
      const jsonLine  = jsonStart >= 0 ? out.slice(jsonStart + 1) : out.trim();
      try { resolve(JSON.parse(jsonLine)); }
      catch { resolve({ error: err || 'Invalid JSON from Execution', raw: out.slice(-500) }); }
    });
  });
}

export async function pythonExecutionLiquidityProfiles(p={}) { return runExecution('build_liquidity_profiles', p); }
export async function pythonExecutionAdjustReturns(p={})     { return runExecution('adjust_returns',           p); }
export async function pythonExecutionPortfolioStress(p={})   { return runExecution('portfolio_stress',         p); }
export async function pythonExecutionScanFeasibility(p={})   { return runExecution('scan_feasibility',         p); }
export async function pythonExecutionProfile(p={})           { return runExecution('get_profile',              p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 28 — Unified Daily Synthesis (UDIS) — THE CROWN JEWEL
// ══════════════════════════════════════════════════════════════════════════════
const SYNTHESIS_SCRIPT = join(__dirname, '../../scripts/python/unified_daily_synthesis.py');

async function runSynthesis(command, params = {}) {
  return new Promise((resolve) => {
    const args  = [SYNTHESIS_SCRIPT, command, JSON.stringify(params)];
    const py    = spawn(PYTHON_BIN, args, { cwd: join(__dirname, '../..') });
    let out = '', err = '';
    const timer = setTimeout(() => { py.kill('SIGTERM'); resolve({ error: `Synthesis timeout (${command})` }); }, 600_000);
    py.stdout.on('data', d => { out += d.toString(); });
    py.stderr.on('data', d => { err += d.toString(); });
    py.on('close', () => {
      clearTimeout(timer);
      const jsonStart = out.lastIndexOf('\n{');
      const jsonLine  = jsonStart >= 0 ? out.slice(jsonStart + 1) : out.trim();
      try { resolve(JSON.parse(jsonLine)); }
      catch { resolve({ error: err || 'Invalid JSON from Synthesis', raw: out.slice(-500) }); }
    });
  });
}

export async function pythonSynthesisBuild(p={})       { return runSynthesis('synthesize',       p); }
export async function pythonSynthesisDailyBrief(p={})  { return runSynthesis('daily_brief',      p); }
export async function pythonSynthesisGetReport(p={})   { return runSynthesis('get_last_report',  p); }
export async function pythonSynthesisGetSection(p={})  { return runSynthesis('get_section',      p); }
export async function pythonSynthesisStatus(p={})      { return runSynthesis('status',           p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 29 — Intelligence Prioritization Layer
// ══════════════════════════════════════════════════════════════════════════════
const PRIORITIZER_SCRIPT = join(__dirname, '../../scripts/python/intelligence_prioritizer.py');

async function runPrioritizer(command, params = {}) {
  return new Promise((resolve) => {
    const args  = [PRIORITIZER_SCRIPT, command, JSON.stringify(params)];
    const py    = spawn(PYTHON_BIN, args, { cwd: join(__dirname, '../..') });
    let out = '', err = '';
    const timer = setTimeout(() => { py.kill('SIGTERM'); resolve({ error: `Prioritizer timeout (${command})` }); }, 600_000);
    py.stdout.on('data', d => { out += d.toString(); });
    py.stderr.on('data', d => { err += d.toString(); });
    py.on('close', () => {
      clearTimeout(timer);
      const jsonStart = out.lastIndexOf('\n{');
      const jsonLine  = jsonStart >= 0 ? out.slice(jsonStart + 1) : out.trim();
      try { resolve(JSON.parse(jsonLine)); }
      catch { resolve({ error: err || 'Invalid JSON from Prioritizer', raw: out.slice(-500) }); }
    });
  });
}

export async function pythonPrioritizerRun(p={})        { return runPrioritizer('prioritize',   p); }
export async function pythonPrioritizerTopInsights(p={}) { return runPrioritizer('top_insights', p); }
export async function pythonPrioritizerAnomaly(p={})    { return runPrioritizer('anomaly_today', p); }
export async function pythonPrioritizerScoreSymbol(p={}) { return runPrioritizer('score_symbol', p); }
export async function pythonPrioritizerDailyBrief(p={}) { return runPrioritizer('daily_brief',  p); }
export async function pythonPrioritizerBuildFull(p={})  { return runPrioritizer('build_full',   p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 30 — Episodic Market Memory Engine
// ══════════════════════════════════════════════════════════════════════════════
const EPISODIC_SCRIPT = join(__dirname, '../../scripts/python/episodic_memory_engine.py');

async function runEpisodic(command, params = {}) {
  return new Promise((resolve) => {
    const args  = [EPISODIC_SCRIPT, command, JSON.stringify(params)];
    const py    = spawn(PYTHON_BIN, args, { cwd: join(__dirname, '../..') });
    let out = '', err = '';
    const timer = setTimeout(() => { py.kill('SIGTERM'); resolve({ error: `Episodic timeout (${command})` }); }, 600_000);
    py.stdout.on('data', d => { out += d.toString(); });
    py.stderr.on('data', d => { err += d.toString(); });
    py.on('close', () => {
      clearTimeout(timer);
      const jsonStart = out.lastIndexOf('\n{');
      const jsonLine  = jsonStart >= 0 ? out.slice(jsonStart + 1) : out.trim();
      try { resolve(JSON.parse(jsonLine)); }
      catch { resolve({ error: err || 'Invalid JSON from Episodic', raw: out.slice(-500) }); }
    });
  });
}

export async function pythonEpisodicEncode(p={})        { return runEpisodic('encode_episodes', p); }
export async function pythonEpisodicFindSimilar(p={})   { return runEpisodic('find_similar',    p); }
export async function pythonEpisodicAnalogy(p={})       { return runEpisodic('analogy_report',  p); }
export async function pythonEpisodicGetEpisode(p={})    { return runEpisodic('get_episode',     p); }
export async function pythonEpisodicBuildFull(p={})     { return runEpisodic('build_full',      p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 31 — Meta-Learning Engine
// ══════════════════════════════════════════════════════════════════════════════
const META_LEARNING_SCRIPT = join(__dirname, '../../scripts/python/meta_learning_engine.py');

async function runMetaLearning(command, params = {}) {
  return new Promise((resolve) => {
    const args  = [META_LEARNING_SCRIPT, command, JSON.stringify(params)];
    const py    = spawn(PYTHON_BIN, args, { cwd: join(__dirname, '../..') });
    let out = '', err = '';
    const timer = setTimeout(() => { py.kill('SIGTERM'); resolve({ error: `MetaLearning timeout (${command})` }); }, 600_000);
    py.stdout.on('data', d => { out += d.toString(); });
    py.stderr.on('data', d => { err += d.toString(); });
    py.on('close', () => {
      clearTimeout(timer);
      const jsonStart = out.lastIndexOf('\n{');
      const jsonLine  = jsonStart >= 0 ? out.slice(jsonStart + 1) : out.trim();
      try { resolve(JSON.parse(jsonLine)); }
      catch { resolve({ error: err || 'Invalid JSON from MetaLearning', raw: out.slice(-500) }); }
    });
  });
}

export async function pythonMetaAnalyzeHypotheses(p={})  { return runMetaLearning('analyze_hypotheses',    p); }
export async function pythonMetaFailureContexts(p={})    { return runMetaLearning('failure_contexts',      p); }
export async function pythonMetaPredictabilityMap(p={})  { return runMetaLearning('predictability_map',    p); }
export async function pythonMetaDirectives(p={})         { return runMetaLearning('meta_directives',       p); }
export async function pythonMetaBuildFull(p={})          { return runMetaLearning('build_full',            p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 32 — Portfolio Cognition System
// ══════════════════════════════════════════════════════════════════════════════
const PORTFOLIO_COGNITION_SCRIPT = join(__dirname, '../../scripts/python/portfolio_cognition.py');

async function runPortfolioCognition(command, params = {}) {
  return new Promise((resolve) => {
    const args  = [PORTFOLIO_COGNITION_SCRIPT, command, JSON.stringify(params)];
    const py    = spawn(PYTHON_BIN, args, { cwd: join(__dirname, '../..') });
    let out = '', err = '';
    const timer = setTimeout(() => { py.kill('SIGTERM'); resolve({ error: `PortfolioCognition timeout (${command})` }); }, 600_000);
    py.stdout.on('data', d => { out += d.toString(); });
    py.stderr.on('data', d => { err += d.toString(); });
    py.on('close', () => {
      clearTimeout(timer);
      const jsonStart = out.lastIndexOf('\n{');
      const jsonLine  = jsonStart >= 0 ? out.slice(jsonStart + 1) : out.trim();
      try { resolve(JSON.parse(jsonLine)); }
      catch { resolve({ error: err || 'Invalid JSON from PortfolioCognition', raw: out.slice(-500) }); }
    });
  });
}

export async function pythonPortfolioOrchestrate(p={})        { return runPortfolioCognition('orchestrate',           p); }
export async function pythonPortfolioSizePositions(p={})      { return runPortfolioCognition('size_positions',        p); }
export async function pythonPortfolioRiskBudget(p={})         { return runPortfolioCognition('risk_budget',           p); }
export async function pythonPortfolioAdaptiveConcentration(p={}) { return runPortfolioCognition('adaptive_concentration', p); }
export async function pythonPortfolioBuild(p={})              { return runPortfolioCognition('build_portfolio',       p); }
export async function pythonPortfolioBuildFull(p={})          { return runPortfolioCognition('build_full',            p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 33 — Regime Transition Forecaster
// ══════════════════════════════════════════════════════════════════════════════
const TRANSITION_SCRIPT = join(__dirname, '../../scripts/python/regime_transition_forecaster.py');

async function runTransition(command, params = {}) {
  return new Promise((resolve) => {
    const args  = [TRANSITION_SCRIPT, command, JSON.stringify(params)];
    const py    = spawn(PYTHON_BIN, args, { cwd: join(__dirname, '../..') });
    let out = '', err = '';
    const timer = setTimeout(() => { py.kill('SIGTERM'); resolve({ error: `Transition timeout (${command})` }); }, 600_000);
    py.stdout.on('data', d => { out += d.toString(); });
    py.stderr.on('data', d => { err += d.toString(); });
    py.on('close', () => {
      clearTimeout(timer);
      const jsonStart = out.lastIndexOf('\n{');
      const jsonLine  = jsonStart >= 0 ? out.slice(jsonStart + 1) : out.trim();
      try { resolve(JSON.parse(jsonLine)); }
      catch { resolve({ error: err || 'Invalid JSON from Transition', raw: out.slice(-500) }); }
    });
  });
}

export async function pythonTransitionProbability(p={})  { return runTransition('compute_probability', p); }
export async function pythonTransitionPrecursors(p={})   { return runTransition('detect_precursors',  p); }
export async function pythonTransitionEWI(p={})          { return runTransition('early_warning_index', p); }
export async function pythonTransitionAlert(p={})        { return runTransition('transition_alert',   p); }
export async function pythonTransitionBuildFull(p={})    { return runTransition('build_full',         p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 34 — Cognitive Arbitration Layer
// ══════════════════════════════════════════════════════════════════════════════
const ARBITRATION_SCRIPT = join(__dirname, '../../scripts/python/cognitive_arbitration.py');

async function runArbitration(command, params = {}) {
  return new Promise((resolve) => {
    const args  = [ARBITRATION_SCRIPT, command, JSON.stringify(params)];
    const py    = spawn(PYTHON_BIN, args, { cwd: join(__dirname, '../..') });
    let out = '', err = '';
    const timer = setTimeout(() => { py.kill('SIGTERM'); resolve({ error: `Arbitration timeout (${command})` }); }, 600_000);
    py.stdout.on('data', d => { out += d.toString(); });
    py.stderr.on('data', d => { err += d.toString(); });
    py.on('close', () => {
      clearTimeout(timer);
      const jsonStart = out.lastIndexOf('\n{');
      const jsonLine  = jsonStart >= 0 ? out.slice(jsonStart + 1) : out.trim();
      try { resolve(JSON.parse(jsonLine)); }
      catch { resolve({ error: err || 'Invalid JSON from Arbitration', raw: out.slice(-500) }); }
    });
  });
}

export async function pythonArbitrateSymbol(p={})     { return runArbitration('arbitrate_symbol',    p); }
export async function pythonArbitrateAll(p={})        { return runArbitration('arbitrate_all',       p); }
export async function pythonArbitrateDailyDecisions(p={}) { return runArbitration('daily_decisions', p); }
export async function pythonArbitrateConstitution(p={}) { return runArbitration('constitution_report', p); }
export async function pythonArbitrateBuildFull(p={})  { return runArbitration('build_full',          p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 35 — Anti-Laws Engine
// ══════════════════════════════════════════════════════════════════════════════
const ANTI_LAWS_SCRIPT = join(__dirname, '../../scripts/python/anti_laws_engine.py');

async function runAntiLaws(command, params = {}) {
  return new Promise((resolve) => {
    const args  = [ANTI_LAWS_SCRIPT, command, JSON.stringify(params)];
    const py    = spawn(PYTHON_BIN, args, { cwd: join(__dirname, '../..') });
    let out = '', err = '';
    const timer = setTimeout(() => { py.kill('SIGTERM'); resolve({ error: `AntiLaws timeout (${command})` }); }, 600_000);
    py.stdout.on('data', d => { out += d.toString(); });
    py.stderr.on('data', d => { err += d.toString(); });
    py.on('close', () => {
      clearTimeout(timer);
      const jsonStart = out.lastIndexOf('\n{');
      const jsonLine  = jsonStart >= 0 ? out.slice(jsonStart + 1) : out.trim();
      try { resolve(JSON.parse(jsonLine)); }
      catch { resolve({ error: err || 'Invalid JSON from AntiLaws', raw: out.slice(-500) }); }
    });
  });
}

export async function pythonAntiLawsExtract(p={})      { return runAntiLaws('extract_anti_laws', p); }
export async function pythonAntiLawsBuildLibrary(p={}) { return runAntiLaws('build_library',     p); }
export async function pythonAntiLawsScanSymbol(p={})   { return runAntiLaws('scan_symbol',       p); }
export async function pythonAntiLawsDailyScan(p={})    { return runAntiLaws('daily_scan',        p); }
export async function pythonAntiLawsReport(p={})       { return runAntiLaws('anti_law_report',   p); }
export async function pythonAntiLawsBuildFull(p={})    { return runAntiLaws('build_full',        p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 36 — Statistical Grounding Engine
// ══════════════════════════════════════════════════════════════════════════════
const STAT_GROUNDING_SCRIPT = join(__dirname, '../../scripts/python/statistical_grounding.py');

async function runStatGrounding(command, params = {}) {
  return new Promise((resolve) => {
    const args  = [STAT_GROUNDING_SCRIPT, command, JSON.stringify(params)];
    const py    = spawn(PYTHON_BIN, args, { cwd: join(__dirname, '../..') });
    let out = '', err = '';
    const timer = setTimeout(() => { py.kill('SIGTERM'); resolve({ error: `StatGrounding timeout (${command})` }); }, 600_000);
    py.stdout.on('data', d => { out += d.toString(); });
    py.stderr.on('data', d => { err += d.toString(); });
    py.on('close', () => {
      clearTimeout(timer);
      const jsonStart = out.lastIndexOf('\n{');
      const jsonLine  = jsonStart >= 0 ? out.slice(jsonStart + 1) : out.trim();
      try { resolve(JSON.parse(jsonLine)); }
      catch { resolve({ error: err || 'Invalid JSON from StatGrounding', raw: out.slice(-500) }); }
    });
  });
}

export async function pythonStatGradeAllLaws(p={})     { return runStatGrounding('grade_all_laws',     p); }
export async function pythonStatTestLaw(p={})          { return runStatGrounding('test_law',           p); }
export async function pythonStatBootstrapLaw(p={})     { return runStatGrounding('bootstrap_law',      p); }
export async function pythonStatOOSValidation(p={})    { return runStatGrounding('oos_validation',     p); }
export async function pythonStatExpectancyReport(p={}) { return runStatGrounding('expectancy_report',  p); }
export async function pythonStatFDR(p={})              { return runStatGrounding('fdr_correction',     p); }
export async function pythonStatBuildFull(p={})        { return runStatGrounding('build_full',         p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 37 — Intelligence Reliability Observatory
// ══════════════════════════════════════════════════════════════════════════════
const OBSERVATORY_SCRIPT = join(__dirname, '../../scripts/python/intelligence_observatory.py');

async function runObservatory(command, params = {}) {
  return new Promise((resolve) => {
    const args  = [OBSERVATORY_SCRIPT, command, JSON.stringify(params)];
    const py    = spawn(PYTHON_BIN, args, { cwd: join(__dirname, '../..') });
    let out = '', err = '';
    const timer = setTimeout(() => { py.kill('SIGTERM'); resolve({ error: `Observatory timeout (${command})` }); }, 600_000);
    py.stdout.on('data', d => { out += d.toString(); });
    py.stderr.on('data', d => { err += d.toString(); });
    py.on('close', () => {
      clearTimeout(timer);
      const jsonStart = out.lastIndexOf('\n{');
      const jsonLine  = jsonStart >= 0 ? out.slice(jsonStart + 1) : out.trim();
      try { resolve(JSON.parse(jsonLine)); }
      catch { resolve({ error: err || 'Invalid JSON from Observatory', raw: out.slice(-500) }); }
    });
  });
}

export async function pythonObservatoryEngineHealth(p={})    { return runObservatory('engine_health',          p); }
export async function pythonObservatoryTrustability(p={})   { return runObservatory('system_trustability',    p); }
export async function pythonObservatoryDetectFailures(p={}) { return runObservatory('detect_failures',        p); }
export async function pythonObservatoryAgreement(p={})      { return runObservatory('inter_engine_agreement', p); }
export async function pythonObservatoryReport(p={})         { return runObservatory('health_report',          p); }
export async function pythonObservatoryBuildFull(p={})      { return runObservatory('build_full',             p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 38 — Cognitive Compression Engine
// ══════════════════════════════════════════════════════════════════════════════
const COMPRESSION_SCRIPT = join(__dirname, '../../scripts/python/cognitive_compression.py');

async function runCompression(command, params = {}) {
  return new Promise((resolve) => {
    const args  = [COMPRESSION_SCRIPT, command, JSON.stringify(params)];
    const py    = spawn(PYTHON_BIN, args, { cwd: join(__dirname, '../..') });
    let out = '', err = '';
    const timer = setTimeout(() => { py.kill('SIGTERM'); resolve({ error: `Compression timeout (${command})` }); }, 600_000);
    py.stdout.on('data', d => { out += d.toString(); });
    py.stderr.on('data', d => { err += d.toString(); });
    py.on('close', () => {
      clearTimeout(timer);
      const jsonStart = out.lastIndexOf('\n{');
      const jsonLine  = jsonStart >= 0 ? out.slice(jsonStart + 1) : out.trim();
      try { resolve(JSON.parse(jsonLine)); }
      catch { resolve({ error: err || 'Invalid JSON from Compression', raw: out.slice(-500) }); }
    });
  });
}

export async function pythonCompressionForces(p={})      { return runCompression('dominant_forces',  p); }
export async function pythonCompressionRisks(p={})       { return runCompression('critical_risks',   p); }
export async function pythonCompressionOpps(p={})        { return runCompression('opportunities',    p); }
export async function pythonCompressionBriefing(p={})    { return runCompression('market_briefing',  p); }
export async function pythonCompressionMII(p={})         { return runCompression('mii',              p); }
export async function pythonCompressionBuildFull(p={})   { return runCompression('build_full',       p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 39 — Uncertainty Quantification Engine
// ══════════════════════════════════════════════════════════════════════════════
const UNCERTAINTY_SCRIPT = join(__dirname, '../../scripts/python/uncertainty_engine.py');

async function runUncertainty(command, params = {}) {
  return new Promise((resolve) => {
    const args  = [UNCERTAINTY_SCRIPT, command, JSON.stringify(params)];
    const py    = spawn(PYTHON_BIN, args, { cwd: join(__dirname, '../..') });
    let out = '', err = '';
    const timer = setTimeout(() => { py.kill('SIGTERM'); resolve({ error: `Uncertainty timeout (${command})` }); }, 600_000);
    py.stdout.on('data', d => { out += d.toString(); });
    py.stderr.on('data', d => { err += d.toString(); });
    py.on('close', () => {
      clearTimeout(timer);
      const jsonStart = out.lastIndexOf('\n{');
      const jsonLine  = jsonStart >= 0 ? out.slice(jsonStart + 1) : out.trim();
      try { resolve(JSON.parse(jsonLine)); }
      catch { resolve({ error: err || 'Invalid JSON from Uncertainty', raw: out.slice(-500) }); }
    });
  });
}

export async function pythonUncertaintyEpistemic(p={})   { return runUncertainty('epistemic_symbol',    p); }
export async function pythonUncertaintyAleatoric(p={})   { return runUncertainty('aleatoric_symbol',    p); }
export async function pythonUncertaintyOOD(p={})         { return runUncertainty('ood_detection',       p); }
export async function pythonUncertaintyPropagate(p={})   { return runUncertainty('propagate',           p); }
export async function pythonUncertaintyReport(p={})      { return runUncertainty('uncertainty_report',  p); }
export async function pythonUncertaintyBuildFull(p={})   { return runUncertainty('build_full',          p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 40 — Autonomous Research Sandbox
// ══════════════════════════════════════════════════════════════════════════════
const SANDBOX_SCRIPT = join(__dirname, '../../scripts/python/research_sandbox.py');

async function runSandbox(command, params = {}) {
  return new Promise((resolve) => {
    const args  = [SANDBOX_SCRIPT, command, JSON.stringify(params)];
    const py    = spawn(PYTHON_BIN, args, { cwd: join(__dirname, '../..') });
    let out = '', err = '';
    const timer = setTimeout(() => { py.kill('SIGTERM'); resolve({ error: `Sandbox timeout (${command})` }); }, 600_000);
    py.stdout.on('data', d => { out += d.toString(); });
    py.stderr.on('data', d => { err += d.toString(); });
    py.on('close', () => {
      clearTimeout(timer);
      const jsonStart = out.lastIndexOf('\n{');
      const jsonLine  = jsonStart >= 0 ? out.slice(jsonStart + 1) : out.trim();
      try { resolve(JSON.parse(jsonLine)); }
      catch { resolve({ error: err || 'Invalid JSON from Sandbox', raw: out.slice(-500) }); }
    });
  });
}

export async function pythonSandboxGenerate(p={})    { return runSandbox('generate_hypotheses', p); }
export async function pythonSandboxBacktest(p={})    { return runSandbox('backtest_hypothesis', p); }
export async function pythonSandboxRunCycle(p={})    { return runSandbox('run_cycle',           p); }
export async function pythonSandboxReport(p={})      { return runSandbox('sandbox_report',      p); }
export async function pythonSandboxBuildFull(p={})   { return runSandbox('build_full',          p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 41 — Governance Constitution Engine
// ══════════════════════════════════════════════════════════════════════════════
const GOVERNANCE_SCRIPT = join(__dirname, '../../scripts/python/governance_constitution.py');

async function runGovernance(command, params = {}) {
  return new Promise((resolve) => {
    const args  = [GOVERNANCE_SCRIPT, command, JSON.stringify(params)];
    const py    = spawn(PYTHON_BIN, args, { cwd: join(__dirname, '../..') });
    let out = '', err = '';
    const timer = setTimeout(() => { py.kill('SIGTERM'); resolve({ error: `Governance timeout (${command})` }); }, 600_000);
    py.stdout.on('data', d => { out += d.toString(); });
    py.stderr.on('data', d => { err += d.toString(); });
    py.on('close', () => {
      clearTimeout(timer);
      const jsonStart = out.lastIndexOf('\n{');
      const jsonLine  = jsonStart >= 0 ? out.slice(jsonStart + 1) : out.trim();
      try { resolve(JSON.parse(jsonLine)); }
      catch { resolve({ error: err || 'Invalid JSON from Governance', raw: out.slice(-500) }); }
    });
  });
}

export async function pythonGovernanceAudit(p={})       { return runGovernance('audit_rule_violations',  p); }
export async function pythonGovernanceEnforce(p={})     { return runGovernance('enforce_mutation_limits', p); }
export async function pythonGovernanceResolve(p={})     { return runGovernance('resolve_override_conflict', p); }
export async function pythonGovernanceHaltCheck(p={})   { return runGovernance('check_halt_conditions',  p); }
export async function pythonGovernanceReport(p={})      { return runGovernance('governance_report',      p); }
export async function pythonGovernanceBuildFull(p={})   { return runGovernance('build_full',             p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 42 — Central Cognitive Bus
// ══════════════════════════════════════════════════════════════════════════════
const COGBUS_SCRIPT = join(__dirname, '../../scripts/python/central_cognitive_bus.py');

async function runCogBus(command, params = {}) {
  return new Promise((resolve) => {
    const args  = [COGBUS_SCRIPT, command, JSON.stringify(params)];
    const py    = spawn(PYTHON_BIN, args, { cwd: join(__dirname, '../..') });
    let out = '', err = '';
    const timer = setTimeout(() => { py.kill('SIGTERM'); resolve({ error: `CogBus timeout (${command})` }); }, 600_000);
    py.stdout.on('data', d => { out += d.toString(); });
    py.stderr.on('data', d => { err += d.toString(); });
    py.on('close', () => {
      clearTimeout(timer);
      const jsonStart = out.lastIndexOf('\n{');
      const jsonLine  = jsonStart >= 0 ? out.slice(jsonStart + 1) : out.trim();
      try { resolve(JSON.parse(jsonLine)); }
      catch { resolve({ error: err || 'Invalid JSON from CogBus', raw: out.slice(-500) }); }
    });
  });
}

export async function pythonBusCollectSignals(p={})    { return runCogBus('collect_signals',      p); }
export async function pythonBusCoherence(p={})         { return runCogBus('compute_coherence',    p); }
export async function pythonBusDirective(p={})         { return runCogBus('bus_directive',        p); }
export async function pythonBusRead(p={})              { return runCogBus('read_bus',             p); }
export async function pythonBusContradictions(p={})    { return runCogBus('contradiction_matrix', p); }
export async function pythonBusBuildFull(p={})         { return runCogBus('build_full',           p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 43 — Guided Research Pressure Zones
// ══════════════════════════════════════════════════════════════════════════════
const PRESSURE_SCRIPT = join(__dirname, '../../scripts/python/research_pressure_engine.py');

async function runPressure(command, params = {}) {
  return new Promise((resolve) => {
    const args  = [PRESSURE_SCRIPT, command, JSON.stringify(params)];
    const py    = spawn(PYTHON_BIN, args, { cwd: join(__dirname, '../..') });
    let out = '', err = '';
    const timer = setTimeout(() => { py.kill('SIGTERM'); resolve({ error: `Pressure timeout (${command})` }); }, 600_000);
    py.stdout.on('data', d => { out += d.toString(); });
    py.stderr.on('data', d => { err += d.toString(); });
    py.on('close', () => {
      clearTimeout(timer);
      const jsonStart = out.lastIndexOf('\n{');
      const jsonLine  = jsonStart >= 0 ? out.slice(jsonStart + 1) : out.trim();
      try { resolve(JSON.parse(jsonLine)); }
      catch { resolve({ error: err || 'Invalid JSON from Pressure', raw: out.slice(-500) }); }
    });
  });
}

export async function pythonPressureIdentify(p={})    { return runPressure('identify_zones',    p); }
export async function pythonPressureMandates(p={})    { return runPressure('generate_mandates', p); }
export async function pythonPressureCycle(p={})       { return runPressure('guided_cycle',      p); }
export async function pythonPressureReport(p={})      { return runPressure('pressure_report',   p); }
export async function pythonPressureBuildFull(p={})   { return runPressure('build_full',        p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 44 — Execution Reality Engine
// ══════════════════════════════════════════════════════════════════════════════
const EXEC_REALITY_SCRIPT = join(__dirname, '../../scripts/python/execution_reality_engine.py');

async function runExecReality(command, params = {}) {
  return new Promise((resolve) => {
    const args  = [EXEC_REALITY_SCRIPT, command, JSON.stringify(params)];
    const py    = spawn(PYTHON_BIN, args, { cwd: join(__dirname, '../..') });
    let out = '', err = '';
    const timer = setTimeout(() => { py.kill('SIGTERM'); resolve({ error: `ExecReality timeout (${command})` }); }, 600_000);
    py.stdout.on('data', d => { out += d.toString(); });
    py.stderr.on('data', d => { err += d.toString(); });
    py.on('close', () => {
      clearTimeout(timer);
      const jsonStart = out.lastIndexOf('\n{');
      const jsonLine  = jsonStart >= 0 ? out.slice(jsonStart + 1) : out.trim();
      try { resolve(JSON.parse(jsonLine)); }
      catch { resolve({ error: err || 'Invalid JSON from ExecReality', raw: out.slice(-500) }); }
    });
  });
}

export async function pythonExecSimulateEntry(p={})   { return runExecReality('simulate_entry',    p); }
export async function pythonExecSimulateExit(p={})    { return runExecReality('simulate_exit',     p); }
export async function pythonExecRealisticPnL(p={})    { return runExecReality('realistic_pnl',     p); }
export async function pythonExecCalendar(p={})        { return runExecReality('liquidity_calendar', p); }
export async function pythonExecRealityCheck(p={})    { return runExecReality('reality_check',     p); }
export async function pythonExecBuildFull(p={})       { return runExecReality('build_full',        p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 37 Extension — Enhanced Observatory Metrics
// ══════════════════════════════════════════════════════════════════════════════
export async function pythonObservatoryLatencyDrift(p={})       { return runObservatory('latency_drift',              p); }
export async function pythonObservatoryFreshness(p={})          { return runObservatory('freshness_degradation',      p); }
export async function pythonObservatoryRegimeDisagreement(p={}) { return runObservatory('regime_detector_disagreement', p); }
export async function pythonObservatoryCausalSpikes(p={})       { return runObservatory('causal_instability_spikes',  p); }
export async function pythonObservatoryEntropy(p={})            { return runObservatory('model_entropy',              p); }
export async function pythonObservatoryFragmentation(p={})      { return runObservatory('graph_fragmentation',        p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 47 — Multi-Horizon Intelligence Engine
// ══════════════════════════════════════════════════════════════════════════════
const MULTI_HORIZON_SCRIPT = join(__dirname, '../../scripts/python/multi_horizon_engine.py');

async function runMultiHorizon(command, params = {}) {
  return new Promise((resolve) => {
    const args  = [MULTI_HORIZON_SCRIPT, command, JSON.stringify(params)];
    const py    = spawn(PYTHON_BIN, args, { cwd: join(__dirname, '../..') });
    let out = '', err = '';
    const timer = setTimeout(() => { py.kill('SIGTERM'); resolve({ error: `MultiHorizon timeout (${command})` }); }, 600_000);
    py.stdout.on('data', d => { out += d.toString(); });
    py.stderr.on('data', d => { err += d.toString(); });
    py.on('close', () => {
      clearTimeout(timer);
      const jsonStart = out.lastIndexOf('\n{');
      const jsonLine  = jsonStart >= 0 ? out.slice(jsonStart + 1) : out.trim();
      try { resolve(JSON.parse(jsonLine)); }
      catch { resolve({ error: err || 'Invalid JSON from MultiHorizon', raw: out.slice(-500) }); }
    });
  });
}

export async function pythonHorizonAnalyze(p={})       { return runMultiHorizon('analyze_horizon',  p); }
export async function pythonHorizonMultiView(p={})     { return runMultiHorizon('multi_view',       p); }
export async function pythonHorizonConflict(p={})      { return runMultiHorizon('horizon_conflict', p); }
export async function pythonHorizonDominant(p={})      { return runMultiHorizon('dominant_signal',  p); }
export async function pythonHorizonBuildFull(p={})     { return runMultiHorizon('build_full',       p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 48 — Capital Intelligence Engine
// ══════════════════════════════════════════════════════════════════════════════
const CAPITAL_INTEL_SCRIPT = join(__dirname, '../../scripts/python/capital_intelligence.py');

async function runCapitalIntel(command, params = {}) {
  return new Promise((resolve) => {
    const args  = [CAPITAL_INTEL_SCRIPT, command, JSON.stringify(params)];
    const py    = spawn(PYTHON_BIN, args, { cwd: join(__dirname, '../..') });
    let out = '', err = '';
    const timer = setTimeout(() => { py.kill('SIGTERM'); resolve({ error: `CapitalIntel timeout (${command})` }); }, 600_000);
    py.stdout.on('data', d => { out += d.toString(); });
    py.stderr.on('data', d => { err += d.toString(); });
    py.on('close', () => {
      clearTimeout(timer);
      const jsonStart = out.lastIndexOf('\n{');
      const jsonLine  = jsonStart >= 0 ? out.slice(jsonStart + 1) : out.trim();
      try { resolve(JSON.parse(jsonLine)); }
      catch { resolve({ error: err || 'Invalid JSON from CapitalIntel', raw: out.slice(-500) }); }
    });
  });
}

export async function pythonCapitalExposure(p={})      { return runCapitalIntel('compute_exposure',      p); }
export async function pythonCapitalSizing(p={})        { return runCapitalIntel('size_with_uncertainty', p); }
export async function pythonCapitalDrawdown(p={})      { return runCapitalIntel('drawdown_state',        p); }
export async function pythonCapitalExploration(p={})   { return runCapitalIntel('exploration_budget',    p); }
export async function pythonCapitalReport(p={})        { return runCapitalIntel('capital_report',        p); }
export async function pythonCapitalBuildFull(p={})     { return runCapitalIntel('build_full',            p); }
export async function pythonObservatoryEnhanced(p={})  { return runObservatory('enhanced_health',        p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 49 — Deep History Engine
// ══════════════════════════════════════════════════════════════════════════════
const DEEP_HISTORY_SCRIPT = join(__dirname, '../../scripts/python/deep_history_engine.py');
function runDeepHistory(cmd, p={}) { return _runPy(DEEP_HISTORY_SCRIPT, cmd, p, 'DeepHistory'); }

export async function pythonDeepHistoryCoverage(p={})   { return runDeepHistory('history_coverage',              p); }
export async function pythonDeepHistoryRegime(p={})     { return runDeepHistory('long_term_regime',              p); }
export async function pythonDeepHistoryVolatility(p={}) { return runDeepHistory('historical_volatility_profile', p); }
export async function pythonDeepHistoryPattern(p={})    { return runDeepHistory('decade_pattern_match',          p); }
export async function pythonDeepHistoryCycles(p={})     { return runDeepHistory('cycle_analysis',                p); }
export async function pythonDeepHistorySector(p={})     { return runDeepHistory('sector_long_term',              p); }
export async function pythonDeepHistoryBuildFull(p={})  { return runDeepHistory('build_full',                    p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 50 — Intraday Intelligence Layer
// ══════════════════════════════════════════════════════════════════════════════
const INTRADAY_SCRIPT = join(__dirname, '../../scripts/python/intraday_intelligence.py');
function runIntraday(cmd, p={}) { return _runPy(INTRADAY_SCRIPT, cmd, p, 'Intraday'); }

export async function pythonIntradaySession(p={})       { return runIntraday('session_analytics',      p); }
export async function pythonIntradayCoverage(p={})      { return runIntraday('intraday_coverage',      p); }
export async function pythonIntradayWindow(p={})        { return runIntraday('execution_window',       p); }
export async function pythonIntradayGaps(p={})          { return runIntraday('opening_gap_analysis',   p); }
export async function pythonIntradayMomentum(p={})      { return runIntraday('intraday_momentum',      p); }
export async function pythonIntradayBuildProfiles(p={}) { return runIntraday('build_session_profiles', p); }
export async function pythonIntradayBuildFull(p={})     { return runIntraday('build_full',             p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 51 — Cross-Market Coupling Engine
// ══════════════════════════════════════════════════════════════════════════════
const CROSS_MARKET_SCRIPT = join(__dirname, '../../scripts/python/cross_market_engine.py');
function runCrossMarket(cmd, p={}) { return _runPy(CROSS_MARKET_SCRIPT, cmd, p, 'CrossMarket'); }

export async function pythonCrossMarketCoverage(p={})   { return runCrossMarket('market_coverage',    p); }
export async function pythonCrossMarketRiskOn(p={})     { return runCrossMarket('risk_on_score',      p); }
export async function pythonCrossMarketUsdEgp(p={})     { return runCrossMarket('usdegp_regime',      p); }
export async function pythonCrossMarketCoupling(p={})   { return runCrossMarket('coupling_matrix',    p); }
export async function pythonCrossMarketMacro(p={})      { return runCrossMarket('macro_regime',       p); }
export async function pythonCrossMarketContext(p={})    { return runCrossMarket('daily_context',      p); }
export async function pythonCrossMarketBuildFull(p={})  { return runCrossMarket('build_full',         p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 52 — Liquidity Microstructure Engine
// ══════════════════════════════════════════════════════════════════════════════
const LIQUIDITY_SCRIPT = join(__dirname, '../../scripts/python/liquidity_microstructure.py');
function runLiquidity(cmd, p={}) { return _runPy(LIQUIDITY_SCRIPT, cmd, p, 'Liquidity'); }

export async function pythonLiquiditySymbol(p={})       { return runLiquidity('compute_symbol_liquidity', p); }
export async function pythonLiquidityTiers(p={})        { return runLiquidity('tier_classification',      p); }
export async function pythonLiquidityFilter(p={})       { return runLiquidity('liquidity_filter',         p); }
export async function pythonLiquidityMaxSize(p={})      { return runLiquidity('max_position_size',        p); }
export async function pythonLiquidityBuildProfiles(p={}) { return runLiquidity('build_liquidity_profiles', p); }
export async function pythonLiquidityReport(p={})       { return runLiquidity('liquidity_report',         p); }
export async function pythonLiquidityBuildFull(p={})    { return runLiquidity('build_full',               p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 53 — Pine Analytics Bridge
// ══════════════════════════════════════════════════════════════════════════════
const PINE_ANALYTICS_SCRIPT = join(__dirname, '../../scripts/python/pine_analytics_bridge.py');
function runPineAnalytics(cmd, p={}) { return _runPy(PINE_ANALYTICS_SCRIPT, cmd, p, 'PineAnalytics'); }

export async function pythonPineStore(p={})             { return runPineAnalytics('store_pine_data',         p); }
export async function pythonPineVolumeProfile(p={})     { return runPineAnalytics('volume_profile_analysis', p); }
export async function pythonPineRSRanking(p={})         { return runPineAnalytics('rs_ranking',              p); }
export async function pythonPineVWAP(p={})              { return runPineAnalytics('vwap_position',           p); }
export async function pythonPineCorpEvents(p={})        { return runPineAnalytics('corporate_event_scan',    p); }
export async function pythonPineCoverage(p={})          { return runPineAnalytics('pine_data_coverage',      p); }
export async function pythonPineBuildFull(p={})         { return runPineAnalytics('build_full',              p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 54 — Corporate Actions Tracker
// ══════════════════════════════════════════════════════════════════════════════
const CORP_ACTIONS_SCRIPT = join(__dirname, '../../scripts/python/corporate_actions_tracker.py');
function runCorpActions(cmd, p={}) { return _runPy(CORP_ACTIONS_SCRIPT, cmd, p, 'CorpActions'); }

export async function pythonCorpScanSymbol(p={})        { return runCorpActions('scan_symbol',              p); }
export async function pythonCorpScanAll(p={})           { return runCorpActions('scan_all',                 p); }
export async function pythonCorpListEvents(p={})        { return runCorpActions('list_events',              p); }
export async function pythonCorpConfirm(p={})           { return runCorpActions('confirm_event',            p); }
export async function pythonCorpImpact(p={})            { return runCorpActions('impact_analysis',          p); }
export async function pythonCorpWarning(p={})           { return runCorpActions('unadjusted_data_warning',  p); }
export async function pythonCorpBuildFull(p={})         { return runCorpActions('build_full',               p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 55 — Unified Data Quality Gate
// ══════════════════════════════════════════════════════════════════════════════
const DATA_QUALITY_SCRIPT = join(__dirname, '../../scripts/python/data_quality_gate.py');
function runDataQuality(cmd, p={}) { return _runPy(DATA_QUALITY_SCRIPT, cmd, p, 'DataQuality'); }

export async function pythonQualityOHLCV(p={})          { return runDataQuality('check_ohlcv_integrity',   p); }
export async function pythonQualityGaps(p={})           { return runDataQuality('check_timestamp_gaps',    p); }
export async function pythonQualityContinuity(p={})     { return runDataQuality('check_price_continuity',  p); }
export async function pythonQualityStale(p={})          { return runDataQuality('check_stale_data',        p); }
export async function pythonQualityFullAudit(p={})      { return runDataQuality('full_audit',              p); }
export async function pythonQualityTrustScores(p={})    { return runDataQuality('get_trust_scores',        p); }
export async function pythonQualityOpenIssues(p={})     { return runDataQuality('get_open_issues',         p); }
export async function pythonQualityQuarantine(p={})     { return runDataQuality('quarantine_symbol',       p); }
export async function pythonQualityQuarantined(p={})    { return runDataQuality('get_quarantined_symbols', p); }
export async function pythonQualityBuildFull(p={})      { return runDataQuality('build_full',              p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 56 — Market Breadth Engine
// ══════════════════════════════════════════════════════════════════════════════
const MARKET_BREADTH_SCRIPT = join(__dirname, '../../scripts/python/market_breadth_engine.py');
function runBreadth(cmd, p={}) { return _runPy(MARKET_BREADTH_SCRIPT, cmd, p, 'Breadth'); }

export async function pythonBreadthCompute(p={})        { return runBreadth('compute_breadth',  p); }
export async function pythonBreadthAD(p={})             { return runBreadth('advance_decline',  p); }
export async function pythonBreadthMA(p={})             { return runBreadth('ma_breadth',       p); }
export async function pythonBreadthHighsLows(p={})      { return runBreadth('new_highs_lows',   p); }
export async function pythonBreadthMcClellan(p={})      { return runBreadth('mcclellan',        p); }
export async function pythonBreadthSector(p={})         { return runBreadth('sector_breadth',   p); }
export async function pythonBreadthSignal(p={})         { return runBreadth('breadth_signal',   p); }
export async function pythonBreadthHistory(p={})        { return runBreadth('history',          p); }
export async function pythonBreadthBuildFull(p={})      { return runBreadth('build_full',       p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 57 — Alert Automation
// ══════════════════════════════════════════════════════════════════════════════
const ALERT_AUTOMATION_SCRIPT = join(__dirname, '../../scripts/python/alert_automation.py');
function runAlerts(cmd, p={}) { return _runPy(ALERT_AUTOMATION_SCRIPT, cmd, p, 'Alerts'); }

export async function pythonAlertGetTargets(p={})       { return runAlerts('get_alert_targets', p); }
export async function pythonAlertLogCreated(p={})       { return runAlerts('log_created',       p); }
export async function pythonAlertListActive(p={})       { return runAlerts('list_active',       p); }
export async function pythonAlertSyncStatus(p={})       { return runAlerts('sync_status',       p); }
export async function pythonAlertClearExpired(p={})     { return runAlerts('clear_expired',     p); }
export async function pythonAlertSummary(p={})          { return runAlerts('summary',           p); }
export async function pythonAlertBuildFull(p={})        { return runAlerts('build_full',        p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 58 — Technical Indicator Confluence
// ══════════════════════════════════════════════════════════════════════════════
const TECH_CONFLUENCE_SCRIPT = join(__dirname, '../../scripts/python/technical_confluence.py');
function runTech(cmd, p={}) { return _runPy(TECH_CONFLUENCE_SCRIPT, cmd, p, 'TechConfluence'); }

export async function pythonTechSaveIndicators(p={})    { return runTech('save_indicators',     p); }
export async function pythonTechScoreSymbol(p={})       { return runTech('score_symbol',        p); }
export async function pythonTechScoreBatch(p={})        { return runTech('score_batch',         p); }
export async function pythonTechReport(p={})            { return runTech('confluence_report',   p); }
export async function pythonTechCoverage(p={})          { return runTech('coverage',            p); }
export async function pythonTechBuildFull(p={})         { return runTech('build_full',          p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 59 — Strategy Tester Integration
// ══════════════════════════════════════════════════════════════════════════════
const STRATEGY_TESTER_SCRIPT = join(__dirname, '../../scripts/python/strategy_tester.py');
function runStrategy(cmd, p={}) { return _runPy(STRATEGY_TESTER_SCRIPT, cmd, p, 'StrategyTester'); }

export async function pythonStrategyGenerate(p={})      { return runStrategy('generate_law_strategy',   p); }
export async function pythonStrategyList(p={})          { return runStrategy('list_laws_for_testing',   p); }
export async function pythonStrategyParse(p={})         { return runStrategy('parse_backtest_results',  p); }
export async function pythonStrategyValidate(p={})      { return runStrategy('validate_law',            p); }
export async function pythonStrategyRank(p={})          { return runStrategy('rank_laws',               p); }
export async function pythonStrategyBuildFull(p={})     { return runStrategy('build_full',              p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 60 — Chart Visualizer + Auto Drawing
// ══════════════════════════════════════════════════════════════════════════════
const CHART_VISUALIZER_SCRIPT = join(__dirname, '../../scripts/python/chart_visualizer.py');
function runViz(cmd, p={}) { return _runPy(CHART_VISUALIZER_SCRIPT, cmd, p, 'ChartViz'); }

export async function pythonVizGetDrawSpecs(p={})       { return runViz('get_draw_specs',       p); }
export async function pythonVizGetTopPicksDraws(p={})   { return runViz('get_top_picks_draws',  p); }
export async function pythonVizLogScreenshot(p={})      { return runViz('log_screenshot',       p); }
export async function pythonVizFinalizeReport(p={})     { return runViz('finalize_report',      p); }
export async function pythonVizListScreenshots(p={})    { return runViz('list_screenshots',     p); }
export async function pythonVizReportSummary(p={})      { return runViz('report_summary',       p); }
export async function pythonVizBuildFull(p={})          { return runViz('build_full',           p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 61 — Real-time Intraday Monitor + DOM
// ══════════════════════════════════════════════════════════════════════════════
const INTRADAY_MONITOR_SCRIPT = join(__dirname, '../../scripts/python/intraday_monitor.py');
function runMonitor(cmd, p={}) { return _runPy(INTRADAY_MONITOR_SCRIPT, cmd, p, 'IntradayMonitor'); }

export async function pythonMonitorSessionStatus(p={})  { return runMonitor('session_status',    p); }
export async function pythonMonitorSaveDom(p={})        { return runMonitor('save_dom_snapshot', p); }
export async function pythonMonitorSaveQuotes(p={})     { return runMonitor('save_live_quotes',  p); }
export async function pythonMonitorExecution(p={})      { return runMonitor('execution_timing',  p); }
export async function pythonMonitorSpread(p={})         { return runMonitor('compute_spread',    p); }
export async function pythonMonitorLiveSnapshot(p={})   { return runMonitor('live_snapshot',     p); }
export async function pythonMonitorBuildFull(p={})      { return runMonitor('build_full',        p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 62 — Feature Factory (300+ computed features)
// ══════════════════════════════════════════════════════════════════════════════
const FEATURE_FACTORY_SCRIPT = join(__dirname, '../../scripts/python/feature_factory.py');
function runFeatures(cmd, p={}) { return _runPy(FEATURE_FACTORY_SCRIPT, cmd, p, 'FeatureFactory'); }

export async function pythonFeatBuildFeatures(p={})  { return runFeatures('build_features',     p); }
export async function pythonFeatGetFeatures(p={})    { return runFeatures('get_features',        p); }
export async function pythonFeatImportance(p={})     { return runFeatures('feature_importance',  p); }
export async function pythonFeatCoverage(p={})       { return runFeatures('coverage',            p); }
export async function pythonFeatBuildFull(p={})      { return runFeatures('build_full',          p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 63 — Explosion ML (LightGBM binary classifier)
// ══════════════════════════════════════════════════════════════════════════════
const EXPLOSION_ML_SCRIPT = join(__dirname, '../../scripts/python/explosion_ml.py');
function runExplosionML(cmd, p={}) { return _runPy(EXPLOSION_ML_SCRIPT, cmd, p, 'ExplosionML'); }

export async function pythonMLTrain(p={})            { return runExplosionML('train',             p); }
export async function pythonMLOptunaTune(p={})       { return runExplosionML('optuna_tune',       p); }
export async function pythonMLPredictToday(p={})     { return runExplosionML('predict_today',     p); }
export async function pythonMLPredictSymbol(p={})    { return runExplosionML('predict_symbol',    p); }
export async function pythonMLEvaluate(p={})         { return runExplosionML('evaluate',          p); }
export async function pythonMLFeatureImportance(p={}) { return runExplosionML('feature_importance', p); }
export async function pythonMLShapExplain(p={})      { return runExplosionML('shap_explain',      p); }
export async function pythonMLBuildFull(p={})        { return runExplosionML('build_full',        p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 73 — Portfolio Optimizer (PyPortfolioOpt + Kelly)
// ══════════════════════════════════════════════════════════════════════════════
const PORTFOLIO_OPT_SCRIPT = join(__dirname, '../../scripts/python/portfolio_optimizer.py');
function runPortOpt(cmd, p={}) { return _runPy(PORTFOLIO_OPT_SCRIPT, cmd, p, 'PortfolioOpt'); }

export async function pythonPortKelly(p={})      { return runPortOpt('kelly_sizing', p); }
export async function pythonPortMaxSharpe(p={})  { return runPortOpt('max_sharpe',   p); }
export async function pythonPortRiskParity(p={}) { return runPortOpt('risk_parity',  p); }
export async function pythonPortReport(p={})     { return runPortOpt('report',       p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 74 — Walk-Forward Lab + Monte Carlo
// ══════════════════════════════════════════════════════════════════════════════
const WF_LAB_SCRIPT = join(__dirname, '../../scripts/python/walk_forward_lab.py');
function runWFLab(cmd, p={}) { return _runPy(WF_LAB_SCRIPT, cmd, p, 'WFLab'); }

export async function pythonWFSignals(p={})       { return runWFLab('wf_signals',      p); }
export async function pythonWFLaws(p={})          { return runWFLab('wf_laws',         p); }
export async function pythonWFMonteCarlo(p={})    { return runWFLab('monte_carlo',     p); }
export async function pythonWFParamStability(p={}) { return runWFLab('param_stability', p); }
export async function pythonWFReport(p={})        { return runWFLab('report',          p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 75 — Hidden Regime HMM
// ══════════════════════════════════════════════════════════════════════════════
const HMM_SCRIPT = join(__dirname, '../../scripts/python/hidden_regime_hmm.py');
function runHMM(cmd, p={}) { return _runPy(HMM_SCRIPT, cmd, p, 'HiddenRegime'); }

export async function pythonHMMFit(p={})                { return runHMM('fit',                   p); }
export async function pythonHMMDetect(p={})             { return runHMM('detect',                p); }
export async function pythonHMMHistory(p={})            { return runHMM('history',               p); }
export async function pythonHMMExplosionCorr(p={})      { return runHMM('explosion_correlation', p); }
export async function pythonHMMReport(p={})             { return runHMM('report',                p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 76 — Genetic Strategy Evolution (DEAP)
// ══════════════════════════════════════════════════════════════════════════════
const GENETIC_SCRIPT = join(__dirname, '../../scripts/python/genetic_strategy_evolution.py');
function runGenetic(cmd, p={}) { return _runPyLong(GENETIC_SCRIPT, cmd, p, 'GeneticEvolution', 600_000); }

export async function pythonGeneticEvolve(p={})        { return runGenetic('evolve',         p); }
export async function pythonGeneticTop(p={})           { return runGenetic('top_strategies', p); }
export async function pythonGeneticValidate(p={})      { return runGenetic('validate',       p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 77 — tsfresh Feature Extraction
// ══════════════════════════════════════════════════════════════════════════════
const TSFRESH_SCRIPT = join(__dirname, '../../scripts/python/tsfresh_features.py');
function runTsfresh(cmd, p={}) { return _runPyLong(TSFRESH_SCRIPT, cmd, p, 'TSFresh', 300_000); }

export async function pythonTsfreshSymbols(p={})      { return runTsfresh('extract_symbols',    p); }
export async function pythonTsfreshExplosions(p={})   { return runTsfresh('extract_explosions', p); }
export async function pythonTsfreshCompare(p={})      { return runTsfresh('compare_importance', p); }
export async function pythonTsfreshReport(p={})       { return runTsfresh('report',             p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 78 — Causal Discovery (Granger + MI)
// ══════════════════════════════════════════════════════════════════════════════
const CAUSAL_DISCOVERY_SCRIPT = join(__dirname, '../../scripts/python/causal_discovery.py');
function runCausal(cmd, p={}) { return _runPy(CAUSAL_DISCOVERY_SCRIPT, cmd, p, 'CausalDiscovery'); }

export async function pythonCausalGranger(p={})  { return runCausal('granger_test', p); }
export async function pythonCausalLag(p={})      { return runCausal('lag_analysis', p); }
export async function pythonCausalMI(p={})       { return runCausal('mi_matrix',    p); }
export async function pythonCausalReport(p={})   { return runCausal('report',       p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 80 — Triple Barrier Labeling + Meta Labeling
// ══════════════════════════════════════════════════════════════════════════════
const TRIPLE_BARRIER_SCRIPT = join(__dirname, '../../scripts/python/triple_barrier.py');
function runTB(cmd, p={}) { return _runPy(TRIPLE_BARRIER_SCRIPT, cmd, p, 'TripleBarrier'); }
function runTBLong(cmd, p={}) { return _runPyLong(TRIPLE_BARRIER_SCRIPT, cmd, p, 'TripleBarrier'); }

export async function pythonTBLabel(p={})      { return runTBLong('label',      p); }
export async function pythonTBMetaLabel(p={})  { return runTBLong('meta_label', p); }
export async function pythonTBPurgedCV(p={})   { return runTBLong('purged_cv',  p); }
export async function pythonTBStability(p={})  { return runTBLong('stability',  p); }
export async function pythonTBBetSizing(p={})  { return runTB('bet_sizing',     p); }
export async function pythonTBReport(p={})     { return runTBLong('report',     p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 81 — MLflow Experiment Tracking
// ══════════════════════════════════════════════════════════════════════════════
const MLFLOW_SCRIPT = join(__dirname, '../../scripts/python/mlflow_tracker.py');
function runMLflow(cmd, p={}) { return _runPy(MLFLOW_SCRIPT, cmd, p, 'MLflow'); }
function runMLflowLong(cmd, p={}) { return _runPyLong(MLFLOW_SCRIPT, cmd, p, 'MLflow'); }

export async function pythonMLflowInit(p={})          { return runMLflow('init',           p); }
export async function pythonMLflowLogRun(p={})        { return runMLflowLong('log_run',    p); }
export async function pythonMLflowLogRegime(p={})     { return runMLflow('log_regime_run', p); }
export async function pythonMLflowCompare(p={})       { return runMLflow('compare',        p); }
export async function pythonMLflowRegister(p={})      { return runMLflow('register',       p); }
export async function pythonMLflowReport(p={})        { return runMLflow('report',         p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 82 — Event-Driven Backtesting
// ══════════════════════════════════════════════════════════════════════════════
const EVENT_BT_SCRIPT = join(__dirname, '../../scripts/python/event_backtest.py');
function runEventBT(cmd, p={}) { return _runPy(EVENT_BT_SCRIPT, cmd, p, 'EventBT'); }
function runEventBTLong(cmd, p={}) { return _runPyLong(EVENT_BT_SCRIPT, cmd, p, 'EventBT'); }

export async function pythonBTRunStrategy(p={})    { return runEventBT('run_strategy',       p); }
export async function pythonBTPortfolio(p={})      { return runEventBTLong('portfolio_backtest', p); }
export async function pythonBTWalkForward(p={})    { return runEventBTLong('walk_forward_bt', p); }
export async function pythonBTExecCost(p={})       { return runEventBT('execution_cost',     p); }
export async function pythonBTReport(p={})         { return runEventBT('report',             p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 83 — Regime Transition Prediction
// ══════════════════════════════════════════════════════════════════════════════
const REGIME_TRANS_SCRIPT = join(__dirname, '../../scripts/python/regime_transition.py');
function runRegimeTrans(cmd, p={}) { return _runPy(REGIME_TRANS_SCRIPT, cmd, p, 'RegimeTrans'); }

export async function pythonRegimeTransMatrix(p={})    { return runRegimeTrans('transition_matrix',      p); }
export async function pythonRegimeTransLeading(p={})   { return runRegimeTrans('leading_indicators',     p); }
export async function pythonRegimeTransWarning(p={})   { return runRegimeTrans('early_warning',          p); }
export async function pythonRegimeTransForecast(p={})  { return runRegimeTrans('forecast',               p); }
export async function pythonRegimeTransVolAccel(p={})  { return runRegimeTrans('volatility_acceleration', p); }
export async function pythonRegimeTransReport(p={})    { return runRegimeTrans('report',                 p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 84 — Feature Store
// ══════════════════════════════════════════════════════════════════════════════
const FEATURE_STORE_SCRIPT = join(__dirname, '../../scripts/python/feature_store.py');
function runFS(cmd, p={}) { return _runPy(FEATURE_STORE_SCRIPT, cmd, p, 'FeatureStore'); }
function runFSLong(cmd, p={}) { return _runPyLong(FEATURE_STORE_SCRIPT, cmd, p, 'FeatureStore'); }

export async function pythonFSRefresh(p={})     { return runFSLong('refresh',      p); }
export async function pythonFSGet(p={})         { return runFS('get_features',     p); }
export async function pythonFSDrift(p={})       { return runFS('drift_report',     p); }
export async function pythonFSLineage(p={})     { return runFS('lineage',          p); }
export async function pythonFSReport(p={})      { return runFSLong('report',       p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 79 — Regime-Specific ML Models
// ══════════════════════════════════════════════════════════════════════════════
const REGIME_ML_SCRIPT = join(__dirname, '../../scripts/python/regime_specific_ml.py');
function runRegimeML(cmd, p={}) { return _runPy(REGIME_ML_SCRIPT, cmd, p, 'RegimeML'); }
function runRegimeMLLong(cmd, p={}) { return _runPyLong(REGIME_ML_SCRIPT, cmd, p, 'RegimeML'); }

export async function pythonRegimeMLAssign(p={})      { return runRegimeML('assign_regimes',     p); }
export async function pythonRegimeMLTrain(p={})       { return runRegimeMLLong('train',           p); }
export async function pythonRegimeMLEvaluate(p={})    { return runRegimeML('evaluate',            p); }
export async function pythonRegimeMLPredict(p={})     { return runRegimeML('predict',             p); }
export async function pythonRegimeMLAdversarial(p={}) { return runRegimeML('adversarial',         p); }
export async function pythonRegimeMLImportance(p={})  { return runRegimeML('regime_importance',   p); }
export async function pythonRegimeMLReport(p={})      { return runRegimeMLLong('report',          p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 64 — Regime-Conditional Laws
// ══════════════════════════════════════════════════════════════════════════════
const REGIME_LAWS_SCRIPT = join(__dirname, '../../scripts/python/regime_laws.py');
function runRegimeLaws(cmd, p={}) { return _runPy(REGIME_LAWS_SCRIPT, cmd, p, 'RegimeLaws'); }

export async function pythonRegimeAnalyze(p={})      { return runRegimeLaws('analyze_conditions',   p); }
export async function pythonRegimeSignals(p={})      { return runRegimeLaws('conditioned_signals',  p); }
export async function pythonRegimeLawMatrix(p={})    { return runRegimeLaws('law_matrix',           p); }
export async function pythonRegimeUpdate(p={})       { return runRegimeLaws('update_conditions',    p); }
export async function pythonRegimeBuildFull(p={})    { return runRegimeLaws('build_full',           p); }
export async function pythonRegimePopulateMut(p={}) { return runRegimeLaws('populate_mut_regime',  p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 65 — Signal Integration Layer (Unified Evidence Score)
// ══════════════════════════════════════════════════════════════════════════════
const SIGNAL_INTEGRATION_SCRIPT = join(__dirname, '../../scripts/python/signal_integration.py');
function runSignal(cmd, p={}) { return _runPy(SIGNAL_INTEGRATION_SCRIPT, cmd, p, 'SignalIntegration'); }

export async function pythonSigScoreSymbol(p={})     { return runSignal('score_symbol',       p); }
export async function pythonSigScoreAll(p={})        { return runSignal('score_all',          p); }
export async function pythonSigDailySignals(p={})    { return runSignal('daily_signals',      p); }
export async function pythonSigConviction(p={})      { return runSignal('conviction_filter',  p); }
export async function pythonSigHistory(p={})         { return runSignal('score_history',      p); }
export async function pythonSigBuildFull(p={})       { return runSignal('build_full',         p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 66 — Realistic EGX Backtesting (with transaction costs)
// ══════════════════════════════════════════════════════════════════════════════
const REALISTIC_BACKTEST_SCRIPT = join(__dirname, '../../scripts/python/realistic_backtest.py');
function runRealBT(cmd, p={}) { return _runPy(REALISTIC_BACKTEST_SCRIPT, cmd, p, 'RealisticBacktest'); }

export async function pythonBTSymbol(p={})           { return runRealBT('backtest_symbol',    p); }
export async function pythonBTUniverse(p={})         { return runRealBT('backtest_universe',  p); }
export async function pythonBTOOS(p={})              { return runRealBT('oos_validation',     p); }
export async function pythonBTCompareLaws(p={})      { return runRealBT('compare_laws',       p); }
export async function pythonBTCostHurdle(p={})       { return runRealBT('law_cost_hurdle',    p); }
export async function pythonBTBuildFull(p={})        { return runRealBT('build_full',         p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 67 — Scientific Refinement Cycle
// ══════════════════════════════════════════════════════════════════════════════
const REFINEMENT_CYCLE_SCRIPT = join(__dirname, '../../scripts/python/refinement_cycle.py');
function runRefine(cmd, p={}) { return _runPy(REFINEMENT_CYCLE_SCRIPT, cmd, p, 'RefinementCycle'); }

export async function pythonRefineMeasure(p={})      { return runRefine('measure',        p); }
export async function pythonRefinePrune(p={})        { return runRefine('prune',          p); }
export async function pythonRefineCondition(p={})    { return runRefine('condition',      p); }
export async function pythonRefineSynthesize(p={})   { return runRefine('synthesize',     p); }
export async function pythonRefineRunCycle(p={})     { return runRefine('run_cycle',      p); }
export async function pythonRefineHistory(p={})      { return runRefine('cycle_history',  p); }
export async function pythonRefineBuildFull(p={})    { return runRefine('build_full',     p); }

// ── Shared runner helper (DRY) ───────────────────────────────────────────────
function _runPy(scriptPath, command, params = {}, label = 'Py') {
  return new Promise((resolve) => {
    const args  = [scriptPath, command, JSON.stringify(params)];
    const py    = spawn(PYTHON_BIN, args, { cwd: join(__dirname, '../..') });
    let out = '', err = '';
    const timer = setTimeout(() => { py.kill('SIGTERM'); resolve({ error: `${label} timeout (${command})` }); }, 600_000);
    py.stdout.on('data', d => { out += d.toString(); });
    py.stderr.on('data', d => { err += d.toString(); });
    py.on('close', () => {
      clearTimeout(timer);
      const jsonStart = out.lastIndexOf('\n{');
      const jsonLine  = jsonStart >= 0 ? out.slice(jsonStart + 1) : out.trim();
      try { resolve(JSON.parse(jsonLine)); }
      catch { resolve({ error: err || `Invalid JSON from ${label}`, raw: out.slice(-500) }); }
    });
  });
}

// ══════════════════════════════════════════════════════════════════════════════
// Phase 68 — Hypothesis DSL Engine
// ══════════════════════════════════════════════════════════════════════════════
const HYPOTHESIS_DSL_SCRIPT = join(__dirname, '../../scripts/python/hypothesis_dsl.py');
function runHypDSL(cmd, p={}) { return _runPy(HYPOTHESIS_DSL_SCRIPT, cmd, p, 'HypDSL'); }

export async function pythonHypGenerate(p={})     { return runHypDSL('generate',        p); }
export async function pythonHypList(p={})         { return runHypDSL('list_hypotheses', p); }
export async function pythonHypAdd(p={})          { return runHypDSL('add_hypothesis',  p); }
export async function pythonHypEvaluate(p={})     { return runHypDSL('evaluate',        p); }
export async function pythonHypBuildFull(p={})    { return runHypDSL('build_full',      p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 69 — Research Grid
// ══════════════════════════════════════════════════════════════════════════════
const RESEARCH_GRID_SCRIPT = join(__dirname, '../../scripts/python/research_grid.py');
function runGrid(cmd, p={}) { return _runPy(RESEARCH_GRID_SCRIPT, cmd, p, 'ResearchGrid'); }

export async function pythonGridRun(p={})         { return runGrid('run_grid',             p); }
export async function pythonGridRunSingle(p={})   { return runGrid('run_single',           p); }
export async function pythonGridStatus(p={})      { return runGrid('status',               p); }
export async function pythonGridTopResults(p={})  { return runGrid('top_results',          p); }
export async function pythonGridBuildFull(p={})   { return runGrid('build_full',           p); }
export async function pythonGridVbtBacktest(p={}) { return runGrid('vbt_backtest_signals', p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 70 — Alpha Ranker + Decay Monitor
// ══════════════════════════════════════════════════════════════════════════════
const ALPHA_RANKER_SCRIPT = join(__dirname, '../../scripts/python/alpha_ranker.py');
function runAlphaRanker(cmd, p={}) { return _runPy(ALPHA_RANKER_SCRIPT, cmd, p, 'AlphaRanker'); }

export async function pythonAlphaRankAll(p={})    { return runAlphaRanker('rank_all',    p); }
export async function pythonAlphaKill(p={})       { return runAlphaRanker('kill_weak',   p); }
export async function pythonAlphaDecay(p={})      { return runAlphaRanker('decay_check', p); }
export async function pythonAlphaLeader(p={})     { return runAlphaRanker('leaderboard', p); }
export async function pythonAlphaEvolve(p={})     { return runAlphaRanker('evolve',      p); }
export async function pythonAlphaBuildFull(p={})  { return runAlphaRanker('build_full',  p); }

// ══════════════════════════════════════════════════════════════════════════════
// Phase 71 — Autonomous Research Director
// ══════════════════════════════════════════════════════════════════════════════
const RESEARCH_DIRECTOR_SCRIPT = join(__dirname, '../../scripts/python/research_director.py');
function _runPyLong(scriptPath, command, params = {}, label = 'Py', timeoutMs = 1_800_000) {
  return new Promise((resolve) => {
    const args  = [scriptPath, command, JSON.stringify(params)];
    const py    = spawn(PYTHON_BIN, args, { cwd: join(__dirname, '../..') });
    let out = '', err = '';
    const timer = setTimeout(() => { py.kill('SIGTERM'); resolve({ error: `${label} timeout (${command})` }); }, timeoutMs);
    py.stdout.on('data', d => { out += d.toString(); });
    py.stderr.on('data', d => { err += d.toString(); });
    py.on('close', () => {
      clearTimeout(timer);
      const jsonStart = out.lastIndexOf('\n{');
      const jsonLine  = jsonStart >= 0 ? out.slice(jsonStart + 1) : out.trim();
      try { resolve(JSON.parse(jsonLine)); }
      catch { resolve({ error: err || `Invalid JSON from ${label}`, raw: out.slice(-500) }); }
    });
  });
}
function runDirector(cmd, p={}) { return _runPyLong(RESEARCH_DIRECTOR_SCRIPT, cmd, p, 'ResearchDirector'); }

export async function pythonDirectorMorning(p={})  { return runDirector('morning_run',     p); }
export async function pythonDirectorStatus(p={})   { return runDirector('status',          p); }
export async function pythonDirectorTopAlpha(p={}) { return runDirector('top_alpha',       p); }
export async function pythonDirectorHistory(p={})  { return runDirector('history',         p); }
export async function pythonDirectorReport(p={})   { return runDirector('generate_report', p); }
export async function pythonDirectorBuildFull(p={}) { return runDirector('build_full',     p); }
