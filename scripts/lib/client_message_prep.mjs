/**
 * Shared prep-mode params for client Telegram (next-session recommendations).
 */
import { nextTradingDay } from './egx_calendar.mjs';
import { runEgxSafetyCheck } from './egx_safety_check.mjs';

export const SAFETY_REASON_AR = {
  indicator_cache: 'بانتظار تحديث المؤشرات الفنية',
  repeat_ultra_loser: 'سجل خسائر فائقة مؤخراً على نفس السهم',
  max_signals_per_day: 'تجاوز حد التوصيات المؤهلة لليوم',
  volume_chase: 'حجم مبالغ فيه عن المتوسط — انتظر تصحيحاً',
  behavioral_volatile: 'سلوك متقلب — يحتاج تأكيد حجم واتجاه',
  explosive_ultra_thin_repeat: 'سيولة ضعيفة مع تكرار خسائر',
  max_open_positions: 'الحد الأقصى للمراكز المفتوحة',
};

export function watchReasonsFromSafety(safety) {
  const out = {};
  for (const d of safety?.decisions || []) {
    if (d.decision !== 'BLOCKED') continue;
    const key = (d.failed_conditions || [])[0] || 'safety';
    out[d.symbol] = SAFETY_REASON_AR[key]
      || (d.failed_conditions || []).join('، ')
      || 'لم يجتز بوابات الأمان بعد';
  }
  return out;
}

/**
 * Post-close client bulletins default to prep (next session). Set EGX_PREP_MODE=0 to disable.
 */
export function resolvePrepMode(argv = process.argv, env = process.env) {
  if (argv.includes('--no-prep')) return false;
  if (argv.includes('--prep')) return true;
  return env.EGX_PREP_MODE !== '0';
}

/**
 * @param {string} signalDate YYYY-MM-DD (last closed session / signal date)
 * @param {{ prep?: boolean }} opts
 */
export function buildClientFormatParams(signalDate, opts = {}) {
  const prep = opts.prep !== false && (opts.prep === true || resolvePrepMode());
  const params = { report_date: signalDate };
  if (!prep) return { params, prep: false, safety: null, target_session_date: null };

  const safety = runEgxSafetyCheck(signalDate, { veto: true });
  const targetSession = nextTradingDay(signalDate).next_trading_day || signalDate;
  params.prep_mode = true;
  params.target_session_date = targetSession;
  params.buy_symbols = safety.passed_symbols;
  params.watch_reasons = watchReasonsFromSafety(safety);
  return {
    params,
    prep: true,
    safety,
    target_session_date: targetSession,
  };
}
