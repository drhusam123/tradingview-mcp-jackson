#!/usr/bin/env node
/**
 * Phase 57 — Alert Automation Fetcher
 * Creates/deletes TradingView alerts based on final client-approved signals.
 *
 * Options:
 *   --date 2026-05-15    use final signals from this date
 *   --min-score 65       minimum final score for alert creation
 *   --max-picks 10       max number of picks to alert on
 *   --sync               sync/expire old alerts only (no new creation)
 *   --clear              clear all expired alerts
 *   --list               list active alerts only
 *   --live               create alerts in TradingView; default is dry-run
 */
import { pythonAlertGetTargets, pythonAlertLogCreated, pythonAlertSyncStatus,
         pythonAlertListActive, pythonAlertClearExpired, pythonAlertBuildFull }
  from '../src/egx/index.js';
import { callMCPTool } from '../src/egx/tv_bridge.js';
import { toTvSymbol } from '../src/egx/tv_symbols.js';

const args     = process.argv.slice(2);
const getArg = (name, fallback = null) => {
  const i = args.indexOf(name);
  return i >= 0 && args[i + 1] && !args[i + 1].startsWith('--') ? args[i + 1] : fallback;
};
const date     = getArg('--date', new Date().toISOString().split('T')[0]);
const minScore = parseFloat(getArg('--min-score', '65'));
const maxPicks = parseInt(getArg('--max-picks', '10'));
const doSync   = args.includes('--sync');
const doClear  = args.includes('--clear');
const doList   = args.includes('--list');
const doLive   = args.includes('--live');

function log(msg) { console.log(`[alerts] ${msg}`); }

// ── List mode ───────────────────────────────────────────────────────────────
if (doList) {
  const r = await pythonAlertListActive({});
  log(`Active alerts: ${r.n_active ?? 0}`);
  if (r.alerts?.length) {
    console.log('\n   Symbol     Type          Price    Expires');
    console.log('   ' + '─'.repeat(55));
    r.alerts.forEach(a =>
      console.log(`   ${String(a.symbol).padEnd(10)} ${String(a.alert_type).padEnd(14)} ${String(a.price_level?.toFixed(2)).padStart(7)}   ${a.expires_at ?? '?'}`));
  }
  process.exit(0);
}

// ── Clear expired ────────────────────────────────────────────────────────────
if (doClear) {
  const s = await pythonAlertSyncStatus({ today: date });
  log(`Synced: ${s.n_expired ?? 0} expired, ${s.n_still_active ?? 0} still active`);
  const c = await pythonAlertClearExpired({ before_date: date });
  log(`Cleared: ${c.n_deleted ?? 0} old records`);
  process.exit(0);
}

// ── Sync only ────────────────────────────────────────────────────────────────
if (doSync) {
  const s = await pythonAlertSyncStatus({ today: date });
  log(`✅ Synced: ${s.n_expired ?? 0} expired, ${s.n_still_active ?? 0} active`);
  process.exit(0);
}

// ── Main: create new alerts ──────────────────────────────────────────────────
log(`Getting final actionable alert targets for ${date} (min score: ${minScore})...`);
const full = await pythonAlertBuildFull({ scan_date: date, min_score: minScore });

if (!full?.targets_to_create?.length) {
  log(`No final actionable signals found for ${date}. Alerts are blocked until final_signals has actionable=1.`);
  log(`Currently active alerts: ${full?.current_active ?? 0}`);
  process.exit(0);
}

log(`Found ${full.targets_to_create.length} alerts to create across ${maxPicks} picks`);

let tvAvailable = false;
if (doLive) {
  const health = await callMCPTool('tv_health_check', {});
  tvAvailable = !!health?.success;
  if (!tvAvailable) {
    log(`TradingView is not connected: ${health?.error ?? 'unknown error'}`);
    process.exit(1);
  }
}

// Create alerts
const created = [];
const previewed = [];
const skipped = [];

for (const target of full.targets_to_create.slice(0, maxPicks * 5)) {
  try {
    if (!doLive) {
      log(`  [DRY-RUN] ${target.symbol} ${target.alert_type} @ ${target.price_level?.toFixed(2)} (${target.condition})`);
      previewed.push(target);
      continue;
    }

    const tvSymbol = toTvSymbol(target.symbol);
    await callMCPTool('chart_set_symbol', { symbol: tvSymbol });
    const message = [
      target.tv_alert_name,
      target.symbol,
      target.alert_type,
      target.condition,
      target.notes,
    ].filter(Boolean).join(' | ');

    const res = await callMCPTool('alert_create', {
      condition: target.condition,
      price: target.price_level,
      message,
    });

    if (res?.success) {
      log(`  ✅ ${target.tv_alert_name}  ${target.symbol} ${target.alert_type} @ ${target.price_level?.toFixed(2)}`);
      created.push(target);
    } else {
      throw new Error(res?.error || 'alert_create returned false');
    }
  } catch (e) {
    log(`  ⚠️  Failed ${target.symbol}: ${e.message}`);
    skipped.push(target);
  }
}

// Log created alerts to DB
if (created.length) {
  const logged = await pythonAlertLogCreated({ alerts: created });
  log(`\n✅ Logged ${logged.n_logged ?? created.length} alerts to DB`);
  log(`   Use TradingView UI or egx:alerts runner to view/manage`);
} else if (previewed.length) {
  log(`\nDry-run only: previewed ${previewed.length} alerts; nothing was created or logged.`);
}

log(`\nSummary: ${created.length} created, ${previewed.length} previewed, ${skipped.length} skipped`);
log(`Active alerts now: ${(full.current_active ?? 0) + created.length}`);
