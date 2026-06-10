#!/usr/bin/env node
/**
 * Phase 57 — Alert Automation runner
 * "تنبيهات ذكية تلقائية من نتائج المسح"
 *
 * Sections: targets | list | sync | clear | summary | full
 *   --date 2026-05-15
 *   --min-score 65
 */
import { pythonAlertGetTargets, pythonAlertLogCreated, pythonAlertListActive,
         pythonAlertSyncStatus, pythonAlertClearExpired, pythonAlertSummary,
         pythonAlertBuildFull }
  from '../src/egx/index.js';

const args       = process.argv.slice(2);
const section    = args.find(a => !a.startsWith('--')) ?? 'summary';
const dateIdx    = args.indexOf('--date');
const date       = dateIdx !== -1 ? args[dateIdx + 1] : new Date().toISOString().split('T')[0];
const msIdx      = args.indexOf('--min-score');
const minScore   = msIdx !== -1 ? parseFloat(args[msIdx + 1]) : 60;

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  🔔 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

const TYPE_EMOJI = {
  price_breakout:    '🚀',
  price_target:      '🎯',
  stop_loss:         '🛑',
  ma_cross:          '📈',
  rsi_oversold:      '💎',
  rsi_overbought:    '⚡',
  volume_spike:      '📊',
};

switch (section) {
  case 'targets': {
    banner(`Alert Targets — ${date} (min_score=${minScore})`);
    const r = await pythonAlertGetTargets({ scan_date: date, min_score: minScore });
    const targets = r?.targets ?? [];
    if (!targets.length) {
      console.log(`\n   No alert targets found for ${date}. Run egx:scan first.`);
      break;
    }
    console.log(`\n   ${targets.length} alert targets generated:\n`);
    console.log('   Symbol    Type               Price     Condition');
    console.log('   ' + '─'.repeat(60));
    targets.slice(0, 25).forEach(t => {
      const em = TYPE_EMOJI[t.alert_type] ?? '🔔';
      const price = t.price_level != null ? t.price_level?.toFixed(2) : 'n/a';
      console.log(`   ${em} ${String(t.symbol).padEnd(8)} ${String(t.alert_type).padEnd(18)} ${String(price).padStart(8)}   ${t.condition ?? ''}`);
    });
    if (r?.n_targets > 25) console.log(`\n   ... and ${r.n_targets - 25} more`);
    if (r?.ready_for_tv?.length) {
      console.log(`\n   ✅ Ready for TradingView: ${r.ready_for_tv.slice(0, 10).join(', ')}`);
    }
    break;
  }
  case 'list': {
    banner('Active Alerts');
    const r = await pythonAlertListActive({ include_expired: false });
    const alerts = r?.alerts ?? [];
    if (!alerts.length) {
      console.log('\n   No active alerts in log.');
      break;
    }
    console.log(`\n   ${alerts.length} active alert(s):\n`);
    console.log('   Symbol    Type               Price     Created         Status');
    console.log('   ' + '─'.repeat(70));
    alerts.slice(0, 30).forEach(a => {
      const em = TYPE_EMOJI[a.alert_type] ?? '🔔';
      const price = a.price_level != null ? a.price_level?.toFixed(2) : 'n/a';
      const created = a.created_at?.substring(0, 10) ?? '?';
      const status = a.status ?? 'active';
      const statEm = status === 'triggered' ? '✅' : status === 'expired' ? '⏰' : '🟡';
      console.log(`   ${em} ${String(a.symbol).padEnd(8)} ${String(a.alert_type).padEnd(18)} ${String(price).padStart(8)}   ${created}   ${statEm} ${status}`);
    });
    if (r?.by_type) {
      console.log('\n   By type:');
      Object.entries(r.by_type).forEach(([t, n]) => console.log(`     ${TYPE_EMOJI[t] ?? '🔔'} ${t}: ${n}`));
    }
    break;
  }
  case 'sync': {
    banner('Sync Alert Status');
    const r = await pythonAlertSyncStatus({ triggered_ids: [], expired_ids: [] });
    console.log(`\n   Sync result:`);
    console.log(`   Updated:   ${r?.n_updated ?? 0}`);
    console.log(`   Triggered: ${r?.n_triggered ?? 0}`);
    console.log(`   Expired:   ${r?.n_expired ?? 0}`);
    if (r?.updated?.length) {
      console.log(`\n   Recently changed: ${r.updated.map(a => a.symbol).join(', ')}`);
    }
    break;
  }
  case 'clear': {
    banner('Clear Expired Alerts');
    const r = await pythonAlertClearExpired({ older_than_days: 7 });
    console.log(`\n   Cleared ${r?.n_cleared ?? 0} expired alert(s).`);
    console.log(`   Remaining active: ${r?.n_remaining ?? 'n/a'}`);
    break;
  }
  case 'summary': {
    banner('Alert Summary');
    const r = await pythonAlertSummary({});
    if (r?.total_created !== undefined || r?.total_created_30d !== undefined || r?.n_active !== undefined) {
      const total = r.total_created ?? r.total_created_30d ?? 0;
      console.log(`\n   Total created:   ${total}`);
      console.log(`   Active now:      ${r.n_active ?? 0}`);
      console.log(`   Triggered:       ${r.n_triggered ?? 0}`);
      console.log(`   Expired:         ${r.n_expired ?? 0}`);
      const hitRate = r.hit_rate ?? (r.n_triggered && total ? r.n_triggered / total : null);
      console.log(`   Hit rate:        ${hitRate != null ? (hitRate*100)?.toFixed(1)+'%' : 'n/a'}`);
      const topSyms = r.top_symbols?.map?.(s => typeof s === 'string' ? s : s.symbol) ?? [];
      if (topSyms.length) console.log(`\n   Most alerted:    ${topSyms.slice(0, 8).join(', ')}`);
      if (r.alert_type_distribution) {
        console.log('\n   By type:');
        Object.entries(r.alert_type_distribution).forEach(([t, n]) =>
          console.log(`     ${TYPE_EMOJI[t.toLowerCase()] ?? '🔔'} ${t}: ${n}`));
      }
      if (r.recent?.length) {
        console.log(`\n   Recent alerts:`);
        r.recent.slice(0, 5).forEach(a => {
          const em = TYPE_EMOJI[a.alert_type?.toLowerCase()] ?? '🔔';
          console.log(`     ${em} ${a.symbol}  ${a.alert_type}  @${a.price_level?.toFixed(2) ?? 'n/a'}  [${a.status ?? 'active'}]`);
        });
      }
    } else pp(r);
    break;
  }
  case 'full': {
    banner(`Alert Full Report — ${date}`);
    const r = await pythonAlertBuildFull({ scan_date: date, min_score: minScore });
    if (r?.targets_generated !== undefined) {
      console.log(`\n   Targets generated: ${r.targets_generated}`);
      console.log(`   Already active:    ${r.already_active}`);
      console.log(`   New to create:     ${r.new_to_create}`);
      if (r?.top_targets?.length) {
        console.log(`\n   Top alert targets:`);
        console.log('   Symbol    Type               Price     Score');
        console.log('   ' + '─'.repeat(55));
        r.top_targets.slice(0, 10).forEach(t => {
          const em = TYPE_EMOJI[t.alert_type] ?? '🔔';
          console.log(`   ${em} ${String(t.symbol).padEnd(8)} ${String(t.alert_type).padEnd(18)} ${String(t.price_level?.toFixed(2) ?? 'n/a').padStart(8)}   ${t.scan_score?.toFixed(0) ?? '?'}`);
        });
      }
      if (r?.tv_commands?.length) {
        console.log(`\n   TradingView commands ready: ${r.tv_commands.length}`);
        console.log('   Run: npm run egx:fetch:alerts to create them in TradingView');
      }
    } else pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}. Use: targets|list|sync|clear|summary|full`); process.exit(1);
}
