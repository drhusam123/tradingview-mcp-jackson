#!/usr/bin/env node
/**
 * Verify EGX production automation (cron, env, locks, notify path).
 * Usage: node scripts/egx_automation_verify.mjs [--ci]
 *   --ci  structural checks only (no crontab/Telegram/machine deps) — for GitHub Actions
 */
import { execSync } from 'child_process';
import { existsSync, readFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { isTelegramConfigured } from '../src/egx/notify.js';

loadEnv();

const CI_MODE = process.argv.includes('--ci');

const checks = [];
function ok(name, pass, detail = '') {
  checks.push({ name, pass, detail });
  console.log(`${pass ? '✅' : '❌'} ${name}${detail ? `: ${detail}` : ''}`);
}

let cron = '';
try {
  cron = execSync('crontab -l 2>/dev/null', { encoding: 'utf8' });
} catch {
  cron = '';
}

if (CI_MODE) {
  ok('CI structural mode', true, 'skipping crontab/Telegram/machine deps');
} else {
  ok('crontab installed', cron.includes('EGX-DAILY-AUTOMATION'), cron ? 'found markers' : 'empty');
  ok('TV sync lock egx-tv-sync', /egx-tv-sync.*egx_tv_auto_update/.test(cron));
  ok('Telegram cron egx-telegram', /egx-telegram.*egx_telegram_cron/.test(cron));
  ok('Telegram cron PYTHON_BIN set', /PYTHON_BIN=[^\s]+.*egx-telegram/.test(cron));
  ok('Telegram NOT sharing egx-daily lock', !/egx-daily.*egx_telegram_daily/.test(cron));
  ok('.env exists', existsSync(join(PROJECT_ROOT, '.env')));
  ok('TELEGRAM_BOT_TOKEN', Boolean(process.env.TELEGRAM_BOT_TOKEN));
  ok('TELEGRAM_CHAT_ID', Boolean(process.env.TELEGRAM_CHAT_ID));
  ok('Telegram configured', isTelegramConfigured());
  ok('PYTHON_BIN', Boolean(process.env.PYTHON_BIN || process.env.PYTHON3), process.env.PYTHON_BIN || process.env.PYTHON3 || 'missing');
}

const scripts = [
  'scripts/egx_telegram_cron.mjs',
  'scripts/egx_prod_prepare_send.mjs',
  'scripts/egx_decision_bot.mjs',
  'scripts/egx_export_trades_csv.mjs',
  'scripts/egx_notify_reconcile.mjs',
  'scripts/egx_runbook.mjs',
  'scripts/egx_session_ready.mjs',
  'scripts/egx_pre_session.mjs',
  'scripts/egx_ml_boost.mjs',
  'scripts/egx_ml_gate_pipeline_verify.mjs',
  'scripts/egx_signal_funnel.mjs',
  'scripts/python/gate_actionable_simulate.py',
  'scripts/python/signal_integration.py',
  'scripts/egx_cron_log_check.mjs',
  'scripts/egx_automation_status.mjs',
  'scripts/egx_prod_ready.mjs',
  'scripts/lib/pre_send_check.mjs',
  'scripts/lib/client_message_prep.mjs',
  'scripts/lib/run_quant_discovery.mjs',
  'scripts/egx_client_message_audit.mjs',
  'scripts/egx_notify_backfill.mjs',
  'scripts/lib/delivery_audit.mjs',
  'scripts/lib/egx_safety_check.mjs',
  'scripts/lib/ops_digest.mjs',
  'scripts/lib/data_quality_gate.mjs',
  'scripts/lib/proof_loop.mjs',
  'scripts/lib/counterfactual_safety.mjs',
  'scripts/egx_learning_loop.mjs',
  'scripts/egx_discovery_refresh.mjs',
  'scripts/egx_discovery_perpetual.mjs',
  'scripts/egx_discovery_promotion_audit.mjs',
  'scripts/egx_discovery_verify.mjs',
  'scripts/egx_discovery_fabric.mjs',
  'scripts/egx_discovery_automate.mjs',
  'scripts/egx_gap_repair.mjs',
  'scripts/egx_ohlcv_catchup.mjs',
  'scripts/egx_ohlcv_hygiene.mjs',
  'scripts/lib/ohlcv_hygiene.mjs',
  'scripts/egx_notify_data_correction.mjs',
  'tests/portfolio_import_dedup.test.py',
  'scripts/lib/final_signals_query.mjs',
  'scripts/python/discovery_fabric_merge.py',
  'scripts/python/discovery_backtest_gate.py',
  'scripts/python/discovery_manifest_loader.py',
  'scripts/migrations/004_discovery_fabric.sql',
  'scripts/tv_microstructure_engine.mjs',
  'scripts/python/tv_discovery_features.py',
  'scripts/python/counterfactual_atom_miner.py',
  'scripts/python/lre_4_0_research_feed.py',
  'scripts/python/lre_dual_gate_daily.py',
  'scripts/python/lre_3_6b_forward_shadow_pilot.py',
  'scripts/python/lre_4_0_integration_test.py',
  'scripts/python/lre_4_0_acceptance.py',
  'scripts/python/lre_4_0_status.py',
  'egx_rules.json',
];
for (const s of scripts) {
  ok(`script ${s}`, existsSync(join(PROJECT_ROOT, s)));
}

if (!CI_MODE) {
  const PROD_PY = '/usr/bin/python3';
  try {
    execSync(`"${PROD_PY}" -c "import numpy, lightgbm"`, { stdio: 'pipe' });
    ok('Python ML deps prod (/usr/bin/python3)', true);
  } catch {
    ok('Python ML deps prod (/usr/bin/python3)', false, 'pip install numpy lightgbm for system python3');
  }
}

const stampPath = join(PROJECT_ROOT, 'data/notification_prepare_stamp.json');
if (existsSync(stampPath)) {
  try {
    const stamp = JSON.parse(readFileSync(stampPath, 'utf8'));
    ok('prepare stamp file', true, `${stamp.signal_date} ok=${stamp.ok}`);
  } catch {
    ok('prepare stamp file', false, 'parse error');
  }
} else {
  ok('prepare stamp file', true, 'optional — created by prepare-send');
}
ok('EGX safety veto configured', process.env.EGX_SAFETY_VETO !== '0', process.env.EGX_SAFETY_VETO ?? 'default=1');
ok('egx_rules.json readable', existsSync(join(PROJECT_ROOT, 'egx_rules.json')));
ok('Full verify script', existsSync(join(PROJECT_ROOT, 'scripts/egx_full_verify.mjs')));
if (!CI_MODE) {
  ok('Cron pre-market verify', /EGX-FULL-VERIFY-DAILY/.test(cron));
  ok('Cron post-session ops', /EGX-POST-SESSION-DAILY/.test(cron));
  ok('Cron pre-session bundle', /EGX-PRE-SESSION-DAILY/.test(cron));
  ok('Cron signal funnel', /EGX-FUNNEL-DAILY/.test(cron));
  ok('Cron session ready', /EGX-SESSION-READY-DAILY/.test(cron));
  ok('Cron log check', /EGX-CRON-LOG-CHECK-DAILY/.test(cron));
  ok('Cron TV microstructure', /EGX-TV-MICRO-D/.test(cron));
  ok('Cron discovery perpetual', /EGX-DISCOVERY-PERPETUAL-W/.test(cron));
  ok('Cron discovery audit weekly', /EGX-DISCOVERY-AUDIT-W/.test(cron));
  ok('Cron DMIDS weekly rescore', /EGX-DMIDS-WEEKLY/.test(cron));
} else {
  ok('install_cron.mjs', existsSync(join(PROJECT_ROOT, 'scripts/install_cron.mjs')));
  const installCron = readFileSync(join(PROJECT_ROOT, 'scripts/install_cron.mjs'), 'utf8');
  ok('install_cron pre-session marker', installCron.includes('EGX-PRE-SESSION-DAILY'));
  ok('install_cron post-session marker', installCron.includes('EGX-POST-SESSION-DAILY'));
  ok('install_cron funnel marker', installCron.includes('EGX-FUNNEL-DAILY'));
}
ok('Post-session script', existsSync(join(PROJECT_ROOT, 'scripts/egx_post_session_ops.mjs')));
const pkg = JSON.parse(readFileSync(join(PROJECT_ROOT, 'package.json'), 'utf8'));
const npmScripts = pkg.scripts || {};
ok('npm egx:ml:boost', npmScripts['egx:ml:boost']?.includes('egx_ml_boost.mjs'));
ok('npm egx:ml:refresh', npmScripts['egx:ml:refresh']?.includes('--skip-ensemble'));
ok('npm egx:post:session', npmScripts['egx:post:session']?.includes('egx_post_session_ops.mjs'));
ok('npm egx:gate:simulate', npmScripts['egx:gate:simulate']?.includes('gate_actionable_simulate.py'));
ok('npm egx:ml:gate:verify', npmScripts['egx:ml:gate:verify']?.includes('egx_ml_gate_pipeline_verify.mjs'));
ok('npm egx:ml:gate:verify:ci', npmScripts['egx:ml:gate:verify:ci']?.includes('--ci'));
ok('npm egx:pre:session', npmScripts['egx:pre:session']?.includes('egx_pre_session.mjs'));
ok('npm egx:client:message:audit', npmScripts['egx:client:message:audit']?.includes('egx_client_message_audit.mjs'));
const tgCron = existsSync(join(PROJECT_ROOT, 'scripts/egx_telegram_cron.mjs'))
  ? readFileSync(join(PROJECT_ROOT, 'scripts/egx_telegram_cron.mjs'), 'utf8')
  : '';
ok('telegram cron prep flag', tgCron.includes('egx_telegram_daily.mjs --prep'));
const tvAuto = existsSync(join(PROJECT_ROOT, 'scripts/egx_tv_auto_update.mjs'))
  ? readFileSync(join(PROJECT_ROOT, 'scripts/egx_tv_auto_update.mjs'), 'utf8')
  : '';
ok('eod light fabric', tvAuto.includes('egx_discovery_fabric.mjs --light'));
ok('eod lre dual-gate daily', tvAuto.includes('lre_dual_gate_daily.py'));
ok('eod lre research feed', tvAuto.includes('lre_4_0_research_feed.py'));
ok('eod lre forward shadow', tvAuto.includes('lre_3_6b_forward_shadow_pilot.py'));
ok('eod prioritizer after opp', tvAuto.indexOf('intelligence_prioritizer.py prioritize') > tvAuto.indexOf('opportunity_score_v2.py'));
const reg = existsSync(join(PROJECT_ROOT, 'scripts/lib/discovery_engine_registry.mjs'))
  ? readFileSync(join(PROJECT_ROOT, 'scripts/lib/discovery_engine_registry.mjs'), 'utf8')
  : '';
ok('registry causal+xpro', reg.includes('causal_discovery') && reg.includes('egx_x_pro'));
ok('Recovery script', existsSync(join(PROJECT_ROOT, 'scripts/egx_notify_recovery.mjs')));
ok('EGX_ALERT_TELEGRAM', process.env.EGX_ALERT_TELEGRAM !== '0', process.env.EGX_ALERT_TELEGRAM ?? 'default=1');
ok('EGX_OPS_SUCCESS_ALERT', process.env.EGX_OPS_SUCCESS_ALERT !== '0', process.env.EGX_OPS_SUCCESS_ALERT ?? 'default=1');
ok('npm egx:phase9:graduation', npmScripts['egx:phase9:graduation']?.includes('egx_phase9_graduation.mjs'));
ok('script egx_phase9_graduation.mjs', existsSync(join(PROJECT_ROOT, 'scripts/egx_phase9_graduation.mjs')));
ok('npm egx:phase10:graduation', npmScripts['egx:phase10:graduation']?.includes('egx_phase10_graduation.mjs'));
ok('npm egx:phase11:promotion', npmScripts['egx:phase11:promotion']?.includes('egx_phase11_promotion.mjs'));
ok('script research_client_env.mjs', existsSync(join(PROJECT_ROOT, 'scripts/lib/research_client_env.mjs')));
ok('script mde_promotion_bridge.py', existsSync(join(PROJECT_ROOT, 'scripts/python/mde_promotion_bridge.py')));
ok('npm egx:phase12:bootstrap', npmScripts['egx:phase12:bootstrap']?.includes('egx_phase12_bootstrap.mjs'));
ok('npm egx:p6:historical-backfill', npmScripts['egx:p6:historical-backfill']?.includes('egx_p6_historical_backfill.mjs'));
ok('script p6_historical_proof.mjs', existsSync(join(PROJECT_ROOT, 'scripts/lib/p6_historical_proof.mjs')));
ok('bootstrap graduation mode', readFileSync(join(PROJECT_ROOT, 'scripts/lib/p6_historical_proof.mjs'), 'utf8').includes('historical_bootstrap'));
ok('eod dynamic research env', tvAuto.includes('research_client_env.mjs') && !tvAuto.includes('MED_CLIENT_SIGNAL=0 MED_OPP_BOOST=0 MED_FEED_BOOST=0 MED_POSITION_SIZING_LIVE=0'));
ok('script p6_graduation_gate.mjs', existsSync(join(PROJECT_ROOT, 'scripts/lib/p6_graduation_gate.mjs')));
ok('delivered seed helper', readFileSync(join(PROJECT_ROOT, 'scripts/lib/delivered_outcomes.mjs'), 'utf8').includes('seedDeliveredOutcomes'));
ok('npm egx:lre:research-feed', Boolean(npmScripts['egx:lre:research-feed']));
ok('npm egx:lre:acceptance', Boolean(npmScripts['egx:lre:acceptance']));
ok('npm egx:lre:status', Boolean(npmScripts['egx:lre:status']));
ok('npm egx:med:run', Boolean(npmScripts['egx:med:run']));
ok('npm egx:ohlcv:catchup', Boolean(npmScripts['egx:ohlcv:catchup']));
ok('npm egx:ohlcv:hygiene', Boolean(npmScripts['egx:ohlcv:hygiene']));
ok('npm egx:notify:correction', Boolean(npmScripts['egx:notify:correction']));
ok('npm egx:phase13:live-validation', npmScripts['egx:phase13:live-validation']?.includes('egx_phase13_live_validation.mjs'));
ok('script med_client_signal_shadow.py', existsSync(join(PROJECT_ROOT, 'scripts/python/med_client_signal_shadow.py')));
ok('script med_feed_ab_pilot.py', existsSync(join(PROJECT_ROOT, 'scripts/python/med_feed_ab_pilot.py')));
ok('script p6_live_kpi.mjs', existsSync(join(PROJECT_ROOT, 'scripts/lib/p6_live_kpi.mjs')));
ok('npm egx:phase14:graduation', npmScripts['egx:phase14:graduation']?.includes('egx_phase14_graduation.mjs'));
ok('npm egx:env:activate-phase14', npmScripts['egx:env:activate-phase14']?.includes('egx_env_activate_phase14.mjs'));
ok('script phase14_graduation.mjs', existsSync(join(PROJECT_ROOT, 'scripts/lib/phase14_graduation.mjs')));
ok('script med_client_signal_probe.py', existsSync(join(PROJECT_ROOT, 'scripts/python/med_client_signal_probe.py')));
ok('npm egx:phase15:client-beta', npmScripts['egx:phase15:client-beta']?.includes('egx_phase15_client_beta.mjs'));
ok('script client_beta_signoff.mjs', existsSync(join(PROJECT_ROOT, 'scripts/lib/client_beta_signoff.mjs')));
ok('script med_opp_delta_monitor.py', existsSync(join(PROJECT_ROOT, 'scripts/python/med_opp_delta_monitor.py')));
ok('eod med opp delta', tvAuto.includes('med_opp_delta_monitor.py'));
ok('ops digest client beta', readFileSync(join(PROJECT_ROOT, 'scripts/lib/ops_digest.mjs'), 'utf8').includes('loadClientBetaSignoffDigest'));
ok('npm egx:phase16:production-graduation', npmScripts['egx:phase16:production-graduation']?.includes('egx_phase16_production_graduation.mjs'));
ok('script production_graduation.mjs', existsSync(join(PROJECT_ROOT, 'scripts/lib/production_graduation.mjs')));
ok('ops digest production graduation', readFileSync(join(PROJECT_ROOT, 'scripts/lib/ops_digest.mjs'), 'utf8').includes('loadProductionGraduationDigest'));
ok('med ab historical backfill', readFileSync(join(PROJECT_ROOT, 'scripts/python/med_feed_ab_pilot.py'), 'utf8').includes('backfill_historical_dates'));
ok('npm egx:phase17:promotion-activation', npmScripts['egx:phase17:promotion-activation']?.includes('egx_phase17_promotion_activation.mjs'));
ok('npm egx:env:activate-phase17', npmScripts['egx:env:activate-phase17']?.includes('egx_env_activate_phase17.mjs'));
ok('script promotion_activation.mjs', existsSync(join(PROJECT_ROOT, 'scripts/lib/promotion_activation.mjs')));
ok('script med_live_delivery_correlation.py', existsSync(join(PROJECT_ROOT, 'scripts/python/med_live_delivery_correlation.py')));
ok('npm egx:phase18:live-ops', npmScripts['egx:phase18:live-ops']?.includes('egx_phase18_live_ops.mjs'));
ok('npm egx:env:activate-phase18', npmScripts['egx:env:activate-phase18']?.includes('egx_env_activate_phase18.mjs'));
ok('script phase18_live_ops.mjs', existsSync(join(PROJECT_ROOT, 'scripts/lib/phase18_live_ops.mjs')));
ok('script lre_oos_gate.mjs', existsSync(join(PROJECT_ROOT, 'scripts/lib/lre_oos_gate.mjs')));
ok('script p6_delivered_kpi_tracker.py', existsSync(join(PROJECT_ROOT, 'scripts/python/p6_delivered_kpi_tracker.py')));
ok('script weekly_prod_ready.mjs', existsSync(join(PROJECT_ROOT, 'scripts/lib/weekly_prod_ready.mjs')));
ok('npm egx:phase19:session-ops', npmScripts['egx:phase19:session-ops']?.includes('egx_phase19_session_ops.mjs'));
ok('npm egx:env:activate-phase19', npmScripts['egx:env:activate-phase19']?.includes('egx_env_activate_phase19.mjs'));
ok('script phase19_session_ops.mjs', existsSync(join(PROJECT_ROOT, 'scripts/lib/phase19_session_ops.mjs')));
ok('script post_graduation_session.mjs', existsSync(join(PROJECT_ROOT, 'scripts/lib/post_graduation_session.mjs')));
ok('script p6_t5_fill_orchestrator.py', existsSync(join(PROJECT_ROOT, 'scripts/python/p6_t5_fill_orchestrator.py')));
ok('script lre_oos_accumulator.py', existsSync(join(PROJECT_ROOT, 'scripts/python/lre_oos_accumulator.py')));
ok('npm egx:phase20:outcome-closure', npmScripts['egx:phase20:outcome-closure']?.includes('egx_phase20_outcome_closure.mjs'));
ok('npm egx:env:activate-phase20', npmScripts['egx:env:activate-phase20']?.includes('egx_env_activate_phase20.mjs'));
ok('script phase20_outcome_closure.mjs', existsSync(join(PROJECT_ROOT, 'scripts/lib/phase20_outcome_closure.mjs')));
ok('script live_session_day_gate.mjs', existsSync(join(PROJECT_ROOT, 'scripts/lib/live_session_day_gate.mjs')));
ok('script p6_watch_t5_closure.py', existsSync(join(PROJECT_ROOT, 'scripts/python/p6_watch_t5_closure.py')));
ok('npm egx:graduation:complete', npmScripts['egx:graduation:complete']?.includes('egx_graduation_final.mjs'));
ok('npm egx:phase21:live-anchor', npmScripts['egx:phase21:live-anchor']?.includes('egx_phase21_live_anchor.mjs'));
ok('npm egx:phase26:audit-close', npmScripts['egx:phase26:audit-close']?.includes('egx_phase26_audit_close.mjs'));
ok('script phase26_audit_close.mjs', existsSync(join(PROJECT_ROOT, 'scripts/lib/phase26_audit_close.mjs')));
ok('script p6_delivered_wr_dashboard.py', existsSync(join(PROJECT_ROOT, 'scripts/python/p6_delivered_wr_dashboard.py')));
ok('eod med daily chain', tvAuto.includes('med_0_3_daily_chain.py'));
ok('eod med client probe', tvAuto.includes('med_client_signal_probe.py'));
ok('registry med', reg.includes('med_daily_chain'));

ok('npm egx:health', npmScripts['egx:health']?.includes('system_health_check.py'));
ok('npm egx:full-cycle', npmScripts['egx:full-cycle']?.includes('egx_full_cycle.mjs'));
ok('script system_health_check.py', existsSync(join(PROJECT_ROOT, 'scripts/python/system_health_check.py')));
ok('script audit_db_report.py', existsSync(join(PROJECT_ROOT, 'scripts/python/audit_db_report.py')));
ok('script egx_system_audit_orchestrator.mjs', existsSync(join(PROJECT_ROOT, 'scripts/egx_system_audit_orchestrator.mjs')));
ok('npm egx:audit:all', npmScripts['egx:audit:all']?.includes('egx_system_audit_orchestrator.mjs'));
ok('audit DB_AUDIT template', existsSync(join(PROJECT_ROOT, 'scripts/python/audit_db_report.py')));
ok('docs automation runbook', existsSync(join(PROJECT_ROOT, 'docs/AUTOMATION_RUNBOOK.md')));
ok('audit ISSUES_REGISTER', existsSync(join(PROJECT_ROOT, 'audit/ISSUES_REGISTER.md')));
ok('audit FINAL_SYSTEM', existsSync(join(PROJECT_ROOT, 'audit/FINAL_SYSTEM_AUDIT_REPORT.md')));

const fail = checks.filter(c => !c.pass).length;
console.log(`\n=== Automation Verify: ${checks.length - fail}/${checks.length} PASS ===\n`);
process.exit(fail ? 1 : 0);
