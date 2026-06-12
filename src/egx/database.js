/**
 * EGX Trade Database
 * ===================
 * قاعدة بيانات SQLite لحفظ كل صفقة، scan، post-mortem، ودرس مكتسب
 * تُبنى الذاكرة التراكمية هنا
 *
 * المالك: Dr. Husam | آخر تحديث: 3 مايو 2026
 */

import { createRequire } from 'module';
import { existsSync, mkdirSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const require  = createRequire(import.meta.url);
const Database = require('better-sqlite3');

const __dirname = dirname(fileURLToPath(import.meta.url));
const DB_DIR    = join(__dirname, '../../data');
const DB_PATH   = join(DB_DIR, 'egx_trading.db');

// ─── إنشاء مجلد البيانات إذا لم يوجد ─────────────────────────────────────
if (!existsSync(DB_DIR)) mkdirSync(DB_DIR, { recursive: true });

// ─── فتح قاعدة البيانات ───────────────────────────────────────────────────
let db;
export function getDB() {
  if (!db) {
    db = new Database(DB_PATH);
    db.pragma('journal_mode = WAL');
    db.pragma('foreign_keys = ON');
    initSchema(db);
  }
  return db;
}

// ─── Schema ──────────────────────────────────────────────────────────────
function initSchema(db) {
  db.exec(`
    -- ── جلسات المسح ──────────────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS scans (
      id            INTEGER PRIMARY KEY AUTOINCREMENT,
      scan_date     TEXT    NOT NULL,
      symbol        TEXT    NOT NULL,
      setup_type    TEXT,
      setup_id      TEXT,
      score         INTEGER DEFAULT 0,
      grade         TEXT,
      priority      INTEGER DEFAULT 99,
      entry_low     REAL,
      entry_high    REAL,
      stop_loss     REAL,
      t1            REAL,
      t2            REAL,
      rr1           REAL,
      rr2           REAL,
      volume_ratio  REAL,
      avg_volume    REAL,
      close_price   REAL,
      confidence    REAL,
      rejected      INTEGER DEFAULT 0,
      rejection_reasons TEXT,
      is_best_safe       INTEGER DEFAULT 0,
      is_best_aggressive INTEGER DEFAULT 0,
      notes         TEXT,
      created_at    TEXT DEFAULT (datetime('now'))
    );

    -- ── سجل الصفقات المنفّذة ──────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS trades (
      id            INTEGER PRIMARY KEY AUTOINCREMENT,
      scan_id       INTEGER REFERENCES scans(id),
      scan_date     TEXT    NOT NULL,
      symbol        TEXT    NOT NULL,
      setup_type    TEXT,
      entry_price   REAL,
      entry_date    TEXT,
      exit_price    REAL,
      exit_date     TEXT,
      pnl_pct       REAL,
      pnl_egp       REAL,
      position_size REAL,
      result        TEXT CHECK(result IN ('win','loss','breakeven','open')),
      hit_t1        INTEGER DEFAULT 0,
      hit_t2        INTEGER DEFAULT 0,
      hit_sl        INTEGER DEFAULT 0,
      hold_days     INTEGER DEFAULT 0,
      exit_reason   TEXT,
      notes         TEXT,
      created_at    TEXT DEFAULT (datetime('now'))
    );

    -- ── سجل الـ Post-Mortem ───────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS postmortems (
      id            INTEGER PRIMARY KEY AUTOINCREMENT,
      session_date  TEXT    NOT NULL,
      total_trades  INTEGER DEFAULT 0,
      wins          INTEGER DEFAULT 0,
      losses        INTEGER DEFAULT 0,
      breakevens    INTEGER DEFAULT 0,
      win_rate      REAL,
      avg_win_pct   REAL,
      avg_loss_pct  REAL,
      best_trade    TEXT,
      worst_trade   TEXT,
      best_pnl_pct  REAL,
      worst_pnl_pct REAL,
      key_lessons   TEXT,
      created_at    TEXT DEFAULT (datetime('now'))
    );

    -- ── أداء الإعدادات (للتعلم) ──────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS setup_performance (
      id            INTEGER PRIMARY KEY AUTOINCREMENT,
      setup_type    TEXT    NOT NULL,
      day_of_week   INTEGER,
      day_name      TEXT,
      total_trades  INTEGER DEFAULT 0,
      wins          INTEGER DEFAULT 0,
      losses        INTEGER DEFAULT 0,
      win_rate      REAL    DEFAULT 0,
      avg_win_pct   REAL    DEFAULT 0,
      avg_loss_pct  REAL    DEFAULT 0,
      avg_pnl       REAL    DEFAULT 0,
      best_pnl      REAL    DEFAULT 0,
      worst_pnl     REAL    DEFAULT 0,
      last_updated  TEXT    DEFAULT (datetime('now')),
      UNIQUE(setup_type, day_of_week)
    );

    -- ── الدروس المكتسبة ───────────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS lessons (
      id              INTEGER PRIMARY KEY AUTOINCREMENT,
      rule_number     INTEGER UNIQUE,
      title           TEXT    NOT NULL,
      description     TEXT,
      filter_code     TEXT,
      date_learned    TEXT    DEFAULT (date('now')),
      trades_confirmed INTEGER DEFAULT 0,
      is_active       INTEGER DEFAULT 1,
      last_updated    TEXT    DEFAULT (datetime('now'))
    );

    -- ── Backtests ─────────────────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS backtests (
      id            INTEGER PRIMARY KEY AUTOINCREMENT,
      run_date      TEXT    NOT NULL,
      symbol        TEXT,
      from_date     TEXT,
      to_date       TEXT,
      setup_filter  TEXT,
      total_signals INTEGER DEFAULT 0,
      wins          INTEGER DEFAULT 0,
      losses        INTEGER DEFAULT 0,
      win_rate      REAL,
      avg_pnl       REAL,
      max_drawdown  REAL,
      profit_factor REAL,
      params        TEXT,
      created_at    TEXT DEFAULT (datetime('now'))
    );

    -- ── مصدر الحقيقة النهائي قبل أي إخراج للعميل ─────────────────────────
    CREATE TABLE IF NOT EXISTS final_signals (
      id                  INTEGER PRIMARY KEY AUTOINCREMENT,
      trade_date           TEXT    NOT NULL,
      symbol               TEXT    NOT NULL,
      setup_type           TEXT,
      score                REAL,
      entry_price          REAL,
      entry_high           REAL,
      stop_loss            REAL,
      t1_target            REAL,
      t2_target            REAL,
      r_ratio              REAL,
      source_rules         REAL,
      source_ues           REAL,
      source_pine          REAL,
      source_ml            REAL,
      regime               TEXT,
      confidence           REAL,
      actionable           INTEGER DEFAULT 0,
      veto_reason          TEXT,
      source_breakdown     TEXT,
      created_at           TEXT DEFAULT (datetime('now')),
      updated_at           TEXT DEFAULT (datetime('now')),
      UNIQUE(trade_date, symbol)
    );
    CREATE INDEX IF NOT EXISTS idx_final_date_actionable ON final_signals(trade_date, actionable, score DESC);
    CREATE INDEX IF NOT EXISTS idx_final_symbol_date     ON final_signals(symbol, trade_date DESC);

    -- ── البيانات التاريخية الكاملة (قلب قاعدة البيانات) ─────────────────
    CREATE TABLE IF NOT EXISTS ohlcv_history (
      id          INTEGER PRIMARY KEY AUTOINCREMENT,
      symbol      TEXT    NOT NULL,
      bar_time    INTEGER NOT NULL,   -- Unix timestamp
      open        REAL,
      high        REAL,
      low         REAL,
      close       REAL,
      volume      REAL,
      UNIQUE(symbol, bar_time)
    );

    -- ── سجل الأسهم وحالة الجلب ────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS stock_universe (
      symbol        TEXT PRIMARY KEY,
      name          TEXT,
      sector        TEXT,
      last_fetch    TEXT,            -- آخر مرة جُلبت بياناتها
      total_bars    INTEGER DEFAULT 0,
      earliest_bar  INTEGER,         -- أقدم شمعة (unix)
      latest_bar    INTEGER,         -- أحدث شمعة (unix)
      status        TEXT DEFAULT 'pending'  -- pending | fetched | failed | skipped
    );

    -- ── Indexes ───────────────────────────────────────────────────────────
    CREATE INDEX IF NOT EXISTS idx_scans_date     ON scans(scan_date);
    CREATE INDEX IF NOT EXISTS idx_scans_symbol   ON scans(symbol);
    CREATE INDEX IF NOT EXISTS idx_trades_date    ON trades(scan_date);
    CREATE INDEX IF NOT EXISTS idx_trades_result  ON trades(result);
    CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol   ON ohlcv_history(symbol);
    CREATE INDEX IF NOT EXISTS idx_ohlcv_sym_time ON ohlcv_history(symbol, bar_time DESC);
    CREATE INDEX IF NOT EXISTS idx_universe_status ON stock_universe(status);
  `);

  // ── New tables for Phase 2 ─────────────────────────────────────────
  db.exec(`
    -- أداء القواعد عبر الزمن
    CREATE TABLE IF NOT EXISTS rule_performance (
      id            INTEGER PRIMARY KEY AUTOINCREMENT,
      rule_id       TEXT    NOT NULL,
      week_start    TEXT    NOT NULL,
      signals_count INTEGER DEFAULT 0,
      win_rate      REAL    DEFAULT 0,
      avg_pnl       REAL    DEFAULT 0,
      is_degrading  INTEGER DEFAULT 0,
      last_updated  TEXT    DEFAULT (datetime('now')),
      UNIQUE(rule_id, week_start)
    );

    -- البيانات المالية الأساسية (تُجلب شهرياً)
    CREATE TABLE IF NOT EXISTS financial_data (
      symbol          TEXT PRIMARY KEY,
      pe_ratio        REAL,
      pb_ratio        REAL,
      dividend_yield  REAL,
      earnings_growth REAL,
      market_cap      REAL,
      revenue_growth  REAL,
      roe             REAL,
      debt_to_equity  REAL,
      free_cashflow   REAL,
      sector          TEXT,
      fetch_date      TEXT DEFAULT (date('now')),
      source          TEXT DEFAULT 'manual'
    );

    -- ملاحظات قابلة للبحث (FTS)
    CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
      symbol UNINDEXED,
      note,
      category,
      created_at UNINDEXED
    );

    -- سجل تغير الأداء
    CREATE TABLE IF NOT EXISTS performance_log (
      id          INTEGER PRIMARY KEY AUTOINCREMENT,
      symbol      TEXT,
      metric      TEXT,
      old_value   REAL,
      new_value   REAL,
      changed_at  TEXT DEFAULT (datetime('now'))
    );

    -- تقارير يومية مولّدة
    CREATE TABLE IF NOT EXISTS daily_reports (
      id          INTEGER PRIMARY KEY AUTOINCREMENT,
      report_date TEXT    NOT NULL UNIQUE,
      report_text TEXT,
      scalp_count    INTEGER DEFAULT 0,
      swing_count    INTEGER DEFAULT 0,
      invest_count   INTEGER DEFAULT 0,
      created_at  TEXT DEFAULT (datetime('now'))
    );

    -- View: أفضل إعدادات نشطة (آخر 90 يوم)
    CREATE VIEW IF NOT EXISTS best_setups_live AS
    SELECT symbol, setup_id as setup_type,
           ROUND(AVG(CASE WHEN t.result='win' THEN 1.0 ELSE 0.0 END)*100, 1) as win_rate,
           ROUND(AVG(t.pnl_pct), 2) as avg_pnl,
           COUNT(*) as total_trades,
           MAX(s.scan_date) as last_seen
    FROM scans s
    JOIN trades t ON t.scan_id = s.id
    WHERE s.scan_date >= date('now', '-90 days')
      AND t.result IN ('win','loss','breakeven')
    GROUP BY symbol, setup_id
    HAVING COUNT(*) >= 3
    ORDER BY win_rate DESC;

    -- Index جديد
    CREATE INDEX IF NOT EXISTS idx_rule_perf ON rule_performance(rule_id, week_start);
    CREATE INDEX IF NOT EXISTS idx_perf_log ON performance_log(symbol, changed_at);
    CREATE INDEX IF NOT EXISTS idx_daily_reports ON daily_reports(report_date);
  `);

  // ── Phase 2b: Macro Economics — مؤشرات الاقتصاد الكلي ────────────────
  db.exec(`
    -- جدول Time-Series لكل مؤشر اقتصادي مصري (إدراج فقط — لا حذف)
    CREATE TABLE IF NOT EXISTS macro_economics (
      id           INTEGER PRIMARY KEY AUTOINCREMENT,
      fetched_at   TEXT NOT NULL,
      symbol       TEXT NOT NULL,
      field_name   TEXT NOT NULL,
      value        REAL,
      period_date  TEXT,
      unit         TEXT,
      category     TEXT,
      source       TEXT DEFAULT 'tradingview_live'
    );
    CREATE INDEX IF NOT EXISTS idx_macro_econ_symbol ON macro_economics(symbol, period_date);
    CREATE INDEX IF NOT EXISTS idx_macro_econ_field  ON macro_economics(field_name, period_date);

    -- جدول Snapshot الكامل — يُحفظ snapshot في كل تحديث (أحدث صف = أحدث بيانات)
    CREATE TABLE IF NOT EXISTS macro_snapshot (
      id                   INTEGER PRIMARY KEY AUTOINCREMENT,
      fetched_at           TEXT NOT NULL,
      source               TEXT DEFAULT 'tradingview_live',
      -- أسعار الصرف والسيولة
      usd_egp              REAL,   usd_egp_date      TEXT,
      -- التضخم
      inflation_yoy        REAL,   inflation_date    TEXT,
      core_inflation       REAL,   core_infl_date    TEXT,
      -- الفائدة
      cbe_rate             REAL,   cbe_rate_date     TEXT,
      real_interest_rate   REAL,
      -- النمو
      gdp_yoy              REAL,   gdp_date          TEXT,
      -- سوق العمل
      unemployment         REAL,   unemp_date        TEXT,
      -- الاحتياطيات والنقد
      fx_reserves_b        REAL,   fx_res_date       TEXT,
      m2_egp_t             REAL,   m2_date           TEXT,
      -- التجارة
      trade_balance_m      REAL,   trade_date        TEXT,
      exports_m            REAL,
      imports_m            REAL,
      remittances_q        REAL,   rem_date          TEXT,
      current_account_b    REAL,   ca_date           TEXT,
      -- المالية العامة
      govt_debt_gdp        REAL,   debt_date         TEXT,
      budget_balance_egp_t REAL,   budget_date       TEXT,
      govt_revenue_egp_t   REAL,
      fiscal_exp_egp_t     REAL,
      -- الاستثمار
      fdi_q_b              REAL,   fdi_date          TEXT,
      external_debt_b      REAL,   ext_debt_date     TEXT,
      -- الطاقة والسياحة
      oil_production_kbd   REAL,   oil_date          TEXT,
      tourist_arrivals_k   REAL,   tour_date         TEXT,
      -- مؤشرات مشتقة
      macro_regime         TEXT,
      regime_score         REAL,
      strategic_bias       TEXT,
      equity_multiplier    REAL,
      inflation_momentum   TEXT,
      rate_cycle           TEXT,
      fx_trend             TEXT,
      growth_trend         TEXT,
      -- JSON خام
      raw_json             TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_macro_snap ON macro_snapshot(fetched_at);
  `);

  // ── Phase 3: indicators_cache — مؤشرات محسوبة مسبقاً ─────────────────
  // الهدف: scan_today يقرأ من هنا بدل إعادة الحساب — سرعة 10x
  // يُحدَّث يومياً بعد daily_update عبر: node scripts/rebuild_indicators.mjs
  db.exec(`
    CREATE TABLE IF NOT EXISTS indicators_cache (
      symbol        TEXT NOT NULL,
      bar_date      TEXT NOT NULL,   -- YYYY-MM-DD
      -- Trend
      ema10         REAL,
      ema20         REAL,
      ema50         REAL,
      ema200        REAL,
      above_ema20   INTEGER,  -- 0/1
      above_ema50   INTEGER,
      above_ema200  INTEGER,
      -- Momentum
      rsi14         REAL,
      macd_line     REAL,
      macd_signal   REAL,
      macd_hist     REAL,
      stoch_k       REAL,
      stoch_d       REAL,
      cci20         REAL,
      williams_r    REAL,
      -- Volatility
      atr14         REAL,
      bb_upper      REAL,
      bb_middle     REAL,
      bb_lower      REAL,
      bb_width      REAL,
      bb_position   REAL,   -- % position within band: 0=at lower, 1=at upper
      -- Volume
      obv           REAL,
      obv_divergence TEXT,  -- 'bullish' | 'bearish' | null
      vol_ratio_20  REAL,   -- volume / 20-day avg
      -- Patterns
      is_hammer     INTEGER DEFAULT 0,
      is_engulfing  INTEGER DEFAULT 0,
      is_doji       INTEGER DEFAULT 0,
      -- ADX
      adx14         REAL,
      adx_plus_di   REAL,
      adx_minus_di  REAL,
      -- Context
      close_position REAL,  -- position within candle range [0,1]
      price_vs_ath   REAL,  -- % below ATH
      momentum_5d    REAL,  -- 5-day price change %
      momentum_10d   REAL,
      momentum_20d   REAL,
      -- Metadata
      source        TEXT DEFAULT 'local',  -- local | tv
      updated_at    TEXT DEFAULT (datetime('now')),
      PRIMARY KEY (symbol, bar_date)
    );

    CREATE INDEX IF NOT EXISTS idx_ic_symbol  ON indicators_cache(symbol);
    CREATE INDEX IF NOT EXISTS idx_ic_date    ON indicators_cache(bar_date);
    CREATE INDEX IF NOT EXISTS idx_ic_rsi     ON indicators_cache(rsi14);
    CREATE INDEX IF NOT EXISTS idx_ic_obv     ON indicators_cache(obv_divergence);
    -- مؤشرات مركّبة للاستعلامات الشائعة
    CREATE INDEX IF NOT EXISTS idx_ic_rsi_adx ON indicators_cache(rsi14, adx14);
    CREATE INDEX IF NOT EXISTS idx_ic_sym_date ON indicators_cache(symbol, bar_date DESC);
    -- مؤشر OHLCV بالتاريخ (للـ param_sweep و walk_forward)
    CREATE INDEX IF NOT EXISTS idx_ohlcv_bar_time ON ohlcv_history(bar_time);
    CREATE INDEX IF NOT EXISTS idx_ohlcv_sym_close ON ohlcv_history(symbol, bar_time, close);

    -- View: الإشارات النشطة اليوم (RSI+OBV combo و Mean Reversion)
    CREATE VIEW IF NOT EXISTS active_signals_today AS
    SELECT
      ic.symbol,
      ic.bar_date,
      ic.rsi14,
      ic.obv_divergence,
      ic.adx14,
      ic.above_ema200,
      ic.bb_position,
      ic.vol_ratio_20,
      ic.close_position,
      ic.is_hammer,
      ic.is_engulfing,
      CASE
        WHEN ic.rsi14 <= 35 AND ic.obv_divergence = 'bullish' THEN 'RSI_OBV_COMBO'
        WHEN ic.rsi14 <= 30                                    THEN 'OVERSOLD_RSI30'
        WHEN ic.bb_position <= 0.05                            THEN 'BB_OVERSOLD'
        WHEN ic.adx14 BETWEEN 20 AND 30 AND ic.rsi14 <= 45    THEN 'ADX_SWEET_SPOT'
        ELSE NULL
      END AS signal_type
    FROM indicators_cache ic
    WHERE ic.bar_date = (SELECT MAX(bar_date) FROM indicators_cache WHERE symbol = ic.symbol)
      AND (ic.rsi14 <= 35 OR ic.bb_position <= 0.10 OR ic.obv_divergence = 'bullish');
  `);

  // ── إدراج الدروس المبدئية من TRADING_LESSONS.md ──────────────────────
  seedInitialLessons(db);

  // ── Phase 49-55: Deep History, Intraday, Cross-Market, Liquidity, etc. ──
  _initPhase49to55Tables(db);
}

function _initPhase49to55Tables(db) {
  db.exec(`
    CREATE TABLE IF NOT EXISTS ohlcv_weekly  (symbol TEXT NOT NULL, bar_time INTEGER NOT NULL, open REAL, high REAL, low REAL, close REAL, volume REAL, PRIMARY KEY (symbol, bar_time));
    CREATE INDEX IF NOT EXISTS idx_ohlcv_w_sym_time ON ohlcv_weekly(symbol, bar_time DESC);
    CREATE TABLE IF NOT EXISTS ohlcv_monthly (symbol TEXT NOT NULL, bar_time INTEGER NOT NULL, open REAL, high REAL, low REAL, close REAL, volume REAL, PRIMARY KEY (symbol, bar_time));
    CREATE INDEX IF NOT EXISTS idx_ohlcv_m_sym_time ON ohlcv_monthly(symbol, bar_time DESC);
    CREATE TABLE IF NOT EXISTS deep_history_snapshot (id INTEGER PRIMARY KEY AUTOINCREMENT, generated_at TEXT NOT NULL, regime TEXT, cycle_phase TEXT, cycle_age_weeks INTEGER, avg_volatility REAL, n_symbols_weekly INTEGER, n_symbols_monthly INTEGER, regime_strength REAL, summary TEXT);
    CREATE TABLE IF NOT EXISTS ohlcv_60min  (symbol TEXT NOT NULL, bar_time INTEGER NOT NULL, open REAL, high REAL, low REAL, close REAL, volume REAL, PRIMARY KEY (symbol, bar_time));
    CREATE INDEX IF NOT EXISTS idx_ohlcv_60_sym_time ON ohlcv_60min(symbol, bar_time DESC);
    CREATE TABLE IF NOT EXISTS ohlcv_15min  (symbol TEXT NOT NULL, bar_time INTEGER NOT NULL, open REAL, high REAL, low REAL, close REAL, volume REAL, PRIMARY KEY (symbol, bar_time));
    CREATE INDEX IF NOT EXISTS idx_ohlcv_15_sym_time ON ohlcv_15min(symbol, bar_time DESC);
    CREATE TABLE IF NOT EXISTS intraday_analytics (symbol TEXT NOT NULL, trade_date TEXT NOT NULL, vwap REAL, opening_range_high REAL, opening_range_low REAL, opening_gap_pct REAL, first_hour_direction TEXT, volume_profile_bins TEXT, session_bias TEXT, best_entry_window TEXT, volatility_percentile REAL, computed_at TEXT, PRIMARY KEY (symbol, trade_date));
    CREATE INDEX IF NOT EXISTS idx_intraday_sym_date ON intraday_analytics(symbol, trade_date DESC);
    CREATE TABLE IF NOT EXISTS cross_market_daily  (asset TEXT NOT NULL, bar_time INTEGER NOT NULL, open REAL, high REAL, low REAL, close REAL, volume REAL, PRIMARY KEY (asset, bar_time));
    CREATE INDEX IF NOT EXISTS idx_cross_asset_time ON cross_market_daily(asset, bar_time DESC);
    CREATE TABLE IF NOT EXISTS cross_market_regime  (date TEXT PRIMARY KEY, usdegp_regime TEXT, gold_regime TEXT, em_regime TEXT, oil_regime TEXT, vix_regime TEXT, risk_on_score REAL, macro_headwind TEXT, generated_at TEXT);
    CREATE TABLE IF NOT EXISTS dom_snapshots       (symbol TEXT NOT NULL, snapshot_time INTEGER NOT NULL, bids TEXT, asks TEXT, spread_pct REAL, PRIMARY KEY (symbol, snapshot_time));
    CREATE TABLE IF NOT EXISTS liquidity_profile   (symbol TEXT NOT NULL, computed_date TEXT NOT NULL, advt_30d REAL, advt_10d REAL, amihud_ratio REAL, turnover_velocity REAL, bid_ask_spread_est REAL, dom_spread_pct REAL, max_safe_order_egp REAL, liquidity_tier TEXT, liquidity_score REAL, PRIMARY KEY (symbol, computed_date));
    CREATE INDEX IF NOT EXISTS idx_liq_sym_date ON liquidity_profile(symbol, computed_date DESC);
    CREATE INDEX IF NOT EXISTS idx_liq_tier     ON liquidity_profile(liquidity_tier);
    CREATE TABLE IF NOT EXISTS pine_analytics      (symbol TEXT NOT NULL, trade_date TEXT NOT NULL, volume_poc REAL, volume_vah REAL, volume_val REAL, vwap REAL, opening_range_high REAL, opening_range_low REAL, session_bias TEXT, rs_score REAL, rs_percentile REAL, corporate_event_flag INTEGER DEFAULT 0, corporate_event_type TEXT, raw_pine_data TEXT, source_script TEXT, PRIMARY KEY (symbol, trade_date));
    CREATE INDEX IF NOT EXISTS idx_pine_sym_date ON pine_analytics(symbol, trade_date DESC);
    CREATE TABLE IF NOT EXISTS corporate_actions   (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, event_date TEXT NOT NULL, event_type TEXT NOT NULL, price_before REAL, price_after REAL, gap_pct REAL, volume_on_day REAL, avg_volume_20d REAL, volume_multiple REAL, adjustment_factor REAL, confidence REAL, is_confirmed INTEGER DEFAULT 0, is_adjusted INTEGER DEFAULT 0, notes TEXT, detected_at TEXT, UNIQUE(symbol, event_date));
    CREATE INDEX IF NOT EXISTS idx_ca_sym_date ON corporate_actions(symbol, event_date DESC);
    CREATE TABLE IF NOT EXISTS data_quality_log    (id INTEGER PRIMARY KEY AUTOINCREMENT, check_type TEXT NOT NULL, table_name TEXT NOT NULL, symbol TEXT, bar_date TEXT, issue_description TEXT, severity TEXT, status TEXT DEFAULT 'OPEN', auto_fixed INTEGER DEFAULT 0, checked_at TEXT, resolved_at TEXT);
    CREATE INDEX IF NOT EXISTS idx_dql_status   ON data_quality_log(status);
    CREATE TABLE IF NOT EXISTS data_trust_scores   (source TEXT PRIMARY KEY, trust_score REAL, last_checked TEXT, n_issues_open INTEGER, n_issues_critical INTEGER, status TEXT);
  `);
}

function seedInitialLessons(db) {
  const existing = db.prepare('SELECT COUNT(*) as c FROM lessons').get();
  if (existing.c > 0) return;

  const lessons = [
    {
      rule_number: 1,
      title: 'ATH Breakout يحتاج حجم ≥ 2.5x',
      description: 'لا توصية Near ATH Continuation بدون حجم breakout يوم الكسر ≥ 2.5x المتوسط. ATH = بائعون محاصرون.',
      filter_code: 'if (isNearATH && volumeRatio < 2.5) → REJECT',
      date_learned: '2026-05-03',
      trades_confirmed: 1,
    },
    {
      rule_number: 2,
      title: 'انهيار الحجم بعد Breakout = توزيع',
      description: 'إذا انخفض الحجم أكثر من 60% عن يوم الـ breakout → لا تدخل. POUL خسرت بسبب هذا.',
      filter_code: 'if (todayVolume < 0.4 * breakoutDayVolume) → DO NOT ENTER',
      date_learned: '2026-05-03',
      trades_confirmed: 1,
    },
    {
      rule_number: 3,
      title: 'لا دخول فوق منطقة الدخول',
      description: 'إذا فتح السهم فوق entry zone بأكثر من 0.5% → أعد حساب R:R. إذا R:R < 1.2 → تخطّ.',
      filter_code: 'if (openPrice > entryZoneTop * 1.005 && newRR < 1.2) → SKIP',
      date_learned: '2026-05-03',
      trades_confirmed: 1,
    },
    {
      rule_number: 4,
      title: 'Institutional Retest = أفضل نمط EGX',
      description: 'ضخامة حجم breakout + retest للمستوى + إغلاق فوقه = أعلى احتمالية. PHDC +9.2% مثال.',
      filter_code: 'if (hadBreakout && retestLevel && closeAbove) → PRIORITY_1',
      date_learned: '2026-05-03',
      trades_confirmed: 1,
    },
    {
      rule_number: 5,
      title: 'Defensive Sector في EGX تعريف مختلف',
      description: 'الغذاء والدواجن ليست defensive. Defensive الحقيقي = مصرفي، اتصالات، خدمات.',
      filter_code: 'DEFENSIVE_EGX = [COMI, HDBK, ETEL, CIEB, EGTS]',
      date_learned: '2026-05-03',
      trades_confirmed: 1,
    },
  ];

  const insert = db.prepare(`
    INSERT OR IGNORE INTO lessons
      (rule_number, title, description, filter_code, date_learned, trades_confirmed)
    VALUES
      (@rule_number, @title, @description, @filter_code, @date_learned, @trades_confirmed)
  `);
  const insertMany = db.transaction(ls => ls.forEach(l => insert.run(l)));
  insertMany(lessons);
}

// ─── API الرئيسي ─────────────────────────────────────────────────────────

/** حفظ نتائج scan جديد */
export function saveScan(scanDate, stockResults) {
  const db   = getDB();
  const stmt = db.prepare(`
    INSERT INTO scans
      (scan_date, symbol, setup_type, setup_id, score, grade, priority,
       entry_low, entry_high, stop_loss, t1, t2, rr1, rr2,
       volume_ratio, avg_volume, close_price, confidence,
       rejected, rejection_reasons, is_best_safe, is_best_aggressive)
    VALUES
      (@scan_date, @symbol, @setup_type, @setup_id, @score, @grade, @priority,
       @entry_low, @entry_high, @stop_loss, @t1, @t2, @rr1, @rr2,
       @volume_ratio, @avg_volume, @close_price, @confidence,
       @rejected, @rejection_reasons, @is_best_safe, @is_best_aggressive)
  `);

  const insertAll = db.transaction(results => {
    db.prepare('DELETE FROM scans WHERE scan_date = ?').run(scanDate);
    const ids = [];
    for (const r of results) {
      const info = stmt.run({
        scan_date: scanDate,
        symbol:    r.symbol,
        setup_type: r.setupType,
        setup_id:   r.setupId,
        score:      r.score,
        grade:      r.grade,
        priority:   r.priority,
        entry_low:  r.levels?.entryLow   ?? null,
        entry_high: r.levels?.entryHigh  ?? null,
        stop_loss:  r.levels?.sl         ?? null,
        t1:         r.levels?.t1         ?? null,
        t2:         r.levels?.t2         ?? null,
        rr1:        r.levels?.rr1        ?? null,
        rr2:        r.levels?.rr2        ?? null,
        volume_ratio:  r.volumeRatio     ?? null,
        avg_volume:    r.avgVolume       ?? null,
        close_price:   r.closePrice ?? null,  // Bug fix: السعر الحقيقي من scorer
        confidence:    r.confidence      ?? null,
        rejected:      r.rejected ? 1 : 0,
        rejection_reasons: JSON.stringify(r.rejections ?? []),
        is_best_safe:       r.isBestSafe       ? 1 : 0,
        is_best_aggressive: r.isBestAggressive ? 1 : 0,
      });
      ids.push(info.lastInsertRowid);
    }
    return ids;
  });

  return insertAll(stockResults);
}

/** تسجيل صفقة منفّذة */
export function saveTrade(trade) {
  const db = getDB();
  // تعبئة الحقول الاختيارية بقيم افتراضية
  const t = {
    pnl_egp:       null,
    position_size: null,
    hit_t2:        0,
    exit_reason:   null,
    notes:         null,
    ...trade,
  };
  return db.prepare(`
    INSERT INTO trades
      (scan_id, scan_date, symbol, setup_type, entry_price, entry_date,
       exit_price, exit_date, pnl_pct, pnl_egp, position_size, result,
       hit_t1, hit_t2, hit_sl, hold_days, exit_reason, notes)
    VALUES
      (@scan_id, @scan_date, @symbol, @setup_type, @entry_price, @entry_date,
       @exit_price, @exit_date, @pnl_pct, @pnl_egp, @position_size, @result,
       @hit_t1, @hit_t2, @hit_sl, @hold_days, @exit_reason, @notes)
  `).run(t);
}

/** حفظ نتيجة post-mortem */
export function savePostMortem(pm) {
  const db = getDB();
  db.prepare(`
    INSERT INTO postmortems
      (session_date, total_trades, wins, losses, breakevens, win_rate,
       avg_win_pct, avg_loss_pct, best_trade, worst_trade,
       best_pnl_pct, worst_pnl_pct, key_lessons)
    VALUES
      (@session_date, @total_trades, @wins, @losses, @breakevens, @win_rate,
       @avg_win_pct, @avg_loss_pct, @best_trade, @worst_trade,
       @best_pnl_pct, @worst_pnl_pct, @key_lessons)
  `).run(pm);
}

/** تحديث أداء نوع الإعداد بعد كل صفقة */
export function updateSetupPerformance(setupType, dayOfWeek, dayName, won, pnlPct) {
  const db  = getDB();
  const row = db.prepare(
    'SELECT * FROM setup_performance WHERE setup_type = ? AND day_of_week = ?'
  ).get(setupType, dayOfWeek);

  if (!row) {
    db.prepare(`
      INSERT INTO setup_performance
        (setup_type, day_of_week, day_name, total_trades, wins, losses,
         win_rate, avg_pnl, best_pnl, worst_pnl)
      VALUES (?,?,?,1,?,?,?,?,?,?)
    `).run(setupType, dayOfWeek, dayName,
           won ? 1 : 0, won ? 0 : 1,
           pnlPct, pnlPct,
           Math.max(pnlPct, 0), Math.min(pnlPct, 0));
  } else {
    const total = row.total_trades + 1;
    const wins  = row.wins + (won ? 1 : 0);
    const avgPnl= ((row.avg_pnl * row.total_trades) + pnlPct) / total;
    db.prepare(`
      UPDATE setup_performance SET
        total_trades = ?, wins = ?, losses = ?, win_rate = ?,
        avg_pnl = ?, best_pnl = MAX(best_pnl, ?), worst_pnl = MIN(worst_pnl, ?),
        last_updated = datetime('now')
      WHERE setup_type = ? AND day_of_week = ?
    `).run(total, wins, total - wins, wins/total, avgPnl, pnlPct, pnlPct, setupType, dayOfWeek);
  }
}

/** جلب إحصائيات كاملة */
export function getStats() {
  const db = getDB();
  return {
    totalScans:   db.prepare('SELECT COUNT(*) as c FROM scans').get().c,
    totalTrades:  db.prepare('SELECT COUNT(*) as c FROM trades').get().c,
    overallWinRate: (() => {
      const r = db.prepare(
        "SELECT AVG(CASE WHEN result='win' THEN 1.0 ELSE 0.0 END) as wr FROM trades WHERE result != 'open'"
      ).get();
      return r?.wr ? +(r.wr * 100).toFixed(1) : 0;
    })(),
    setupPerformance: db.prepare(
      'SELECT * FROM setup_performance ORDER BY win_rate DESC'
    ).all(),
    recentTrades: db.prepare(
      'SELECT * FROM trades ORDER BY created_at DESC LIMIT 10'
    ).all(),
    activeRules: db.prepare(
      'SELECT rule_number, title FROM lessons WHERE is_active = 1 ORDER BY rule_number'
    ).all(),
  };
}

/** جلب أفضل وأسوأ إعدادات */
export function getBestSetups() {
  const db = getDB();
  return db.prepare(`
    SELECT setup_type, day_name,
           total_trades, win_rate,
           avg_pnl, best_pnl
    FROM setup_performance
    WHERE total_trades >= 3
    ORDER BY win_rate DESC, avg_pnl DESC
    LIMIT 10
  `).all();
}

// ═══════════════════════════════════════════════════════════════════════════
// ██  OHLCV HISTORY — حفظ واسترجاع البيانات التاريخية الكاملة
// ═══════════════════════════════════════════════════════════════════════════

/**
 * حفظ شمعات تاريخية لسهم معين (UPSERT — يُحدّث الشمعة إذا تغيّرت القيم)
 * @param {string} symbol
 * @param {Array<{time,open,high,low,close,volume}>} bars
 * @returns {number} عدد الصفوف المُضافة أو المُحدَّثة
 */
export function saveOHLCV(symbol, bars) {
  const db = getDB();
  const insert = db.prepare(`
    INSERT INTO ohlcv_history (symbol, bar_time, open, high, low, close, volume)
    VALUES (@symbol, @bar_time, @open, @high, @low, @close, @volume)
    ON CONFLICT(symbol, bar_time) DO UPDATE SET
      open   = excluded.open,
      high   = excluded.high,
      low    = excluded.low,
      close  = excluded.close,
      volume = excluded.volume
    WHERE excluded.close IS NOT NULL AND excluded.close > 0
  `);

  const insertMany = db.transaction((rows) => {
    let count = 0;
    for (const b of rows) {
      const result = insert.run({
        symbol,
        bar_time: b.time,
        open:   b.open  ?? null,
        high:   b.high  ?? null,
        low:    b.low   ?? null,
        close:  b.close ?? null,
        volume: b.volume ?? null,
      });
      count += result.changes;
    }
    return count;
  });

  return insertMany(bars);
}

function ohlcvReadTable(db) {
  const view = db.prepare(
    "SELECT 1 AS ok FROM sqlite_master WHERE type='view' AND name='ohlcv_history_execution'",
  ).get();
  return view ? 'ohlcv_history_execution' : 'ohlcv_history';
}

/**
 * استرجاع البيانات التاريخية لسهم (يفضّل ohlcv_history_execution عند توفرها)
 * @param {string} symbol
 * @param {number} [limit=500]  - عدد الشمعات (من الأحدث للأقدم)
 * @param {{ sinceDate?: string, execution?: boolean }} [opts]
 * @returns {Array}
 */
export function getOHLCV(symbol, limit = 500, opts = {}) {
  const db = getDB();
  const useExecution = opts.execution !== false;
  const table = useExecution ? ohlcvReadTable(db) : 'ohlcv_history';
  const sinceDate = opts.sinceDate ?? null;
  let sql = `
    SELECT bar_time as time, open, high, low, close, volume
    FROM ${table}
    WHERE symbol = ?
  `;
  const params = [symbol];
  if (sinceDate) {
    sql += ` AND date(bar_time, 'unixepoch') >= ?`;
    params.push(sinceDate);
  }
  sql += ` ORDER BY bar_time DESC LIMIT ?`;
  params.push(limit);
  const rows = db.prepare(sql).all(...params);
  return rows.reverse();
}

/** Same as getOHLCV but always reads production execution view when present. */
export function getOHLCVExecution(symbol, limit = 500, sinceDate = null) {
  return getOHLCV(symbol, limit, { execution: true, sinceDate });
}

/**
 * تحديث أو إدراج حالة سهم في stock_universe
 */
export function upsertStockUniverse(symbol, data) {
  const db = getDB();
  db.prepare(`
    INSERT INTO stock_universe (symbol, name, sector, last_fetch, total_bars, earliest_bar, latest_bar, status)
    VALUES (@symbol, @name, @sector, @last_fetch, @total_bars, @earliest_bar, @latest_bar, @status)
    ON CONFLICT(symbol) DO UPDATE SET
      last_fetch   = excluded.last_fetch,
      total_bars   = excluded.total_bars,
      earliest_bar = excluded.earliest_bar,
      latest_bar   = excluded.latest_bar,
      status       = excluded.status
  `).run({
    symbol,
    name:         data.name         ?? null,
    sector:       data.sector        ?? null,
    last_fetch:   data.last_fetch    ?? new Date().toISOString().split('T')[0],
    total_bars:   data.total_bars    ?? 0,
    earliest_bar: data.earliest_bar  ?? null,
    latest_bar:   data.latest_bar    ?? null,
    status:       data.status        ?? 'fetched',
  });
}

/**
 * إحصائيات البيانات التاريخية
 */
export function getHistoryStats() {
  const db = getDB();
  const summary = db.prepare(`
    SELECT
      COUNT(DISTINCT symbol)                   as total_symbols,
      COUNT(*)                                 as total_bars,
      MIN(bar_time)                            as earliest_bar,
      MAX(bar_time)                            as latest_bar,
      ROUND(COUNT(*) * 1.0 / COUNT(DISTINCT symbol), 0) as avg_bars_per_symbol
    FROM ohlcv_history
  `).get();

  const bySymbol = db.prepare(`
    SELECT symbol, COUNT(*) as bars,
           MIN(bar_time) as earliest, MAX(bar_time) as latest
    FROM ohlcv_history
    GROUP BY symbol
    ORDER BY bars DESC
  `).all();

  const statusCount = db.prepare(`
    SELECT status, COUNT(*) as cnt
    FROM stock_universe
    GROUP BY status
  `).all();

  return { summary, bySymbol, statusCount };
}

// ═══════════════════════════════════════════════════════════════════════════
// ██  RULE PERFORMANCE — تتبع أداء القواعد عبر الزمن
// ═══════════════════════════════════════════════════════════════════════════

export function saveRulePerformance(ruleId, weekStart, stats) {
  const db = getDB();
  db.prepare(`
    INSERT INTO rule_performance (rule_id, week_start, signals_count, win_rate, avg_pnl, is_degrading)
    VALUES (@rule_id, @week_start, @signals_count, @win_rate, @avg_pnl, @is_degrading)
    ON CONFLICT(rule_id, week_start) DO UPDATE SET
      signals_count = excluded.signals_count,
      win_rate      = excluded.win_rate,
      avg_pnl       = excluded.avg_pnl,
      is_degrading  = excluded.is_degrading,
      last_updated  = datetime('now')
  `).run({ rule_id: ruleId, week_start: weekStart, ...stats });
}

export function getRulePerformance(ruleId, weeks = 12) {
  const db = getDB();
  return db.prepare(`
    SELECT * FROM rule_performance
    WHERE rule_id = ?
    ORDER BY week_start DESC
    LIMIT ?
  `).all(ruleId, weeks);
}

export function getAllRulesPerformance() {
  const db = getDB();
  return db.prepare(`
    SELECT rule_id,
           AVG(win_rate) as avg_wr,
           AVG(avg_pnl) as avg_pnl,
           SUM(signals_count) as total_signals,
           MAX(week_start) as latest_week,
           SUM(is_degrading) as degrading_weeks
    FROM rule_performance
    GROUP BY rule_id
    ORDER BY avg_wr DESC
  `).all();
}

// ═══════════════════════════════════════════════════════════════════════════
// ██  FINANCIAL DATA — بيانات مالية أساسية
// ═══════════════════════════════════════════════════════════════════════════

export function saveFinancialData(symbol, data) {
  const db = getDB();
  db.prepare(`
    INSERT INTO financial_data
      (symbol, pe_ratio, pb_ratio, dividend_yield, earnings_growth,
       market_cap, revenue_growth, roe, debt_to_equity, free_cashflow, sector, source)
    VALUES
      (@symbol, @pe_ratio, @pb_ratio, @dividend_yield, @earnings_growth,
       @market_cap, @revenue_growth, @roe, @debt_to_equity, @free_cashflow, @sector, @source)
    ON CONFLICT(symbol) DO UPDATE SET
      pe_ratio       = excluded.pe_ratio,
      pb_ratio       = excluded.pb_ratio,
      dividend_yield = excluded.dividend_yield,
      earnings_growth= excluded.earnings_growth,
      market_cap     = excluded.market_cap,
      revenue_growth = excluded.revenue_growth,
      roe            = excluded.roe,
      debt_to_equity = excluded.debt_to_equity,
      free_cashflow  = excluded.free_cashflow,
      sector         = excluded.sector,
      fetch_date     = date('now'),
      source         = excluded.source
  `).run({ symbol, ...data, sector: data.sector ?? null, source: data.source ?? 'manual' });
}

export function getFinancialData(symbol) {
  return getDB().prepare('SELECT * FROM financial_data WHERE symbol = ?').get(symbol);
}

export function getUndervaluedStocks({ maxPE = 8, maxPB = 1.5, minGrowth = 5 } = {}) {
  const db = getDB();
  return db.prepare(`
    SELECT f.*, u.total_bars, u.latest_bar
    FROM financial_data f
    LEFT JOIN stock_universe u ON u.symbol = f.symbol
    WHERE (f.pe_ratio IS NULL OR f.pe_ratio < @maxPE)
      AND (f.pb_ratio IS NULL OR f.pb_ratio < @maxPB)
      AND (f.earnings_growth IS NULL OR f.earnings_growth > @minGrowth)
    ORDER BY f.pb_ratio ASC NULLS LAST
    LIMIT 20
  `).all({ maxPE, maxPB, minGrowth });
}

// ═══════════════════════════════════════════════════════════════════════════
// ██  NOTES — ملاحظات قابلة للبحث
// ═══════════════════════════════════════════════════════════════════════════

export function addNote(symbol, note, category = 'general') {
  const db = getDB();
  db.prepare(`
    INSERT INTO notes_fts (symbol, note, category, created_at)
    VALUES (?, ?, ?, datetime('now'))
  `).run(symbol, note, category);
}

export function searchNotes(query) {
  const db = getDB();
  return db.prepare(`
    SELECT * FROM notes_fts WHERE notes_fts MATCH ?
    ORDER BY created_at DESC LIMIT 20
  `).all(query);
}

// ═══════════════════════════════════════════════════════════════════════════
// ██  DAILY REPORTS ─────────────────────────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════

export function saveDailyReport(reportDate, reportText, counts = {}) {
  const db = getDB();
  db.prepare(`
    INSERT INTO daily_reports (report_date, report_text, scalp_count, swing_count, invest_count)
    VALUES (@report_date, @report_text, @scalp_count, @swing_count, @invest_count)
    ON CONFLICT(report_date) DO UPDATE SET
      report_text  = excluded.report_text,
      scalp_count  = excluded.scalp_count,
      swing_count  = excluded.swing_count,
      invest_count = excluded.invest_count,
      created_at   = datetime('now')
  `).run({
    report_date:  reportDate,
    report_text:  reportText,
    scalp_count:  counts.scalp  ?? 0,
    swing_count:  counts.swing  ?? 0,
    invest_count: counts.invest ?? 0,
  });
}

export function getLastReport() {
  return getDB().prepare('SELECT * FROM daily_reports ORDER BY report_date DESC LIMIT 1').get();
}

export function getReportByDate(date) {
  return getDB().prepare('SELECT * FROM daily_reports WHERE report_date = ?').get(date);
}

// ═══════════════════════════════════════════════════════════════════════════
// ██  WALK-FORWARD HELPERS ──────────────────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════

/**
 * جلب شمعات بنطاق زمني محدد
 */
export function getOHLCVRange(symbol, fromDate, toDate) {
  const db = getDB();
  // تحويل YYYY-MM-DD إلى unix timestamp
  const fromTs = Math.floor(new Date(fromDate).getTime() / 1000);
  const toTs   = Math.floor(new Date(toDate).getTime()   / 1000);
  return db.prepare(`
    SELECT bar_time as time, open, high, low, close, volume
    FROM ohlcv_history
    WHERE symbol = ? AND bar_time >= ? AND bar_time <= ?
    ORDER BY bar_time ASC
  `).all(symbol, fromTs, toTs);
}

/**
 * أسهم التي تم تحديثها اليوم
 */
export function getStaleSymbols(daysOld = 1) {
  const db = getDB();
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - daysOld);
  const cutoffStr = cutoff.toISOString().split('T')[0];
  return db.prepare(`
    SELECT symbol FROM stock_universe
    WHERE status = 'fetched'
      AND (last_fetch IS NULL OR last_fetch < ?)
    ORDER BY last_fetch ASC
  `).all(cutoffStr).map(r => r.symbol);
}

// ═══════════════════════════════════════════════════════════════════════════
// ██  INDICATORS CACHE API ──────────────────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════

/**
 * حفظ/تحديث مؤشرات سهم ليوم معين
 * @param {string} symbol - رمز السهم
 * @param {string} barDate - YYYY-MM-DD
 * @param {Object} ind - نتيجة calculateIndicators()
 */
export function saveIndicatorsCache(symbol, barDate, ind, { source = 'local' } = {}) {
  const db = getDB();
  const cols = db.prepare('PRAGMA table_info(indicators_cache)').all().map(r => r.name);
  const hasSource = cols.includes('source');
  db.prepare(`
    INSERT OR REPLACE INTO indicators_cache
      (symbol, bar_date, ema10, ema20, ema50, ema200,
       above_ema20, above_ema50, above_ema200,
       rsi14, macd_line, macd_signal, macd_hist,
       stoch_k, stoch_d, cci20, williams_r,
       atr14, bb_upper, bb_middle, bb_lower, bb_width, bb_position,
       obv, obv_divergence, vol_ratio_20,
       is_hammer, is_engulfing, is_doji,
       adx14, adx_plus_di, adx_minus_di,
       close_position, price_vs_ath, momentum_5d, momentum_10d, momentum_20d,
       rsi_slope_3d,
       ${hasSource ? 'source,' : ''}
       updated_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,${hasSource ? '?,' : ''}datetime('now'))
  `).run(
    symbol, barDate,
    ind.ema10    ?? null, ind.ema20  ?? null, ind.ema50   ?? null, ind.ema200 ?? null,
    ind.ema20    ? (ind.lastClose > ind.ema20  ? 1 : 0) : null,
    ind.ema50    ? (ind.lastClose > ind.ema50  ? 1 : 0) : null,
    ind.ema200   ? (ind.lastClose > ind.ema200 ? 1 : 0) : null,
    ind.rsi      ?? null,
    ind.macd?.macd        ?? null, ind.macd?.signal ?? null, ind.macd?.histogram ?? null,
    ind.stochastic?.k     ?? null, ind.stochastic?.d ?? null,
    ind.cci      ?? null, ind.williamsR ?? null,
    ind.atr      ?? null,
    ind.bollingerBands?.upper  ?? null, ind.bollingerBands?.middle ?? null,
    ind.bollingerBands?.lower  ?? null, ind.bollingerBands?.width  ?? null,
    ind.bollingerBands?.position ?? null,
    ind.obv      ?? null, ind.obvDivergence ?? null,
    ind.volumeRatio20 ?? null,
    ind.isHammer       ? 1 : 0,
    ind.isBullishEngulfing ? 1 : 0,
    ind.isDoji         ? 1 : 0,
    ind.adx?.adx     ?? null, ind.adx?.pdi ?? null, ind.adx?.mdi ?? null,
    ind.closePosition ?? null, ind.athProximity ?? null,
    ind.momentum5d  ?? null, ind.momentum10d ?? null, ind.momentum20d ?? null,
    ind.rsiSlope3d ?? null,
    ...(hasSource ? [source] : []),
  );
}

/**
 * جلب آخر مؤشرات لسهم من الكاش
 * @param {string} symbol
 * @returns {Object|null}
 */
export function getLatestIndicators(symbol) {
  return getDB().prepare(`
    SELECT * FROM indicators_cache
    WHERE symbol = ?
    ORDER BY bar_date DESC LIMIT 1
  `).get(symbol) ?? null;
}

/**
 * جلب الإشارات النشطة اليوم من كاش المؤشرات
 * أسرع بكثير من إعادة الحساب على 268 سهم
 * @param {Object} opts - { minRsi, maxRsi, signalType, limit }
 */
export function getSignalsFromCache(opts = {}) {
  const { minRsi = 0, maxRsi = 45, signalType = null, limit = 50 } = opts;
  const db = getDB();

  let sql = `
    SELECT ic.symbol, ic.bar_date, ic.rsi14, ic.obv_divergence, ic.adx14,
           ic.above_ema200, ic.bb_position, ic.vol_ratio_20,
           ic.is_hammer, ic.is_engulfing,
           CASE
             WHEN ic.rsi14 <= 35 AND ic.obv_divergence = 'bullish' THEN 'RSI_OBV_COMBO'
             WHEN ic.rsi14 <= 30                                    THEN 'OVERSOLD_RSI30'
             WHEN ic.bb_position <= 0.05                            THEN 'BB_OVERSOLD'
             WHEN ic.adx14 BETWEEN 20 AND 30 AND ic.rsi14 <= 45    THEN 'ADX_SWEET_SPOT'
             ELSE 'GENERAL'
           END AS signal_type
    FROM indicators_cache ic
    WHERE ic.bar_date = (SELECT MAX(bar_date) FROM indicators_cache WHERE symbol = ic.symbol)
      AND ic.rsi14 BETWEEN ? AND ?
  `;
  const params = [minRsi, maxRsi];

  if (signalType === 'RSI_OBV_COMBO') {
    sql += ` AND ic.rsi14 <= 35 AND ic.obv_divergence = 'bullish'`;
  } else if (signalType === 'OVERSOLD') {
    sql += ' AND ic.rsi14 <= 35';
  } else if (signalType === 'BB_OVERSOLD') {
    sql += ' AND ic.bb_position <= 0.10';
  }

  sql += ` ORDER BY ic.rsi14 ASC LIMIT ?`;
  params.push(limit);

  return db.prepare(sql).all(...params);
}

/**
 * إحصائيات الـ cache — كم سهم محسوب ومتى آخر تحديث
 */
export function getIndicatorsCacheStats() {
  const db = getDB();
  // استخدام per-symbol latest بدل global MAX (أدق)
  const stats = db.prepare(`
    WITH latest AS (
      SELECT symbol, MAX(bar_date) as max_date FROM indicators_cache GROUP BY symbol
    ),
    latest_rows AS (
      SELECT ic.* FROM indicators_cache ic
      JOIN latest l ON ic.symbol = l.symbol AND ic.bar_date = l.max_date
    )
    SELECT COUNT(DISTINCT symbol) as symbols_count,
           COUNT(*) as total_rows,
           MAX(bar_date) as latest_date,
           MIN(bar_date) as earliest_date,
           COUNT(CASE WHEN obv_divergence = 'bullish' THEN 1 END) as bullish_obv,
           COUNT(CASE WHEN rsi14 <= 35 THEN 1 END) as oversold_rsi,
           COUNT(CASE WHEN rsi14 <= 35 AND obv_divergence = 'bullish' THEN 1 END) as rsi_obv_combo
    FROM latest_rows
  `).get();
  return stats;
}

// ══════════════════════════════════════════════════════════════════════════════
// Phases 49-55 — New Tables Schema
// ══════════════════════════════════════════════════════════════════════════════

/**
 * Initialize all Phase 49-55 tables.
 * Called once at DB startup.
 */
export function initPhase49to55Schema() {
  const db = getDB();

  // ── Phase 49: Deep History ─────────────────────────────────────────────────
  db.exec(`
    CREATE TABLE IF NOT EXISTS ohlcv_weekly (
      symbol   TEXT    NOT NULL,
      bar_time INTEGER NOT NULL,
      open     REAL,
      high     REAL,
      low      REAL,
      close    REAL,
      volume   REAL,
      PRIMARY KEY (symbol, bar_time)
    );
    CREATE INDEX IF NOT EXISTS idx_ohlcv_w_sym_time ON ohlcv_weekly(symbol, bar_time DESC);

    CREATE TABLE IF NOT EXISTS ohlcv_monthly (
      symbol   TEXT    NOT NULL,
      bar_time INTEGER NOT NULL,
      open     REAL,
      high     REAL,
      low      REAL,
      close    REAL,
      volume   REAL,
      PRIMARY KEY (symbol, bar_time)
    );
    CREATE INDEX IF NOT EXISTS idx_ohlcv_m_sym_time ON ohlcv_monthly(symbol, bar_time DESC);

    CREATE TABLE IF NOT EXISTS deep_history_snapshot (
      id              INTEGER PRIMARY KEY AUTOINCREMENT,
      generated_at    TEXT NOT NULL,
      regime          TEXT,
      cycle_phase     TEXT,
      cycle_age_weeks INTEGER,
      avg_volatility  REAL,
      n_symbols_weekly  INTEGER,
      n_symbols_monthly INTEGER,
      regime_strength REAL,
      summary         TEXT
    );
  `);

  // ── Phase 50: Intraday ─────────────────────────────────────────────────────
  db.exec(`
    CREATE TABLE IF NOT EXISTS ohlcv_60min (
      symbol   TEXT    NOT NULL,
      bar_time INTEGER NOT NULL,
      open     REAL,
      high     REAL,
      low      REAL,
      close    REAL,
      volume   REAL,
      PRIMARY KEY (symbol, bar_time)
    );
    CREATE INDEX IF NOT EXISTS idx_ohlcv_60_sym_time ON ohlcv_60min(symbol, bar_time DESC);

    CREATE TABLE IF NOT EXISTS ohlcv_15min (
      symbol   TEXT    NOT NULL,
      bar_time INTEGER NOT NULL,
      open     REAL,
      high     REAL,
      low      REAL,
      close    REAL,
      volume   REAL,
      PRIMARY KEY (symbol, bar_time)
    );
    CREATE INDEX IF NOT EXISTS idx_ohlcv_15_sym_time ON ohlcv_15min(symbol, bar_time DESC);

    CREATE TABLE IF NOT EXISTS intraday_analytics (
      symbol               TEXT    NOT NULL,
      trade_date           TEXT    NOT NULL,
      vwap                 REAL,
      opening_range_high   REAL,
      opening_range_low    REAL,
      opening_gap_pct      REAL,
      first_hour_direction TEXT,
      volume_profile_bins  TEXT,
      session_bias         TEXT,
      best_entry_window    TEXT,
      volatility_percentile REAL,
      computed_at          TEXT,
      PRIMARY KEY (symbol, trade_date)
    );
    CREATE INDEX IF NOT EXISTS idx_intraday_sym_date ON intraday_analytics(symbol, trade_date DESC);
  `);

  // ── Phase 51: Cross-Market ─────────────────────────────────────────────────
  db.exec(`
    CREATE TABLE IF NOT EXISTS cross_market_daily (
      asset    TEXT    NOT NULL,
      bar_time INTEGER NOT NULL,
      open     REAL,
      high     REAL,
      low      REAL,
      close    REAL,
      volume   REAL,
      PRIMARY KEY (asset, bar_time)
    );
    CREATE INDEX IF NOT EXISTS idx_cross_asset_time ON cross_market_daily(asset, bar_time DESC);

    CREATE TABLE IF NOT EXISTS cross_market_regime (
      date          TEXT PRIMARY KEY,
      usdegp_regime TEXT,
      gold_regime   TEXT,
      em_regime     TEXT,
      oil_regime    TEXT,
      vix_regime    TEXT,
      risk_on_score REAL,
      macro_headwind TEXT,
      generated_at  TEXT
    );
  `);

  // ── Phase 52: Liquidity ────────────────────────────────────────────────────
  db.exec(`
    CREATE TABLE IF NOT EXISTS dom_snapshots (
      symbol        TEXT    NOT NULL,
      snapshot_time INTEGER NOT NULL,
      bids          TEXT,
      asks          TEXT,
      spread_pct    REAL,
      PRIMARY KEY (symbol, snapshot_time)
    );

    CREATE TABLE IF NOT EXISTS liquidity_profile (
      symbol              TEXT NOT NULL,
      computed_date       TEXT NOT NULL,
      advt_30d            REAL,
      advt_10d            REAL,
      amihud_ratio        REAL,
      turnover_velocity   REAL,
      bid_ask_spread_est  REAL,
      dom_spread_pct      REAL,
      max_safe_order_egp  REAL,
      liquidity_tier      TEXT,
      liquidity_score     REAL,
      PRIMARY KEY (symbol, computed_date)
    );
    CREATE INDEX IF NOT EXISTS idx_liq_sym_date ON liquidity_profile(symbol, computed_date DESC);
    CREATE INDEX IF NOT EXISTS idx_liq_tier     ON liquidity_profile(liquidity_tier);
  `);

  // ── Phase 53: Pine Analytics ───────────────────────────────────────────────
  db.exec(`
    CREATE TABLE IF NOT EXISTS pine_analytics (
      symbol               TEXT NOT NULL,
      trade_date           TEXT NOT NULL,
      volume_poc           REAL,
      volume_vah           REAL,
      volume_val           REAL,
      vwap                 REAL,
      opening_range_high   REAL,
      opening_range_low    REAL,
      session_bias         TEXT,
      rs_score             REAL,
      rs_percentile        REAL,
      corporate_event_flag INTEGER DEFAULT 0,
      corporate_event_type TEXT,
      raw_pine_data        TEXT,
      source_script        TEXT,
      PRIMARY KEY (symbol, trade_date)
    );
    CREATE INDEX IF NOT EXISTS idx_pine_sym_date ON pine_analytics(symbol, trade_date DESC);
    CREATE INDEX IF NOT EXISTS idx_pine_rs       ON pine_analytics(rs_score);
  `);

  // ── Phase 54: Corporate Actions ────────────────────────────────────────────
  db.exec(`
    CREATE TABLE IF NOT EXISTS corporate_actions (
      id               INTEGER PRIMARY KEY AUTOINCREMENT,
      symbol           TEXT NOT NULL,
      event_date       TEXT NOT NULL,
      event_type       TEXT NOT NULL,
      price_before     REAL,
      price_after      REAL,
      gap_pct          REAL,
      volume_on_day    REAL,
      avg_volume_20d   REAL,
      volume_multiple  REAL,
      adjustment_factor REAL,
      confidence       REAL,
      is_confirmed     INTEGER DEFAULT 0,
      is_adjusted      INTEGER DEFAULT 0,
      notes            TEXT,
      detected_at      TEXT,
      UNIQUE(symbol, event_date)
    );
    CREATE INDEX IF NOT EXISTS idx_ca_sym_date ON corporate_actions(symbol, event_date DESC);
    CREATE INDEX IF NOT EXISTS idx_ca_type     ON corporate_actions(event_type);
  `);

  // ── Phase 55: Data Quality ─────────────────────────────────────────────────
  db.exec(`
    CREATE TABLE IF NOT EXISTS data_quality_log (
      id                INTEGER PRIMARY KEY AUTOINCREMENT,
      check_type        TEXT NOT NULL,
      table_name        TEXT NOT NULL,
      symbol            TEXT,
      bar_date          TEXT,
      issue_description TEXT,
      severity          TEXT,
      status            TEXT DEFAULT 'OPEN',
      auto_fixed        INTEGER DEFAULT 0,
      checked_at        TEXT,
      resolved_at       TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_dql_status   ON data_quality_log(status);
    CREATE INDEX IF NOT EXISTS idx_dql_severity ON data_quality_log(severity);
    CREATE INDEX IF NOT EXISTS idx_dql_table    ON data_quality_log(table_name);

    CREATE TABLE IF NOT EXISTS data_trust_scores (
      source            TEXT PRIMARY KEY,
      trust_score       REAL,
      last_checked      TEXT,
      n_issues_open     INTEGER,
      n_issues_critical INTEGER,
      status            TEXT
    );
  `);
}

// ── Save helpers for new tables ──────────────────────────────────────────────

/** Save OHLCV bars to a specific timeframe table (weekly/monthly/60min/15min) */
export function saveOHLCVTimeframe(tableName, symbol, bars) {
  const ALLOWED = ['ohlcv_weekly', 'ohlcv_monthly', 'ohlcv_60min', 'ohlcv_15min'];
  if (!ALLOWED.includes(tableName)) throw new Error(`Invalid table: ${tableName}`);
  const cleanBars = (bars ?? []).filter((b) => {
    const time = Number(b?.time);
    const open = Number(b?.open);
    const high = Number(b?.high);
    const low = Number(b?.low);
    const close = Number(b?.close);
    const volume = Number(b?.volume ?? 0);
    if (![time, open, high, low, close, volume].every(Number.isFinite)) return false;
    if (time <= 0 || open <= 0 || close <= 0 || volume < 0) return false;
    if (high < low || high < open || high < close || low > open || low > close) return false;
    if (volume === 0 && open === close && high === close && low === close) return false;
    return true;
  });
  const db = getDB();
  const insert = db.prepare(`
    INSERT OR IGNORE INTO ${tableName} (symbol, bar_time, open, high, low, close, volume)
    VALUES (@symbol, @bar_time, @open, @high, @low, @close, @volume)
  `);
  const insertMany = db.transaction((rows) => {
    let count = 0;
    for (const b of rows) {
      const result = insert.run({
        symbol,
        bar_time: b.time,
        open: b.open ?? null, high: b.high ?? null,
        low:  b.low  ?? null, close: b.close ?? null,
        volume: b.volume ?? null,
      });
      count += result.changes;
    }
    return count;
  });
  return insertMany(cleanBars);
}

/** Get OHLCV from a specific timeframe table */
export function getOHLCVTimeframe(tableName, symbol, limit = 500) {
  const ALLOWED = ['ohlcv_weekly', 'ohlcv_monthly', 'ohlcv_60min', 'ohlcv_15min'];
  if (!ALLOWED.includes(tableName)) throw new Error(`Invalid table: ${tableName}`);
  const db = getDB();
  return db.prepare(`
    SELECT bar_time as time, open, high, low, close, volume
    FROM ${tableName}
    WHERE symbol = ?
    ORDER BY bar_time ASC
    LIMIT ?
  `).all(symbol, limit);
}

/** Save cross-market daily bars */
export function saveCrossMarket(asset, bars) {
  const db = getDB();
  const insert = db.prepare(`
    INSERT OR IGNORE INTO cross_market_daily (asset, bar_time, open, high, low, close, volume)
    VALUES (@asset, @bar_time, @open, @high, @low, @close, @volume)
  `);
  const insertMany = db.transaction((rows) => {
    let count = 0;
    for (const b of rows) {
      const r = insert.run({ asset, bar_time: b.time, open: b.open ?? null, high: b.high ?? null,
                             low: b.low ?? null, close: b.close ?? null, volume: b.volume ?? null });
      count += r.changes;
    }
    return count;
  });
  return insertMany(bars);
}

/** Get cross-market bars for an asset */
export function getCrossMarket(asset, limit = 500) {
  const db = getDB();
  return db.prepare(`
    SELECT bar_time as time, open, high, low, close, volume
    FROM cross_market_daily WHERE asset = ?
    ORDER BY bar_time ASC LIMIT ?
  `).all(asset, limit);
}

/** Get coverage stats for cross_market_daily */
export function getCrossMarketCoverage() {
  const db = getDB();
  return db.prepare(`
    SELECT asset, COUNT(*) as bars,
           MIN(date(bar_time,'unixepoch')) as oldest,
           MAX(date(bar_time,'unixepoch')) as newest
    FROM cross_market_daily
    GROUP BY asset ORDER BY bars DESC
  `).all();
}

/** Get coverage stats for any OHLCV table */
export function getTimeframeCoverage(tableName) {
  const ALLOWED = ['ohlcv_weekly', 'ohlcv_monthly', 'ohlcv_60min', 'ohlcv_15min'];
  if (!ALLOWED.includes(tableName)) throw new Error(`Invalid table: ${tableName}`);
  const db = getDB();
  return db.prepare(`
    SELECT COUNT(DISTINCT symbol) as symbols,
           COUNT(*) as total_bars,
           MIN(date(bar_time,'unixepoch')) as oldest,
           MAX(date(bar_time,'unixepoch')) as newest
    FROM ${tableName}
  `).get();
}

/** Save DOM snapshot */
export function saveDOMSnapshot(symbol, bids, asks, spreadPct) {
  const db = getDB();
  db.prepare(`
    INSERT OR REPLACE INTO dom_snapshots (symbol, snapshot_time, bids, asks, spread_pct)
    VALUES (?, ?, ?, ?, ?)
  `).run(symbol, Math.floor(Date.now() / 1000), JSON.stringify(bids), JSON.stringify(asks), spreadPct);
}

/** Get latest liquidity profile for a symbol */
export function getLiquidityProfile(symbol) {
  const db = getDB();
  return db.prepare(`
    SELECT * FROM liquidity_profile
    WHERE symbol = ?
    ORDER BY computed_date DESC LIMIT 1
  `).get(symbol);
}

/** Get liquidity tier summary */
export function getLiquidityTierSummary() {
  const db = getDB();
  return db.prepare(`
    WITH latest AS (
      SELECT symbol, MAX(computed_date) as max_date FROM liquidity_profile GROUP BY symbol
    )
    SELECT lp.liquidity_tier, COUNT(*) as count,
           AVG(lp.advt_10d) as avg_advt_10d
    FROM liquidity_profile lp
    JOIN latest l ON lp.symbol = l.symbol AND lp.computed_date = l.max_date
    GROUP BY lp.liquidity_tier
    ORDER BY count DESC
  `).all();
}

/** Save pine analytics data */
export function savePineAnalytics(symbol, tradeDate, data) {
  const db = getDB();
  db.prepare(`
    INSERT OR REPLACE INTO pine_analytics
      (symbol, trade_date, volume_poc, volume_vah, volume_val, vwap,
       opening_range_high, opening_range_low, session_bias,
       rs_score, rs_percentile, corporate_event_flag, corporate_event_type,
       raw_pine_data, source_script)
    VALUES (@symbol, @trade_date, @volume_poc, @volume_vah, @volume_val, @vwap,
            @opening_range_high, @opening_range_low, @session_bias,
            @rs_score, @rs_percentile, @corporate_event_flag, @corporate_event_type,
            @raw_pine_data, @source_script)
  `).run({
    symbol, trade_date: tradeDate,
    volume_poc: data.volume_poc ?? null,
    volume_vah: data.volume_vah ?? null,
    volume_val: data.volume_val ?? null,
    vwap: data.vwap ?? null,
    opening_range_high: data.opening_range_high ?? null,
    opening_range_low: data.opening_range_low ?? null,
    session_bias: data.session_bias ?? null,
    rs_score: data.rs_score ?? null,
    rs_percentile: data.rs_percentile ?? null,
    corporate_event_flag: data.corporate_event_flag ?? 0,
    corporate_event_type: data.corporate_event_type ?? null,
    raw_pine_data: data.raw_pine_data ? JSON.stringify(data.raw_pine_data) : null,
    source_script: data.source_script ?? null,
  });
}

export default { getDB, saveScan, saveTrade, savePostMortem, updateSetupPerformance,
                 getStats, getBestSetups, saveOHLCV, getOHLCV, upsertStockUniverse, getHistoryStats,
                 saveRulePerformance, getRulePerformance, getAllRulesPerformance,
                 saveFinancialData, getFinancialData, getUndervaluedStocks,
                 addNote, searchNotes, saveDailyReport, getLastReport, getReportByDate,
                 getOHLCVRange, getStaleSymbols,
                 saveIndicatorsCache, getLatestIndicators, getSignalsFromCache, getIndicatorsCacheStats,
                 // Phase 49-55
                 initPhase49to55Schema,
                 saveOHLCVTimeframe, getOHLCVTimeframe,
                 saveCrossMarket, getCrossMarket, getCrossMarketCoverage,
                 getTimeframeCoverage,
                 saveDOMSnapshot, getLiquidityProfile, getLiquidityTierSummary,
                 savePineAnalytics };
