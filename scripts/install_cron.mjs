#!/usr/bin/env node
/**
 * تثبيت cron job للتشغيل التلقائي اليومي
 * ==========================================
 * يضيف مهمة cron تعمل تلقائياً بعد إغلاق البورصة المصرية
 * وقت الإغلاق: 3:30 PM توقيت القاهرة = 1:30 PM UTC (صيف) / 12:30 UTC (شتاء)
 *
 * التشغيل:
 *   node scripts/install_cron.mjs           — يثبّت cron (الأحد-الخميس 4:30 م)
 *   node scripts/install_cron.mjs --remove  — يحذف cron
 *   node scripts/install_cron.mjs --show    — يعرض cron الحالي
 *
 * المالك: Dr. Husam | مايو 2026
 */

import { execSync } from 'child_process';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __dirname  = dirname(fileURLToPath(import.meta.url));
const ROOT       = join(__dirname, '..');
const LOG_FILE   = join(ROOT, 'logs', 'tv_auto_daily.log');
const SCRIPT     = join(ROOT, 'scripts', 'egx_tv_auto_update.mjs');
const LOCK_SCRIPT= join(ROOT, 'scripts', 'with_lock.mjs');
const NODE_BIN   = process.execPath;   // المسار الكامل لـ node
const MARKER         = '# EGX-DAILY-AUTOMATION';
const MARKER_MACRO   = '# EGX-MACRO-WEEKLY';
const MARKER_TG      = '# EGX-TELEGRAM-DAILY';
const MARKER_DMIDS   = '# EGX-DMIDS-WEEKLY';
const MARKER_DHVD    = '# EGX-DHVD-MONTHLY';
const MARKER_EVO_Q   = '# EGX-EVO-QUICK-DAILY';
const MARKER_EVO_F   = '# EGX-EVO-FULL-WEEKLY';
const MARKER_COG_Q     = '# EGX-COG-QUICK-DAILY';
const MARKER_COG_F     = '# EGX-COG-FULL-WEEKLY';
const MARKER_GRAPH_W   = '# EGX-GRAPH-WEEKLY';
const MARKER_RL_W      = '# EGX-RL-WEEKLY';
const MARKER_EXPLAIN_Q = '# EGX-EXPLAIN-QUICK-DAILY';
const MARKER_MACRO_G   = '# EGX-MACRO-GLOBAL-WEEKLY';
const MARKER_TV_LIVE_1 = '# EGX-TV-LIVE-MIDDAY';
const MARKER_TV_LIVE_2 = '# EGX-TV-LIVE-CLOSE';

// وقت التشغيل: الأحد-الخميس الساعة 4:30 PM القاهرة
// القاهرة = UTC+2 (صيف) / UTC+3 (رمضان/شتاء 2025+)
// نستخدم 14:30 UTC = 4:30 PM القاهرة صيف
const CRON_TIME  = '30 14 * * 0-4';   // الأحد(0) - الخميس(4)
const locked = (scope, command) => `${NODE_BIN} "${LOCK_SCRIPT}" ${scope} -- ${command}`;
// TV sync uses its own lock — must not block Telegram (egx-telegram) or other jobs.
const CRON_LINE  = `${CRON_TIME} cd "${ROOT}" && ${locked('egx-tv-sync', `${NODE_BIN} "${SCRIPT}" --launch --pine --tech`)} >> "${LOG_FILE}" 2>&1 ${MARKER}`;

// Macro refresh أسبوعي كل أحد 9 صباحاً (قبل السوق) لتحديث الـ 22 مؤشر
const MACRO_SCRIPT   = join(ROOT, 'scripts', 'fetch_economics.mjs');
const MACRO_LOG      = join(ROOT, 'logs', 'macro.log');
const CRON_MACRO     = `0 7 * * 0 cd "${ROOT}" && ${NODE_BIN} "${MACRO_SCRIPT}" --bars 24 >> "${MACRO_LOG}" 2>&1 ${MARKER_MACRO}`;

// Telegram daily briefing — الأحد-الخميس 5:00 PM القاهرة (15:00 UTC صيف)
// يعمل بعد egx_tv_auto_update (المسار الرسمي) لتضمين أحدث البيانات
const TG_SCRIPT      = join(ROOT, 'scripts', 'egx_telegram_cron.mjs');
const TG_LOG         = join(ROOT, 'logs', 'telegram.log');
// Full automation: prepare-send → live telegram → reconcile (separate lock from TV sync).
const CRON_ENV       = `PYTHON_BIN=/usr/bin/python3`;
const CRON_TG        = `20 15 * * 0-4 cd "${ROOT}" && ${CRON_ENV} ${locked('egx-telegram', `${NODE_BIN} "${TG_SCRIPT}"`)} >> "${TG_LOG}" 2>&1 ${MARKER_TG}`;

// TradingView live snapshots — خفيفة ومحدودة أثناء الجلسة لتغذية quotes/DOM
const TV_LIVE_SCRIPT = join(ROOT, 'scripts', 'fetch_intraday_live.mjs');
const TV_LIVE_LOG    = join(ROOT, 'logs', 'tv_live.log');
const CRON_TV_LIVE_1 = `30 10 * * 0-4 cd "${ROOT}" && ${locked('egx-tv-live', `${NODE_BIN} "${TV_LIVE_SCRIPT}" --quotes --dom --once`)} >> "${TV_LIVE_LOG}" 2>&1 ${MARKER_TV_LIVE_1}`;
const CRON_TV_LIVE_2 = `15 13 * * 0-4 cd "${ROOT}" && ${locked('egx-tv-live', `${NODE_BIN} "${TV_LIVE_SCRIPT}" --quotes --dom --once`)} >> "${TV_LIVE_LOG}" 2>&1 ${MARKER_TV_LIVE_2}`;

// DMIDS Deep Intelligence Discovery — كل أحد 8 PM القاهرة (18:00 UTC صيف)
// يعمل مرة أسبوعياً لإعادة تحليل 249 سهم وتحديث قاعدة المعرفة الهيكلية
// بحث داخلي فقط: log-only، بدون --notify في cron
const DMIDS_SCRIPT   = join(ROOT, 'scripts', 'egx_discover.mjs');
const DMIDS_LOG      = join(ROOT, 'logs', 'discovery.log');
const CRON_DMIDS     = `0 18 * * 0 cd "${ROOT}" && ${NODE_BIN} "${DMIDS_SCRIPT}" >> "${DMIDS_LOG}" 2>&1 ${MARKER_DMIDS}`;

// DHVD Deep Historical Validation — كل أول أحد من الشهر 7 PM القاهرة (17:00 UTC صيف)
// يُعيد التحقق من جميع القوانين المكتشفة عبر كامل التاريخ، log-only بدون إرسال عميل
const DHVD_SCRIPT    = join(ROOT, 'scripts', 'egx_dhvd.mjs');
const DHVD_LOG       = join(ROOT, 'logs', 'dhvd.log');
const CRON_DHVD      = `0 17 1-7 * 0 cd "${ROOT}" && ${NODE_BIN} "${DHVD_SCRIPT}" >> "${DHVD_LOG}" 2>&1 ${MARKER_DHVD}`;

// Phase 15 — Self-Learning Evolution Engine
// Quick evolution (confidence + reinforcement): الأحد-الخميس 4:45 PM القاهرة (14:45 UTC صيف)
//   يعمل بعد egx_tv_auto_update — يحدّث مستويات الثقة والتعزيز بناءً على آخر جلسة
const EVO_SCRIPT     = join(ROOT, 'scripts', 'egx_evolution.mjs');
const EVO_LOG        = join(ROOT, 'logs', 'evolution.log');
const CRON_EVO_Q     = `50 14 * * 0-4 cd "${ROOT}" && ${locked('egx-daily', `${NODE_BIN} "${EVO_SCRIPT}" --quick`)} >> "${EVO_LOG}" 2>&1 ${MARKER_EVO_Q}`;

// Full evolution (7-stage pipeline): كل أحد 9 PM القاهرة (19:00 UTC صيف)
//   يعمل بعد DMIDS (18:00) ليستفيد من أحدث نتائج الاكتشاف الهيكلي
//   يشمل: الفشل، ذاكرة الأسهم، الفرضيات، نماذج النظام
const CRON_EVO_F     = `0 19 * * 0 cd "${ROOT}" && ${NODE_BIN} "${EVO_SCRIPT}" >> "${EVO_LOG}" 2>&1 ${MARKER_EVO_F}`;

// Phase 16 — Autonomous Cognition Engine
// Quick cognition (stock DNA + laws + evolve): الأحد-الخميس 5:00 PM القاهرة (15:30 UTC صيف)
//   يعمل 30 دقيقة بعد Telegram digest لتجنب التزامن
const COG_SCRIPT     = join(ROOT, 'scripts', 'egx_cognition.mjs');
const COG_LOG        = join(ROOT, 'logs', 'cognition.log');
const CRON_COG_Q     = `45 15 * * 0-4 cd "${ROOT}" && ${locked('egx-daily', `${NODE_BIN} "${COG_SCRIPT}" --quick`)} >> "${COG_LOG}" 2>&1 ${MARKER_COG_Q}`;

// Full cognition (7-stage pipeline): كل أحد 10 PM القاهرة (20:00 UTC صيف)
//   يعمل بعد EVO FULL (19:00) ليستفيد من أحدث نتائج التطور الذاتي
const CRON_COG_F     = `0 20 * * 0 cd "${ROOT}" && ${NODE_BIN} "${COG_SCRIPT}" >> "${COG_LOG}" 2>&1 ${MARKER_COG_F}`;

// Phase 17 — Graph Contagion Engine (weekly full analysis)
// كل أحد 10:30 PM القاهرة (20:30 UTC صيف) — بعد COG FULL
const GRAPH_SCRIPT   = join(ROOT, 'scripts', 'egx_graph.mjs');
const GRAPH_LOG      = join(ROOT, 'logs', 'graph.log');
const CRON_GRAPH_W   = `30 20 * * 0 cd "${ROOT}" && ${NODE_BIN} "${GRAPH_SCRIPT}" --section full >> "${GRAPH_LOG}" 2>&1 ${MARKER_GRAPH_W}`;

// Phase 18 — RL Walk-Forward Backtesting (weekly validation)
// كل أحد 11:00 PM القاهرة (21:00 UTC صيف) — بعد Graph
const RL_SCRIPT      = join(ROOT, 'scripts', 'egx_rl.mjs');
const RL_LOG         = join(ROOT, 'logs', 'rl.log');
const CRON_RL_W      = `0 21 * * 0 cd "${ROOT}" && ${NODE_BIN} "${RL_SCRIPT}" --section walkforward >> "${RL_LOG}" 2>&1 ${MARKER_RL_W}`;

// Phase 19 — SHAP Explainability daily explanations
// الأحد-الخميس 4:45 PM القاهرة (14:45 UTC صيف) — بالتوازي مع Evo-Quick
const EXPLAIN_SCRIPT = join(ROOT, 'scripts', 'egx_explain.mjs');
const EXPLAIN_LOG    = join(ROOT, 'logs', 'explain.log');
const CRON_EXPLAIN_Q = `5 15 * * 0-4 cd "${ROOT}" && ${locked('egx-daily', `${NODE_BIN} "${EXPLAIN_SCRIPT}" --section daily`)} >> "${EXPLAIN_LOG}" 2>&1 ${MARKER_EXPLAIN_Q}`;

// Global Macro Fetch — 22 indicators via yfinance/stooq
// كل أحد 6:30 AM القاهرة (04:30 UTC صيف) — قبل فتح أسواق الأسهم الأوروبية
const MACRO_G_SCRIPT = join(ROOT, 'scripts', 'python', 'fetch_global_macro.py');
const MACRO_G_LOG    = join(ROOT, 'logs', 'macro_global.log');
const PYTHON_BIN_VAR = 'python3';
const CRON_MACRO_G   = `30 4 * * 0 ${PYTHON_BIN_VAR} "${MACRO_G_SCRIPT}" fetch_all '{}' >> "${MACRO_G_LOG}" 2>&1 ${MARKER_MACRO_G}`;

// ── Phase 20-28 Cron Jobs ────────────────────────────────────────────────────
const MARKER_INTEGRITY  = '# EGX-INTEGRITY-WEEKLY';
const MARKER_UMCG_W     = '# EGX-UMCG-WEEKLY';
const MARKER_CAUSAL_W   = '# EGX-CAUSAL-WEEKLY';
const MARKER_FAILURE_D  = '# EGX-FAILURE-DAILY';
const MARKER_EXPLOSION_D= '# EGX-EXPLOSION-DAILY';
const MARKER_DNA_W      = '# EGX-DNA-WEEKLY';
const MARKER_RESEARCH_W = '# EGX-RESEARCH-WEEKLY';
const MARKER_EXECUTION_W= '# EGX-EXECUTION-WEEKLY';
const MARKER_SYNTHESIS_D= '# EGX-SYNTHESIS-DAILY';
const MARKER_PRIORITIZE_D='# EGX-PRIORITIZE-DAILY';
const MARKER_MEMORY_W   = '# EGX-MEMORY-WEEKLY';
const MARKER_META_W     = '# EGX-META-WEEKLY';
const MARKER_PORTFOLIO_D= '# EGX-PORTFOLIO-DAILY';
const MARKER_TRANSITION_D='# EGX-TRANSITION-DAILY';
const MARKER_ANTILAWS_D  = '# EGX-ANTILAWS-DAILY';
const MARKER_STAT_W        = '# EGX-STAT-WEEKLY';
const MARKER_ARBITRATE_D   = '# EGX-ARBITRATE-DAILY';
const MARKER_OBSERVATORY_D = '# EGX-OBSERVATORY-DAILY';
const MARKER_COMPRESSION_D = '# EGX-COMPRESSION-DAILY';
const MARKER_UNCERTAINTY_W = '# EGX-UNCERTAINTY-WEEKLY';
const MARKER_SANDBOX_W     = '# EGX-SANDBOX-WEEKLY';
const MARKER_GOVERNANCE_D  = '# EGX-GOVERNANCE-DAILY';
const MARKER_COGBUS_D      = '# EGX-COGBUS-DAILY';
const MARKER_PRESSURE_W    = '# EGX-PRESSURE-WEEKLY';
const MARKER_EXECREAL_W    = '# EGX-EXECREAL-WEEKLY';
const MARKER_HEALTH_D      = '# EGX-HEALTH-DAILY';
const MARKER_FUNNEL_D      = '# EGX-FUNNEL-DAILY';
const MARKER_DEPS_W        = '# EGX-DEPS-WEEKLY';
const MARKER_TRAIN_W       = '# EGX-TRAIN-HEALTH-WEEKLY';
const MARKER_NIGHT_W       = '# EGX-NIGHTLAB-WEEKLY';
const MARKER_PROD_STATUS_D = '# EGX-PROD-STATUS-DAILY';
const MARKER_SESSION_READY = '# EGX-SESSION-READY-DAILY';
const MARKER_CRON_LOG_CHK  = '# EGX-CRON-LOG-CHECK-DAILY';
const MARKER_QUALITY_W     = '# EGX-QUALITY-WEEKLY';
const MARKER_LEARNING_W    = '# EGX-LEARNING-LOOP-WEEKLY';
const MARKER_PROD_READY_W  = '# EGX-PROD-READY-WEEKLY';
const MARKER_VERIFY_D      = '# EGX-FULL-VERIFY-DAILY';
const MARKER_POST_SESSION  = '# EGX-POST-SESSION-DAILY';
const MARKER_MLADV_W       = '# EGX-MLADV-WEEKLY';

const INTEGRITY_SCRIPT  = join(ROOT, 'scripts', 'egx_integrity.mjs');
const INTEGRITY_LOG     = join(ROOT, 'logs', 'integrity.log');
// Phase 20 — Integrity scan: كل أحد 5:30 AM (قبل السوق) — scan كامل للبيانات
const CRON_INTEGRITY_W  = `30 5 * * 0 cd "${ROOT}" && ${NODE_BIN} "${INTEGRITY_SCRIPT}" full >> "${INTEGRITY_LOG}" 2>&1 ${MARKER_INTEGRITY}`;

const UMCG_SCRIPT       = join(ROOT, 'scripts', 'egx_umcg.mjs');
const UMCG_LOG          = join(ROOT, 'logs', 'umcg.log');
// Phase 21 — UMCG weekly snapshot: كل أحد 11:30 PM (بعد RL)
const CRON_UMCG_W       = `30 21 * * 0 cd "${ROOT}" && ${NODE_BIN} "${UMCG_SCRIPT}" snapshot >> "${UMCG_LOG}" 2>&1 ${MARKER_UMCG_W}`;

const CAUSAL_DISC_SCRIPT= join(ROOT, 'scripts', 'egx_causal_disc.mjs');
const CAUSAL_DISC_LOG   = join(ROOT, 'logs', 'causal_disc.log');
// Phase 22 — Causal discovery: كل أحد 11:00 PM (مع UMCG slot)
const CRON_CAUSAL_W     = `0 22 * * 0 cd "${ROOT}" && ${NODE_BIN} "${CAUSAL_DISC_SCRIPT}" full >> "${CAUSAL_DISC_LOG}" 2>&1 ${MARKER_CAUSAL_W}`;

const FAILURE_SCRIPT    = join(ROOT, 'scripts', 'egx_failure.mjs');
const FAILURE_LOG       = join(ROOT, 'logs', 'failure.log');
// Phase 23 — Failure daily scan: الأحد-الخميس 3:00 PM القاهرة (13:00 UTC)
const CRON_FAILURE_D    = `0 13 * * 0-4 cd "${ROOT}" && ${locked('egx-daily', `${NODE_BIN} "${FAILURE_SCRIPT}" scan`)} >> "${FAILURE_LOG}" 2>&1 ${MARKER_FAILURE_D}`;

const EXPLOSION_SCRIPT  = join(ROOT, 'scripts', 'egx_explosion.mjs');
const EXPLOSION_LOG     = join(ROOT, 'logs', 'explosion.log');
// Phase 24 — Explosion readiness: الأحد-الخميس 4:15 PM القاهرة (14:15 UTC) — قبل egx_tv_auto_update
const CRON_EXPLOSION_D  = `15 14 * * 0-4 cd "${ROOT}" && ${locked('egx-daily', `${NODE_BIN} "${EXPLOSION_SCRIPT}" readiness`)} >> "${EXPLOSION_LOG}" 2>&1 ${MARKER_EXPLOSION_D}`;

const DNA_SCRIPT        = join(ROOT, 'scripts', 'egx_dna.mjs');
const DNA_LOG           = join(ROOT, 'logs', 'dna.log');
// Phase 25 — DNA rebuild: كل أحد 9:30 PM (21:30 UTC) — بعد Graph
const CRON_DNA_W        = `30 19 * * 0 cd "${ROOT}" && ${NODE_BIN} "${DNA_SCRIPT}" full >> "${DNA_LOG}" 2>&1 ${MARKER_DNA_W}`;

const RESEARCH_SCRIPT   = join(ROOT, 'scripts', 'egx_research.mjs');
const RESEARCH_LOG      = join(ROOT, 'logs', 'research.log');
// Phase 26 — Research evolution cycle: كل أحد 10:30 PM (22:30 UTC) — بعد DNA
const CRON_RESEARCH_W   = `30 22 * * 0 cd "${ROOT}" && ${NODE_BIN} "${RESEARCH_SCRIPT}" evolve >> "${RESEARCH_LOG}" 2>&1 ${MARKER_RESEARCH_W}`;

const EXECUTION_SCRIPT  = join(ROOT, 'scripts', 'egx_execution.mjs');
const EXECUTION_LOG     = join(ROOT, 'logs', 'execution.log');
// Phase 27 — Execution profiles: كل أحد 7:00 AM (05:00 UTC) — قبل السوق
const CRON_EXECUTION_W  = `15 7 * * 0 cd "${ROOT}" && ${NODE_BIN} "${EXECUTION_SCRIPT}" liquidity >> "${EXECUTION_LOG}" 2>&1 ${MARKER_EXECUTION_W}`;

const SYNTHESIS_SCRIPT  = join(ROOT, 'scripts', 'egx_synthesis.mjs');
const SYNTHESIS_LOG     = join(ROOT, 'logs', 'synthesis.log');
// Phase 28 — Daily synthesis: الأحد-الخميس 5:45 PM القاهرة (15:45 UTC) — التقرير الذكي اليومي
const CRON_SYNTHESIS_D  = `55 15 * * 0-4 cd "${ROOT}" && ${locked('egx-daily', `${NODE_BIN} "${SYNTHESIS_SCRIPT}" run`)} >> "${SYNTHESIS_LOG}" 2>&1 ${MARKER_SYNTHESIS_D}`;

const PRIORITIZER_SCRIPT= join(ROOT, 'scripts', 'egx_prioritizer.mjs');
const PRIORITIZER_LOG   = join(ROOT, 'logs', 'prioritizer.log');
// Phase 29 — Intelligence brief: الأحد-الخميس 4:00 PM القاهرة (14:00 UTC) — قبل Synthesis
const CRON_PRIORITIZE_D = `0 14 * * 0-4 cd "${ROOT}" && ${locked('egx-daily', `${NODE_BIN} "${PRIORITIZER_SCRIPT}" brief`)} >> "${PRIORITIZER_LOG}" 2>&1 ${MARKER_PRIORITIZE_D}`;

const MEMORY_SCRIPT     = join(ROOT, 'scripts', 'egx_memory.mjs');
const MEMORY_LOG        = join(ROOT, 'logs', 'memory.log');
// Phase 30 — Episodic memory: كل أحد 11:00 PM — تحديث ذاكرة الحلقات التاريخية
const CRON_MEMORY_W     = `15 21 * * 0 cd "${ROOT}" && ${NODE_BIN} "${MEMORY_SCRIPT}" full >> "${MEMORY_LOG}" 2>&1 ${MARKER_MEMORY_W}`;

const META_SCRIPT       = join(ROOT, 'scripts', 'egx_meta.mjs');
const META_LOG          = join(ROOT, 'logs', 'meta.log');
// Phase 31 — Meta-learning: كل أحد 8:00 PM — بعد DNA وقبل Research
const CRON_META_W       = `15 20 * * 0 cd "${ROOT}" && ${NODE_BIN} "${META_SCRIPT}" full >> "${META_LOG}" 2>&1 ${MARKER_META_W}`;

const PORTFOLIO_COG_SCRIPT= join(ROOT, 'scripts', 'egx_portfolio_cog.mjs');
const PORTFOLIO_COG_LOG = join(ROOT, 'logs', 'portfolio_cog.log');
// Phase 32 — Portfolio cognition: الأحد-الخميس 4:30 PM القاهرة (14:30 UTC) — بعد Prioritizer
const CRON_PORTFOLIO_D  = `40 14 * * 0-4 cd "${ROOT}" && ${locked('egx-daily', `${NODE_BIN} "${PORTFOLIO_COG_SCRIPT}" build`)} >> "${PORTFOLIO_COG_LOG}" 2>&1 ${MARKER_PORTFOLIO_D}`;

const TRANSITION_SCRIPT = join(ROOT, 'scripts', 'egx_transition.mjs');
const TRANSITION_LOG    = join(ROOT, 'logs', 'transition.log');
// Phase 33 — Regime transition EWI: الأحد-الخميس 3:30 PM القاهرة (13:30 UTC) — قبل Prioritizer
const CRON_TRANSITION_D = `30 13 * * 0-4 cd "${ROOT}" && ${locked('egx-daily', `${NODE_BIN} "${TRANSITION_SCRIPT}" alert`)} >> "${TRANSITION_LOG}" 2>&1 ${MARKER_TRANSITION_D}`;

const ANTI_LAWS_SCRIPT  = join(ROOT, 'scripts', 'egx_anti_laws.mjs');
const ANTI_LAWS_LOG     = join(ROOT, 'logs', 'anti_laws.log');
// Phase 35 — Anti-laws daily scan: الأحد-الخميس 1:00 PM القاهرة (11:00 UTC) — حماية أول الجلسة
const CRON_ANTILAWS_D   = `0 11 * * 0-4 cd "${ROOT}" && ${locked('egx-daily', `${NODE_BIN} "${ANTI_LAWS_SCRIPT}" daily`)} >> "${ANTI_LAWS_LOG}" 2>&1 ${MARKER_ANTILAWS_D}`;

const STAT_SCRIPT       = join(ROOT, 'scripts', 'egx_stat_grounding.mjs');
const STAT_LOG          = join(ROOT, 'logs', 'stat_grounding.log');
// Phase 36 — Statistical grounding: كل أحد 5:00 PM (17:00 UTC) — مراجعة أسبوعية للقوانين
const CRON_STAT_W       = `15 17 * * 0 cd "${ROOT}" && ${NODE_BIN} "${STAT_SCRIPT}" full >> "${STAT_LOG}" 2>&1 ${MARKER_STAT_W}`;

const ARBITRATE_SCRIPT  = join(ROOT, 'scripts', 'egx_arbitration.mjs');
const ARBITRATE_LOG     = join(ROOT, 'logs', 'arbitration.log');
// Phase 34 — Cognitive arbitration: الأحد-الخميس 3:45 PM القاهرة (13:45 UTC) — بعد Anti-laws وEWI
const CRON_ARBITRATE_D  = `45 13 * * 0-4 cd "${ROOT}" && ${locked('egx-daily', `${NODE_BIN} "${ARBITRATE_SCRIPT}" decisions`)} >> "${ARBITRATE_LOG}" 2>&1 ${MARKER_ARBITRATE_D}`;

const OBSERVATORY_SCRIPT = join(ROOT, 'scripts', 'egx_observatory.mjs');
const OBSERVATORY_LOG    = join(ROOT, 'logs', 'observatory.log');
// Phase 37 — Observatory health check: الأحد-الخميس 7:00 AM (5:00 UTC) — قبل السوق، التحقق من صحة النظام
const CRON_OBSERVATORY_D = `0 5 * * 0-4 cd "${ROOT}" && ${NODE_BIN} "${OBSERVATORY_SCRIPT}" full >> "${OBSERVATORY_LOG}" 2>&1 ${MARKER_OBSERVATORY_D}`;

const COMPRESSION_SCRIPT = join(ROOT, 'scripts', 'egx_compression.mjs');
const COMPRESSION_LOG    = join(ROOT, 'logs', 'compression.log');
// Phase 38 — Compression MII: الأحد-الخميس 5:00 PM القاهرة (15:00 UTC) — ملخص يومي بعد السوق
const CRON_COMPRESSION_D = `35 15 * * 0-4 cd "${ROOT}" && ${locked('egx-daily', `${NODE_BIN} "${COMPRESSION_SCRIPT}" full`)} >> "${COMPRESSION_LOG}" 2>&1 ${MARKER_COMPRESSION_D}`;

const UNCERTAINTY_SCRIPT = join(ROOT, 'scripts', 'egx_uncertainty.mjs');
const UNCERTAINTY_LOG    = join(ROOT, 'logs', 'uncertainty.log');
// Phase 39 — Uncertainty report: كل أحد 6:00 PM (16:00 UTC) — مراجعة أسبوعية
const CRON_UNCERTAINTY_W = `0 16 * * 0 cd "${ROOT}" && ${NODE_BIN} "${UNCERTAINTY_SCRIPT}" full >> "${UNCERTAINTY_LOG}" 2>&1 ${MARKER_UNCERTAINTY_W}`;

const SANDBOX_SCRIPT     = join(ROOT, 'scripts', 'egx_sandbox.mjs');
const SANDBOX_LOG        = join(ROOT, 'logs', 'sandbox.log');
// Phase 40 — Research sandbox cycle: كل أحد 7:00 PM (17:00 UTC) — اكتشاف قوانين جديدة أسبوعياً
const CRON_SANDBOX_W     = `30 17 * * 0 cd "${ROOT}" && ${NODE_BIN} "${SANDBOX_SCRIPT}" full >> "${SANDBOX_LOG}" 2>&1 ${MARKER_SANDBOX_W}`;

const GOVERNANCE_SCRIPT  = join(ROOT, 'scripts', 'egx_governance.mjs');
const GOVERNANCE_LOG     = join(ROOT, 'logs', 'governance.log');
// Phase 41 — Governance audit: الأحد-الخميس 4:00 AM (2:00 UTC) — قبل السوق، مراجعة الدستور
const CRON_GOVERNANCE_D  = `0 2 * * 0-4 cd "${ROOT}" && ${locked('egx-daily', `${NODE_BIN} "${GOVERNANCE_SCRIPT}" full`)} >> "${GOVERNANCE_LOG}" 2>&1 ${MARKER_GOVERNANCE_D}`;

const COGBUS_SCRIPT      = join(ROOT, 'scripts', 'egx_cogbus.mjs');
const COGBUS_LOG         = join(ROOT, 'logs', 'cogbus.log');
// Phase 42 — Cognitive Bus: الأحد-الخميس 5:30 AM (3:30 UTC) — بعد Observatory، قبل السوق
const CRON_COGBUS_D      = `30 3 * * 0-4 cd "${ROOT}" && ${locked('egx-daily', `${NODE_BIN} "${COGBUS_SCRIPT}" full`)} >> "${COGBUS_LOG}" 2>&1 ${MARKER_COGBUS_D}`;

const PRESSURE_SCRIPT    = join(ROOT, 'scripts', 'egx_pressure.mjs');
const PRESSURE_LOG       = join(ROOT, 'logs', 'pressure.log');
// Phase 43 — Pressure zones guided cycle: كل أحد 8:00 PM (18:00 UTC) — بعد Sandbox
const CRON_PRESSURE_W    = `30 18 * * 0 cd "${ROOT}" && ${NODE_BIN} "${PRESSURE_SCRIPT}" full >> "${PRESSURE_LOG}" 2>&1 ${MARKER_PRESSURE_W}`;

const EXECREAL_SCRIPT    = join(ROOT, 'scripts', 'egx_exec_reality.mjs');
const EXECREAL_LOG       = join(ROOT, 'logs', 'exec_reality.log');
// Phase 44 — Execution reality check: كل أحد 9:00 PM (19:00 UTC) — مراجعة أسبوعية للتنفيذ
const CRON_EXECREAL_W    = `45 19 * * 0 cd "${ROOT}" && ${NODE_BIN} "${EXECREAL_SCRIPT}" full >> "${EXECREAL_LOG}" 2>&1 ${MARKER_EXECREAL_W}`;

// ── Production Ops (monitoring + health) ─────────────────────────────────────
const HEALTH_SCRIPT      = join(ROOT, 'scripts', 'python', 'health_monitor.py');
const HEALTH_LOG         = join(ROOT, 'logs', 'health_monitor.log');
const FUNNEL_SCRIPT      = join(ROOT, 'scripts', 'egx_signal_funnel.mjs');
const FUNNEL_LOG         = join(ROOT, 'logs', 'funnel.log');
const DEPS_SCRIPT        = join(ROOT, 'scripts', 'egx_deps_check.mjs');
const DEPS_LOG           = join(ROOT, 'logs', 'deps_check.log');
const NIGHT_SCRIPT       = join(ROOT, 'scripts', 'python', 'night_lab.py');
const NIGHT_LOG          = join(ROOT, 'logs', 'night_lab.log');
const PROD_OPS_SCRIPT    = join(ROOT, 'scripts', 'egx_production_ops.mjs');
const PROD_STATUS_LOG    = join(ROOT, 'logs', 'prod_status.log');
const SESSION_READY_SCRIPT = join(ROOT, 'scripts', 'egx_session_ready.mjs');
const SESSION_READY_LOG  = join(ROOT, 'logs', 'session_ready.log');
const CRON_LOG_SCRIPT    = join(ROOT, 'scripts', 'egx_cron_log_check.mjs');
const CRON_LOG_LOG       = join(ROOT, 'logs', 'cron_log_check.log');
const PROD_READY_SCRIPT  = join(ROOT, 'scripts', 'egx_prod_ready.mjs');
const PROD_READY_LOG     = join(ROOT, 'logs', 'prod_ready.log');
const QUALITY_W_SCRIPT   = join(ROOT, 'scripts', 'egx_quality_weekly.mjs');
const QUALITY_W_LOG      = join(ROOT, 'logs', 'quality_weekly.log');
const LEARNING_SCRIPT    = join(ROOT, 'scripts', 'egx_learning_loop.mjs');
const LEARNING_LOG       = join(ROOT, 'logs', 'learning_loop.log');
const VERIFY_SCRIPT      = join(ROOT, 'scripts', 'egx_full_verify.mjs');
const VERIFY_LOG         = join(ROOT, 'logs', 'full_verify.log');
const POST_SESSION_SCRIPT= join(ROOT, 'scripts', 'egx_post_session_ops.mjs');
const POST_SESSION_LOG   = join(ROOT, 'logs', 'post_session.log');
const PYTHON_BIN         = 'python3';

// صحة النظام: 6:30 AM القاهرة (4:30 UTC) قبل السوق
const CRON_HEALTH_D      = `30 4 * * 0-4 cd "${ROOT}" && ${PYTHON_BIN} "${HEALTH_SCRIPT}" check >> "${HEALTH_LOG}" 2>&1 ${MARKER_HEALTH_D}`;
// قمع الإشارات: بعد daily مباشرة 5:05 PM القاهرة (15:05 UTC)
const CRON_FUNNEL_D      = `5 15 * * 0-4 cd "${ROOT}" && ${NODE_BIN} "${FUNNEL_SCRIPT}" >> "${FUNNEL_LOG}" 2>&1 ${MARKER_FUNNEL_D}`;
// لوحة الإنتاج: 7:00 AM القاهرة (5:00 UTC)
const CRON_PROD_STATUS_D = `0 5 * * 0-4 cd "${ROOT}" && ${NODE_BIN} "${PROD_OPS_SCRIPT}" status >> "${PROD_STATUS_LOG}" 2>&1 ${MARKER_PROD_STATUS_D}`;
// جاهزية الجلسة: 7:10 AM القاهرة (5:10 UTC) — upstream + cron + delivery
const CRON_SESSION_READY = `10 5 * * 0-4 cd "${ROOT}" && ${CRON_ENV} ${NODE_BIN} "${SESSION_READY_SCRIPT}" >> "${SESSION_READY_LOG}" 2>&1 ${MARKER_SESSION_READY}`;
// فحص سجلات cron للأعطال: 7:15 AM القاهرة (5:15 UTC)
const CRON_LOG_CHECK   = `15 5 * * 0-4 cd "${ROOT}" && ${NODE_BIN} "${CRON_LOG_SCRIPT}" --hours 48 >> "${CRON_LOG_LOG}" 2>&1 ${MARKER_CRON_LOG_CHK}`;
// تدقيق جودة بيانات عميق: كل أحد 6:30 AM القاهرة (4:30 UTC) — build_full (بطيء)
const CRON_QUALITY_W     = `30 4 * * 0 cd "${ROOT}" && ${CRON_ENV} ${locked('egx-quality-weekly', `${NODE_BIN} "${QUALITY_W_SCRIPT}"`)} >> "${QUALITY_W_LOG}" 2>&1 ${MARKER_QUALITY_W}`;
// حلقة التعلم المغلقة: كل أحد 6:40 AM القاهرة (4:40 UTC) — forensic + counterfactual
const CRON_LEARNING_W    = `40 4 * * 0 cd "${ROOT}" && ${CRON_ENV} ${NODE_BIN} "${LEARNING_SCRIPT}" >> "${LEARNING_LOG}" 2>&1 ${MARKER_LEARNING_W}`;
// فحص جاهزية إنتاج أسبوعي: كل أحد 6:45 AM القاهرة (4:45 UTC)
const CRON_PROD_READY_W  = `45 4 * * 0 cd "${ROOT}" && ${CRON_ENV} ${NODE_BIN} "${PROD_READY_SCRIPT}" --skip-cdp >> "${PROD_READY_LOG}" 2>&1 ${MARKER_PROD_READY_W}`;
// Full stack verify قبل السوق — هيكلي (بدون CDP/tests) 5:15 AM القاهرة = 3:15 UTC
const CRON_VERIFY_D      = `15 3 * * 0-4 cd "${ROOT}" && ${NODE_BIN} "${VERIFY_SCRIPT}" --skip-tests --skip-cdp >> "${VERIFY_LOG}" 2>&1 ${MARKER_VERIFY_D}`;
// Post-session safety net بعد Telegram — reconcile + verify 5:45 PM القاهرة = 15:45 UTC
const CRON_POST_SESSION  = `45 15 * * 0-4 cd "${ROOT}" && ${locked('egx-post-session', `${NODE_BIN} "${POST_SESSION_SCRIPT}"`)} >> "${POST_SESSION_LOG}" 2>&1 ${MARKER_POST_SESSION}`;
// فحص التبعيات: كل أحد 6:00 AM (4:00 UTC)
const CRON_DEPS_W        = `0 4 * * 0 cd "${ROOT}" && ${NODE_BIN} "${DEPS_SCRIPT}" >> "${DEPS_LOG}" 2>&1 ${MARKER_DEPS_W}`;
// صحة نماذج ML: كل أحد 8:00 AM (6:00 UTC)
const CRON_TRAIN_W       = `0 6 * * 0 cd "${ROOT}" && ${PYTHON_BIN} scripts/python/egx_ml_trainer.py phase58 >> "${join(ROOT, 'logs', 'train_health.log')}" 2>&1 ${MARKER_TRAIN_W}`;
// Night Lab عميق: كل أحد 11:00 PM (21:00 UTC) — بعد RL
const CRON_NIGHT_W       = `0 21 * * 0 cd "${ROOT}" && ${PYTHON_BIN} "${NIGHT_SCRIPT}" weekly_deep >> "${NIGHT_LOG}" 2>&1 ${MARKER_NIGHT_W}`;
// ML-Advanced weekly (meta/purged/DSR-PBO/survival/embeddings/Thompson): كل جمعة 10:00 PM (20:00 UTC)
const CRON_MLADV_W       = `0 20 * * 5 cd "${ROOT}" && ${PYTHON_BIN} scripts/python/ml_advanced.py weekly >> "${join(ROOT, 'logs', 'ml_advanced.log')}" 2>&1 ${MARKER_MLADV_W}`;

const REMOVE = process.argv.includes('--remove');
const SHOW   = process.argv.includes('--show');

function getCurrentCron() {
  try {
    return execSync('crontab -l 2>/dev/null', { encoding: 'utf8' });
  } catch {
    return '';
  }
}

function setCron(content) {
  execSync(`echo ${JSON.stringify(content.trim())} | crontab -`);
}

if (SHOW) {
  const current = getCurrentCron();
  if (!current.trim()) {
    console.log('لا توجد مهام cron حالياً');
  } else {
    console.log('مهام cron الحالية:');
    console.log(current);
    const hasEgx        = current.includes(MARKER);
    const hasEvo        = current.includes(MARKER_EVO_Q);
    const hasCog        = current.includes(MARKER_COG_Q);
    const hasGraph      = current.includes(MARKER_GRAPH_W);
    const hasRL         = current.includes(MARKER_RL_W);
    const hasExplain    = current.includes(MARKER_EXPLAIN_Q);
    const hasMacroG     = current.includes(MARKER_MACRO_G);
    const hasIntegrity  = current.includes(MARKER_INTEGRITY);
    const hasUMCG       = current.includes(MARKER_UMCG_W);
    const hasCausal     = current.includes(MARKER_CAUSAL_W);
    const hasFailure    = current.includes(MARKER_FAILURE_D);
    const hasExplosion  = current.includes(MARKER_EXPLOSION_D);
    const hasDNA        = current.includes(MARKER_DNA_W);
    const hasResearch   = current.includes(MARKER_RESEARCH_W);
    const hasExecution  = current.includes(MARKER_EXECUTION_W);
    const hasSynthesis  = current.includes(MARKER_SYNTHESIS_D);
    const hasTg         = current.includes(MARKER_TG);
    const hasTvSyncLock = /egx-tv-sync/.test(current);
    const hasTgCron     = /egx_telegram_cron/.test(current);
    const hasVerify     = current.includes(MARKER_VERIFY_D);
    const hasSessionReady = current.includes(MARKER_SESSION_READY);
    const hasCronLogChk = current.includes(MARKER_CRON_LOG_CHK);
    const hasPostSess   = current.includes(MARKER_POST_SESSION);
    console.log(hasEgx       ? '✅  EGX automation مثبّت'           : '❌  EGX automation غير مثبّت');
    console.log(hasTg        ? '✅  Telegram cron مثبّت'            : '❌  Telegram cron غير مثبّت');
    console.log(hasTvSyncLock ? '✅  TV sync lock (egx-tv-sync)'   : '❌  TV sync lock مفقود');
    console.log(hasTgCron    ? '✅  egx_telegram_cron.mjs'         : '❌  لا يزال egx_telegram_daily مباشرة');
    console.log(hasVerify    ? '✅  Pre-market verify cron'        : '❌  Pre-market verify مفقود');
    console.log(hasSessionReady ? '✅  Session ready cron'         : '❌  Session ready مفقود');
    console.log(hasCronLogChk ? '✅  Cron log check'              : '❌  Cron log check مفقود');
    console.log(hasPostSess  ? '✅  Post-session ops cron'         : '❌  Post-session ops مفقود');
    console.log(hasEvo       ? '✅  Phase 15 Evolution مثبّت'       : '❌  Phase 15 Evolution غير مثبّت');
    console.log(hasCog       ? '✅  Phase 16 Cognition مثبّت'       : '❌  Phase 16 Cognition غير مثبّت');
    console.log(hasGraph     ? '✅  Phase 17 Graph مثبّت'           : '❌  Phase 17 Graph غير مثبّت');
    console.log(hasRL        ? '✅  Phase 18 RL مثبّت'              : '❌  Phase 18 RL غير مثبّت');
    console.log(hasExplain   ? '✅  Phase 19 Explain مثبّت'         : '❌  Phase 19 Explain غير مثبّت');
    console.log(hasMacroG    ? '✅  Global Macro Fetch مثبّت'       : '❌  Global Macro Fetch غير مثبّت');
    console.log(hasIntegrity ? '✅  Phase 20 Integrity مثبّت'       : '❌  Phase 20 Integrity غير مثبّت');
    console.log(hasUMCG      ? '✅  Phase 21 UMCG مثبّت'            : '❌  Phase 21 UMCG غير مثبّت');
    console.log(hasCausal    ? '✅  Phase 22 Causal Discovery مثبّت': '❌  Phase 22 Causal Discovery غير مثبّت');
    console.log(hasFailure   ? '✅  Phase 23 Failure Memory مثبّت'  : '❌  Phase 23 Failure Memory غير مثبّت');
    console.log(hasExplosion ? '✅  Phase 24 Explosion Physics مثبّت': '❌  Phase 24 Explosion Physics غير مثبّت');
    console.log(hasDNA       ? '✅  Phase 25 Market DNA مثبّت'      : '❌  Phase 25 Market DNA غير مثبّت');
    console.log(hasResearch  ? '✅  Phase 26 Research Loop مثبّت'   : '❌  Phase 26 Research Loop غير مثبّت');
    console.log(hasExecution ? '✅  Phase 27 Execution Reality مثبّت': '❌  Phase 27 Execution Reality غير مثبّت');
    console.log(hasSynthesis ? '✅  Phase 28 Daily Synthesis مثبّت' : '❌  Phase 28 Daily Synthesis غير مثبّت');
  }
  process.exit(0);
}

if (REMOVE) {
  const current = getCurrentCron();
  const ALL_MARKERS = [MARKER, MARKER_MACRO, MARKER_TG, MARKER_DMIDS, MARKER_DHVD, MARKER_EVO_Q, MARKER_EVO_F, MARKER_COG_Q, MARKER_COG_F, MARKER_GRAPH_W, MARKER_RL_W, MARKER_EXPLAIN_Q, MARKER_MACRO_G, MARKER_TV_LIVE_1, MARKER_TV_LIVE_2, MARKER_INTEGRITY, MARKER_UMCG_W, MARKER_CAUSAL_W, MARKER_FAILURE_D, MARKER_EXPLOSION_D, MARKER_DNA_W, MARKER_RESEARCH_W, MARKER_EXECUTION_W, MARKER_SYNTHESIS_D, MARKER_PRIORITIZE_D, MARKER_MEMORY_W, MARKER_META_W, MARKER_PORTFOLIO_D, MARKER_TRANSITION_D, MARKER_ANTILAWS_D, MARKER_STAT_W, MARKER_ARBITRATE_D, MARKER_OBSERVATORY_D, MARKER_COMPRESSION_D, MARKER_UNCERTAINTY_W, MARKER_SANDBOX_W, MARKER_GOVERNANCE_D, MARKER_COGBUS_D, MARKER_PRESSURE_W, MARKER_EXECREAL_W, MARKER_HEALTH_D, MARKER_FUNNEL_D, MARKER_DEPS_W, MARKER_TRAIN_W, MARKER_NIGHT_W, MARKER_PROD_STATUS_D, MARKER_SESSION_READY, MARKER_CRON_LOG_CHK, MARKER_QUALITY_W, MARKER_LEARNING_W, MARKER_PROD_READY_W, MARKER_VERIFY_D, MARKER_POST_SESSION, MARKER_MLADV_W];
  const filtered = current.split('\n').filter(l => !ALL_MARKERS.some(m => l.includes(m))).join('\n').trim();
  if (!ALL_MARKERS.some(m => current.includes(m))) {
    console.log('❌  لا يوجد EGX cron لحذفه');
    process.exit(0);
  }
  setCron(filtered || '');
  console.log('✅  EGX automation cron حُذف');
  process.exit(0);
}

// تثبيت
const current = getCurrentCron();
if (current.includes(MARKER)) {
  console.log('⚠️  EGX automation مثبّت مسبقاً — يُزال أولاً ثم يُضاف');
}

const ALL_MARKERS = [MARKER, MARKER_MACRO, MARKER_TG, MARKER_DMIDS, MARKER_DHVD, MARKER_EVO_Q, MARKER_EVO_F, MARKER_COG_Q, MARKER_COG_F, MARKER_GRAPH_W, MARKER_RL_W, MARKER_EXPLAIN_Q, MARKER_MACRO_G, MARKER_TV_LIVE_1, MARKER_TV_LIVE_2, MARKER_INTEGRITY, MARKER_UMCG_W, MARKER_CAUSAL_W, MARKER_FAILURE_D, MARKER_EXPLOSION_D, MARKER_DNA_W, MARKER_RESEARCH_W, MARKER_EXECUTION_W, MARKER_SYNTHESIS_D, MARKER_PRIORITIZE_D, MARKER_MEMORY_W, MARKER_META_W, MARKER_PORTFOLIO_D, MARKER_TRANSITION_D, MARKER_ANTILAWS_D, MARKER_STAT_W, MARKER_ARBITRATE_D, MARKER_OBSERVATORY_D, MARKER_COMPRESSION_D, MARKER_UNCERTAINTY_W, MARKER_SANDBOX_W, MARKER_GOVERNANCE_D, MARKER_COGBUS_D, MARKER_PRESSURE_W, MARKER_EXECREAL_W, MARKER_HEALTH_D, MARKER_FUNNEL_D, MARKER_DEPS_W, MARKER_TRAIN_W, MARKER_NIGHT_W, MARKER_PROD_STATUS_D, MARKER_SESSION_READY, MARKER_CRON_LOG_CHK, MARKER_QUALITY_W, MARKER_LEARNING_W, MARKER_PROD_READY_W, MARKER_VERIFY_D, MARKER_POST_SESSION, MARKER_MLADV_W];
const filtered = current.split('\n')
  .filter(l => !ALL_MARKERS.some(m => l.includes(m)))
  .join('\n').trim();
const ALL_CRONS = [
  CRON_LINE, CRON_MACRO, CRON_TG, CRON_TV_LIVE_1, CRON_TV_LIVE_2, CRON_DMIDS, CRON_DHVD,
  CRON_EVO_Q, CRON_EVO_F, CRON_COG_Q, CRON_COG_F,
  CRON_GRAPH_W, CRON_RL_W, CRON_EXPLAIN_Q, CRON_MACRO_G,
  CRON_INTEGRITY_W, CRON_UMCG_W, CRON_CAUSAL_W, CRON_FAILURE_D,
  CRON_EXPLOSION_D, CRON_DNA_W, CRON_RESEARCH_W, CRON_EXECUTION_W, CRON_SYNTHESIS_D,
  CRON_TRANSITION_D, CRON_PRIORITIZE_D, CRON_PORTFOLIO_D,
  CRON_MEMORY_W, CRON_META_W,
  CRON_ANTILAWS_D, CRON_STAT_W, CRON_ARBITRATE_D,
  CRON_OBSERVATORY_D, CRON_COMPRESSION_D, CRON_UNCERTAINTY_W, CRON_SANDBOX_W,
  CRON_GOVERNANCE_D, CRON_COGBUS_D, CRON_PRESSURE_W, CRON_EXECREAL_W,
  CRON_HEALTH_D, CRON_FUNNEL_D, CRON_PROD_STATUS_D, CRON_SESSION_READY, CRON_LOG_CHECK, CRON_QUALITY_W, CRON_LEARNING_W, CRON_PROD_READY_W, CRON_VERIFY_D, CRON_POST_SESSION, CRON_DEPS_W, CRON_TRAIN_W, CRON_NIGHT_W, CRON_MLADV_W
].join('\n');
const newCron  = filtered ? `${filtered}\n${ALL_CRONS}` : ALL_CRONS;

setCron(newCron);

console.log('✅  EGX Automation مثبّت بنجاح (53 مهمة):');
console.log(`   📅 Daily TV:    ${CRON_TIME}      (الأحد-الخميس 4:30 PM) → egx_tv_auto_update.mjs --launch --pine --tech`);
console.log(`   🏛️  Macro-EGX:   0 7 * * 0         (الأحد 9 ص)           → fetch_economics.mjs`);
console.log(`   🌍 Macro-Global: 30 4 * * 0         (الأحد 6:30 ص)        → fetch_global_macro.py`);
console.log(`   📲 Telegram:    20 15 * * 0-4      (الأحد-الخميس 5:20 PM) → egx_telegram_cron.mjs`);
console.log(`   ✅ Verify AM:    15 3 * * 0-4       (الأحد-الخميس 5:15 AM) → egx_full_verify.mjs`);
console.log(`   ✅ Session ready: 10 5 * * 0-4      (الأحد-الخميس 7:10 AM) → egx_session_ready.mjs`);
console.log(`   ✅ Cron log scan: 15 5 * * 0-4      (الأحد-الخميس 7:15 AM) → egx_cron_log_check.mjs`);
console.log(`   ✅ Post-session: 45 15 * * 0-4      (الأحد-الخميس 5:45 PM) → egx_post_session_ops.mjs`);
console.log(`   👁️  TV Live 1:   30 10 * * 0-4       (منتصف الجلسة)         → fetch_intraday_live.mjs`);
console.log(`   👁️  TV Live 2:   15 13 * * 0-4       (قرب الإغلاق)          → fetch_intraday_live.mjs`);
console.log(`   🔬 DMIDS:       0 18 * * 0          (الأحد 8 PM)          → egx_discover.mjs (log only)`);
console.log(`   🧪 DHVD:        0 17 1-7 * 0       (أول أحد شهرياً)      → egx_dhvd.mjs (log only)`);
console.log(`   🧠 Evo-Quick:   50 14 * * 0-4      (الأحد-الخميس 4:50 PM) → egx_evolution.mjs --quick`);
console.log(`   🧬 Evo-Full:    0 19 * * 0          (الأحد 9 PM)          → egx_evolution.mjs`);
console.log(`   🤖 Cog-Quick:   45 15 * * 0-4      (الأحد-الخميس 5:45 PM) → egx_cognition.mjs --quick`);
console.log(`   🧠 Cog-Full:    0 20 * * 0          (الأحد 10 PM)         → egx_cognition.mjs`);
console.log(`   🕸️  Graph:       30 20 * * 0         (الأحد 10:30 PM)      → egx_graph.mjs --section full`);
console.log(`   🤖 RL:          0 21 * * 0           (الأحد 11 PM)         → egx_rl.mjs --section walkforward`);
console.log(`   🔬 Explain:     5 15 * * 0-4        (الأحد-الخميس 5:05 PM) → egx_explain.mjs --section daily`);
console.log(`   🔍 Integrity:   30 5 * * 0           (الأحد 7:30 ص)        → egx_integrity.mjs full`);
console.log(`   🕸️  UMCG:        30 21 * * 0          (الأحد 11:30 PM)      → egx_umcg.mjs snapshot`);
console.log(`   🔗 Causal:      0 22 * * 0            (الأحد 12 AM)         → egx_causal_disc.mjs full`);
console.log(`   🧠 Failure:     0 13 * * 0-4          (الأحد-الخميس 3 PM)  → egx_failure.mjs scan`);
console.log(`   💥 Explosion:   15 14 * * 0-4         (الأحد-الخميس 4:15 PM) → egx_explosion.mjs readiness`);
console.log(`   🧬 DNA:         30 19 * * 0           (الأحد 9:30 PM)       → egx_dna.mjs full`);
console.log(`   🔬 Research:    30 22 * * 0           (الأحد 12:30 AM)      → egx_research.mjs evolve`);
console.log(`   ⚖️  Execution:   15 7 * * 0            (الأحد 9:15 ص)       → egx_execution.mjs liquidity`);
console.log(`   👑 Synthesis:   55 15 * * 0-4         (الأحد-الخميس 5:55 PM) → egx_synthesis.mjs run`);
console.log(`   Logs: ${LOG_FILE} | ${COG_LOG} | ${GRAPH_LOG} | ${RL_LOG} | ${EXPLAIN_LOG}`);
console.log('');
console.log('للتحقق: crontab -l');
console.log('للحذف:  npm run egx:cron:install -- --remove');
