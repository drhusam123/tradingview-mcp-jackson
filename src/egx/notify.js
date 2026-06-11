/**
 * EGX Notify — إرسال التقارير عبر Telegram
 * ==========================================
 * يستخدم Node.js v24 fetch المدمج (لا npm packages)
 * يدعم: رسائل نصية، ملفات نصية (reports)
 *
 * الإعداد:
 *   1. أنشئ بوت من @BotFather على Telegram
 *   2. أضف TELEGRAM_BOT_TOKEN و TELEGRAM_CHAT_ID في .env
 *   3. شغّل: node scripts/notify_test.mjs لاختبار الاتصال
 *
 * المالك: Dr. Husam | مايو 2026
 */

import { readFileSync, existsSync } from 'fs';
import { join, dirname }            from 'path';
import { fileURLToPath }            from 'url';
import Database                     from 'better-sqlite3';
import { tradingDayStaleness, freshnessReferenceDate } from '../../scripts/lib/egx_calendar.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));

// ── تحميل .env يدوياً (بدون dotenv) ──────────────────────────────────────────
function loadEnv() {
  const envPath = join(__dirname, '../../.env');
  if (!existsSync(envPath)) return;
  const lines = readFileSync(envPath, 'utf8').split('\n');
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const idx = trimmed.indexOf('=');
    if (idx < 0) continue;
    const key = trimmed.slice(0, idx).trim();
    const val = trimmed.slice(idx + 1).trim();
    if (key && val && !process.env[key]) {
      process.env[key] = val;
    }
  }
}

loadEnv();

// ── إعدادات ────────────────────────────────────────────────────────────────
const BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN ?? '';
const CHAT_ID   = process.env.TELEGRAM_CHAT_ID   ?? '';
const BASE_URL  = `https://api.telegram.org/bot${BOT_TOKEN}`;
const DB_PATH   = join(__dirname, '../../data/egx_trading.db');

function latestOhlcvDate() {
  try {
    if (!existsSync(DB_PATH)) return null;
    const db = new Database(DB_PATH, { readonly: true });
    const row = db.prepare("SELECT MAX(date(bar_time, 'unixepoch')) AS latest FROM ohlcv_history").get();
    db.close();
    return row?.latest ?? null;
  } catch {
    return null;
  }
}

function ohlcvFreshnessIssues() {
  const latest = latestOhlcvDate();
  if (!latest) return ['trusted OHLCV freshness is unavailable'];
  try {
    const cal = tradingDayStaleness(latest, freshnessReferenceDate());
    const stale = Number(cal.staleness_trading_days ?? 999);
    if (stale > 0) {
      return [
        `trusted OHLCV is stale by ${stale} trading session(s): latest=${latest}, last_td=${cal.last_trading_day}`,
      ];
    }
  } catch {
    const today = new Date().toISOString().slice(0, 10);
    if (latest < today) return [`trusted OHLCV is stale: ${latest} < ${today}`];
  }
  return [];
}

function upstreamAlignmentIssues(reportDate) {
  const issues = [];
  if (!existsSync(DB_PATH) || !reportDate) return ['upstream alignment unavailable'];
  try {
    const db = new Database(DB_PATH, { readonly: true });
    const maxScan = db.prepare('SELECT MAX(scan_date) AS d FROM scans').get()?.d ?? null;
    const maxPred = db.prepare('SELECT MAX(pred_date) AS d FROM explosion_predictions').get()?.d ?? null;
    const maxMeta = db.prepare('SELECT MAX(date) AS d FROM meta_label_scores').get()?.d ?? null;
    db.close();
    if (!maxScan || maxScan < reportDate) {
      issues.push(`scans not ready for ${reportDate} (latest=${maxScan ?? 'none'})`);
    }
    if (!maxPred || maxPred < reportDate) {
      issues.push(`ML predictions not ready for ${reportDate} (latest=${maxPred ?? 'none'})`);
    }
    if (!maxMeta || maxMeta < reportDate) {
      issues.push(`meta_label_scores not ready for ${reportDate} (latest=${maxMeta ?? 'none'})`);
    }
  } catch (e) {
    issues.push(`upstream alignment check failed: ${e.message}`);
  }
  return issues;
}

function finalActionableCount(reportDate) {
  try {
    if (!existsSync(DB_PATH) || !reportDate || String(reportDate).startsWith('2099-')) return 0;
    const db = new Database(DB_PATH, { readonly: true });
    const row = db.prepare(
      `SELECT COUNT(*) AS n
       FROM final_signals
       WHERE trade_date=? AND actionable=1 AND veto_reason IS NULL
         AND trade_date NOT LIKE '2099-%'`
    ).get(reportDate);
    db.close();
    return Number(row?.n || 0);
  } catch {
    return 0;
  }
}

export function validateTelegramPayload(text, opts = {}) {
  const body = String(text ?? '');
  const issues = [];
  const reportDate = opts.reportDate ?? new Date().toISOString().slice(0, 10);
  const finalCount = Number(opts.finalActionableCount ?? finalActionableCount(reportDate));

  if (!body.trim()) {
    issues.push('payload is empty');
  }
  if (/\b(undefined|null|NaN)\b/i.test(body)) {
    issues.push('payload contains undefined/null/NaN');
  }
  if (/0 stocks|No data|@ undefined|لا توجد بيانات/i.test(body)) {
    issues.push('payload looks like research/debug output, not client output');
  }
  if (opts.clientDelivery && finalCount <= 0) {
    const forbiddenWhenNoFinal = [
      /أفضل فرص التداول/,
      /فرص مؤهلة/,
      /منطقة الدخول/,
      /(?:^|\n)\s*الدخول:/,
      /رادار الانفجار/,
      /توقعات الانفجار/,
      /زخم ML/,
      /مرشح داعم/,
      /قائمة المراقبة/,
    ];
    for (const rx of forbiddenWhenNoFinal) {
      if (rx.test(body)) {
        issues.push(`no final_signals actionable=1 for ${reportDate}, but payload contains client opportunity language`);
        break;
      }
    }
  }
  if (opts.clientDelivery && !opts.backfillMode) {
    const dates = [...body.matchAll(/\b20\d{2}-\d{2}-\d{2}\b/g)].map(m => m[0]);
    const wrongDates = [...new Set(dates.filter(d => d !== reportDate))];
    if (wrongDates.length > 0) {
      issues.push(`payload contains non-report ISO date(s): ${wrongDates.slice(0, 5).join(', ')}`);
    }
  }
  if (opts.clientDelivery && !opts.backfillMode) {
    issues.push(...ohlcvFreshnessIssues());
    issues.push(...upstreamAlignmentIssues(reportDate));
  }
  if (opts.clientDelivery && opts.backfillMode) {
    issues.push(...upstreamAlignmentIssues(reportDate));
  }

  return issues.length ? { ok: false, issues } : { ok: true, warnings: [] };
}

// ── حالة الإعداد ────────────────────────────────────────────────────────────
export function isTelegramConfigured() {
  return Boolean(BOT_TOKEN && CHAT_ID);
}

/**
 * تشخيص حالة الإعداد بالتفصيل
 */
export function telegramStatus() {
  return {
    configured:    Boolean(BOT_TOKEN && CHAT_ID),
    hasToken:      Boolean(BOT_TOKEN),
    hasChatId:     Boolean(CHAT_ID),
    botUsername:   BOT_TOKEN ? '✅' : '❌',
    chatId:        CHAT_ID   ? `✅ ${CHAT_ID}` : '❌ (أرسل رسالة للبوت ثم node scripts/setup_telegram.mjs)',
  };
}

// ── إرسال رسالة نصية ─────────────────────────────────────────────────────────
/**
 * إرسال رسالة نصية عادية إلى Telegram
 * @param {string} text - نص الرسالة
 * @param {Object} opts - خيارات إضافية
 * @returns {Promise<{ok: boolean, error?: string}>}
 */
export async function sendTelegram(text, opts = {}) {
  const qa = validateTelegramPayload(text, opts);
  if (!qa.ok) {
    return { ok: false, error: `Telegram QA blocked delivery: ${qa.issues.join('; ')}`, qaBlocked: true };
  }

  if (!isTelegramConfigured()) {
    return { ok: false, error: 'TELEGRAM_BOT_TOKEN أو TELEGRAM_CHAT_ID غير مضبوطة في .env' };
  }

  if (!opts.clientDelivery && !opts.opsAlert && process.env.EGX_INTERNAL_TELEGRAM_OK !== '1') {
    return {
      ok: false,
      error: 'Telegram delivery blocked: only egx_telegram_daily.mjs may send to clients by default',
      policyBlocked: true,
    };
  }

  // Telegram يدعم حتى 4096 حرف لكل رسالة
  const chunks = splitMessage(text, 4000);
  let lastResult = { ok: false };

  for (const chunk of chunks) {
    try {
      const res = await fetch(`${BASE_URL}/sendMessage`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          chat_id:    opts.chatId ?? CHAT_ID,
          text:       chunk,
          parse_mode: opts.parseMode ?? 'HTML',
          disable_web_page_preview: true,
          ...opts.extra,
        }),
      });

      const json = await res.json();
      if (!json.ok) {
        return { ok: false, error: json.description ?? 'Telegram API error', code: json.error_code };
      }
      lastResult = { ok: true, messageId: json.result?.message_id };
    } catch (err) {
      return { ok: false, error: `fetch error: ${err.message}` };
    }
  }

  return lastResult;
}

// ── بناء تقرير Telegram المنسّق ──────────────────────────────────────────────
/**
 * يبني مجموعة رسائل Telegram منسّقة من بيانات التقرير
 * كل section = رسالة مستقلة = تجربة قراءة ممتازة
 *
 * @param {Object} data - { scalp, shortSwing, longSwing, investment, undervalued, breadth, macro }
 * @param {Object} meta - { date, scalp, swing, invest, usdEgp }
 * @returns {string[]} مصفوفة رسائل HTML جاهزة للإرسال
 */
/** Escape HTML chars so Telegram HTML parser doesn't break */
function esc(v) {
  if (v == null) return '';
  return String(v)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

export function buildTelegramMessages(data, meta = {}) {
  const {
    scalp = [], shortSwing = [], longSwing = [],
    investment = [], undervalued = [], breadth = {}, macro = null,
  } = data;

  const date    = meta.date ?? new Date().toISOString().split('T')[0];
  const dayName = ['الأحد','الاثنين','الثلاثاء','الأربعاء','الخميس','الجمعة','السبت'][new Date().getDay()];
  const H  = (t) => `<b>${t}</b>`;
  const C  = (t) => `<code>${esc(t)}</code>`;   // always escape inside code tags
  const LN = '─'.repeat(28);

  const messages = [];

  // ── رسالة 1: Header + ملخص السوق ──────────────────────────────────────────
  // normalize breadth (يقبل up/down/flat أو rising/falling)
  const bUp    = breadth.up ?? breadth.rising ?? 0;
  const bDown  = breadth.down ?? breadth.falling ?? 0;
  const bFlat  = breadth.flat ?? breadth.unchanged ?? 0;
  const bTotal = breadth.total ?? ((bUp + bDown + bFlat) > 0 ? bUp + bDown + bFlat : 1);
  const pctUp  = breadth.pctUp ?? Math.round(bUp / bTotal * 100);

  const regimeCtx  = meta.regimeCtx ?? {};
  const regimeStr  = regimeCtx.regime ?? '';
  const mktIcon    = regimeStr === 'BULL' ? '🟢' : regimeStr === 'BEAR' ? '🔴' : (pctUp >= 50 ? '🟢' : pctUp >= 35 ? '🟡' : '🔴');
  const mktMood    = regimeStr === 'BULL' ? 'صاعد' : regimeStr === 'BEAR' ? 'هابط' : (pctUp >= 55 ? 'صاعد' : pctUp >= 45 ? 'محايد' : 'هابط');
  const totalSig = scalp.length + shortSwing.length + longSwing.length;

  const msg1 = [
    `📊 ${H('EGX Navigator — تقرير يومي')}`,
    `${dayName}  ${C(date)}`,
    LN,
    `${mktIcon} ${H('مزاج السوق:')} ${mktMood}  (${bTotal} سهم)`,
    `   🔼 صاعد: ${bUp}  🔽 هابط: ${bDown}  ↔️ ثابت: ${bFlat}  (${pctUp}%)`,
    regimeCtx.regime && regimeCtx.regime !== 'UNKNOWN'
      ? `🧠 ${H('النظام:')} ${C(regimeCtx.regime)} | ${C(regimeCtx.breadthSignal ?? 'N/A')} | وضعية: ${C(regimeCtx.posture ?? 'N/A')}${regimeCtx.exposure != null ? ` (${regimeCtx.exposure.toFixed(0)}%)` : ''}`
      : '',
    LN,
    `📌 ${H('الإشارات اليوم:')}`,
    `   ⚡ Scalp: ${C(scalp.length)}  🔄 Swing: ${C(shortSwing.length)}  📈 Long: ${C(longSwing.length)}`,
    `   🏦 Invest: ${C(investment.length)}  💎 Undervalued: ${C(undervalued.length)}`,
    macro
      ? `${LN}\n🌍 ${H('ماكرو:')} USD/EGP ${C(macro.usd_egp?.toFixed(2) ?? 'N/A')} | تضخم ${C((macro.inflation_pct?.toFixed(1) ?? 'N/A') + '%')}`
      : '',
  ].filter(l => l !== '').join('\n');
  messages.push(msg1);

  // ── رسالة 2: SCALP signals ─────────────────────────────────────────────────
  if (scalp.length > 0) {
    const lines = [
      `⚡ ${H('SCALP — جلسة اليوم')}`,
      `${C('هدف +1.5-2.5% | SL -0.8-1.2% | دخول قبل 11AM')}`,
      LN,
    ];
    for (let i = 0; i < scalp.length; i++) {
      const s   = scalp[i];
      const star = s.isQuality ? '★ ' : '';
      const slPct = (s.sl && s.entry) ? `(-${Math.abs((s.sl - s.entry) / s.entry * 100).toFixed(1)}%)` : '';
      const t1Pct = (s.t1 && s.entry) ? `(+${((s.t1 - s.entry) / s.entry * 100).toFixed(1)}%)` : '';
      lines.push(`${i+1}. ${H(star + esc(s.symbol))}`);
      lines.push(`   دخول ${C(esc(s.entry))}  SL ${C(esc(s.sl))} ${slPct}  T1 ${C(esc(s.t1))} ${t1Pct}  R:R ${esc(s.rr)}x`);
      if (s.reason) lines.push(`   <i>${esc(s.reason)}</i>`);
    }
    messages.push(lines.join('\n'));
  }

  // ── رسالة 3: SHORT SWING ──────────────────────────────────────────────────
  if (shortSwing.length > 0) {
    const lines = [
      `🔄 ${H('SHORT SWING — 3-7 أيام')}`,
      `${C('هدف +4-8% | SL هيكلي | R:R 2+')}`,
      LN,
    ];
    for (let i = 0; i < Math.min(shortSwing.length, 8); i++) {
      const s    = shortSwing[i];
      const star    = s.isQuality ? '★ ' : '';
      const grade   = s.grade ? ` ${esc(s.grade)}` : '';
      const uesStr  = s.ues != null ? ` | UES:${C(s.ues.toFixed(0))}` : '';
      const convStr = s.convictionTier ? ` [${esc(s.convictionTier.replace('_CONVICTION',''))}]` : '';
      lines.push(`${i+1}. ${H(star + esc(s.symbol))}${grade}${uesStr}${convStr} — نقاط ${s.score}/100`);
      if (s.levels) {
        lines.push(`   دخول ${C(esc(s.levels.entryLow) + '-' + esc(s.levels.entryHigh))}`);
        lines.push(`   SL ${C(esc(s.levels.sl))}  T1 ${C(esc(s.levels.t1))}  R:R ${esc(s.levels.rr1)}x`);
      }
      const rsiV = s.rsiVal ?? s.rsi;
      const adxV = s.adxVal ?? s.adx;
      if (rsiV != null) lines.push(`   RSI ${(+rsiV).toFixed(0)}  ADX ${adxV != null ? (+adxV).toFixed(0) : '?'}`);
    }
    messages.push(lines.join('\n'));
  }

  // ── رسالة 4: LONG SWING + INVESTMENT (مجمّعة) ─────────────────────────────
  const longInvLines = [];
  if (longSwing.length > 0) {
    longInvLines.push(`📈 ${H('LONG SWING — 2-4 أسابيع')}`);
    longInvLines.push(`${C('هدف +10-20% | R:R 2.5+')}`);
    longInvLines.push(LN);
    for (let i = 0; i < Math.min(longSwing.length, 5); i++) {
      const s    = longSwing[i];
      const star = s.isQuality ? '★ ' : '';
      longInvLines.push(`${i+1}. ${H(star + esc(s.symbol))} — ${esc(s.reason ?? s.setupType ?? '')}`);
      const slVal = s.levels?.sl ?? s.sl;
      const t1Val = s.levels?.t1 ?? s.t1;
      const t2Val = s.levels?.t2 ?? s.t2;
      const rrVal = s.levels?.rr1 ?? s.rr;
      const cp    = s.currentPrice != null ? (+s.currentPrice).toFixed(3) : '?';
      const slPct = (slVal != null && s.currentPrice) ? `(-${Math.abs((slVal - s.currentPrice) / s.currentPrice * 100).toFixed(1)}%)` : '';
      const t1Pct = (t1Val != null && s.currentPrice) ? `(+${((t1Val - s.currentPrice) / s.currentPrice * 100).toFixed(1)}%)` : '';
      longInvLines.push(`   دخول ${C(cp)}  SL ${C(esc(slVal ?? '?'))} ${slPct}  T1 ${C(esc(t1Val ?? '?'))} ${t1Pct}  R:R ${C(esc(rrVal ?? '?'))}x`);
      if (t2Val != null) longInvLines.push(`   T2 ${C(esc(t2Val))}`);
    }
  }

  if (investment.length > 0) {
    if (longInvLines.length) longInvLines.push('');
    longInvLines.push(`🏦 ${H('INVESTMENT — 6-12 شهر')}`);
    longInvLines.push(LN);
    for (let i = 0; i < investment.length; i++) {
      const s  = investment[i];
      const pe = s.pe_ratio != null ? `P/E ${(+s.pe_ratio).toFixed(1)}` : (s.pe != null ? `P/E ${(+s.pe).toFixed(1)}` : '');
      const pb = s.pb_ratio != null ? `P/B ${(+s.pb_ratio).toFixed(2)}` : (s.pb != null ? `P/B ${(+s.pb).toFixed(2)}` : '');
      const cp = s.currentPrice != null ? (+s.currentPrice).toFixed(2) : '?';
      longInvLines.push(`${i+1}. ${H('★ ' + esc(s.symbol))} — ${C(cp)} EGP`);
      if (pe || pb) longInvLines.push(`   ${[pe, pb].filter(Boolean).join('  ')}`);
      if (s.reason) longInvLines.push(`   <i>${esc(s.reason)}</i>`);
    }
  }

  if (longInvLines.length) messages.push(longInvLines.join('\n'));

  // ── رسالة 5: Undervalued (إن وجد) ─────────────────────────────────────────
  if (undervalued.length > 0) {
    const lines = [
      `💎 ${H('UNDERVALUED — قيمة حقيقية منخفضة')}`,
      `${C('P/E < 10 | P/B < 1.5 | ROE > 10%')}`,
      LN,
    ];
    for (let i = 0; i < undervalued.length; i++) {
      const s  = undervalued[i];
      const pe = (s.pe_ratio ?? s.pe) != null ? `P/E ${(+(s.pe_ratio ?? s.pe)).toFixed(1)}` : '';
      const pb = (s.pb_ratio ?? s.pb) != null ? `P/B ${(+(s.pb_ratio ?? s.pb)).toFixed(2)}` : '';
      lines.push(`${i+1}. ${H(esc(s.symbol))} — ${[pe, pb].filter(Boolean).join(' | ')}`);
      if (s.reason) lines.push(`   <i>${esc(s.reason)}</i>`);
    }
    messages.push(lines.join('\n'));
  }

  // ── رسالة 6: Macro (إن وجد) ────────────────────────────────────────────────
  if (macro && (macro.usd_egp || macro.inflation_pct || macro.cbe_rate_pct)) {
    const realRate = macro.real_interest_rate;
    const rateIcon = realRate == null ? '⚪'
                   : realRate < -5    ? '🟢'
                   : realRate < 0     ? '🟡'
                   : realRate < 5     ? '🟠'
                   : '🔴';

    const biasMap  = {
      FAVOUR_EXPORTERS: '📦 تفضيل المُصدِّرين وصناديق العملة',
      EQUITY_POSITIVE:  '🟢 بيئة محفّزة للأسهم (فائدة حقيقية سلبية)',
      EQUITY_NEGATIVE:  '🔴 الودائع تنافس الأسهم (فائدة حقيقية موجبة)',
      NEUTRAL:          '🟡 بيئة محايدة — انتقائية عالية',
    };

    const tvBadge = macro.tradingview_data ? '📡 TradingView Live' : '🌐 APIs';

    // اتجاه التضخم: آخر 3-4 نقاط
    const infTrend = macro.inflation_trend?.length >= 2
      ? macro.inflation_trend.slice(-4).map(b => `${b.date.slice(2)}: ${(+b.value).toFixed(1)}%`).join(' → ')
      : null;

    // اتجاه فائدة CBE
    const cbeTrend = macro.cbe_rate_trend?.length >= 2
      ? macro.cbe_rate_trend.slice(-4).map(b => `${b.date.slice(2)}: ${(+b.value).toFixed(1)}%`).join(' → ')
      : null;

    const infMom  = macro.inflation_momentum === 'falling' ? ' ↘' : macro.inflation_momentum === 'rising' ? ' ↗' : '';
    const cbeMom  = macro.cbe_rate_momentum  === 'falling' ? ' ↘' : macro.cbe_rate_momentum  === 'rising' ? ' ↗' : '';

    const macroLines = [
      `🌍 ${H('الاقتصاد الكلي — مصر')}  ${C(tvBadge)}`,
      `${C(esc(macro.usd_egp_date ?? date))}`,
      LN,
      macro.usd_egp          ? `💵 ${H('USD/EGP:')}  ${C((+macro.usd_egp).toFixed(2))} جنيه` : '',
      macro.inflation_pct    ? `📈 ${H('التضخم (EGIRYY):')}  ${C((+macro.inflation_pct).toFixed(1) + '%')} (${esc(macro.inflation_year ?? '')})${infMom}` : '',
      infTrend               ? `   ${C(esc(infTrend))}` : '',
      (macro.cbe_rate_pct ?? macro.lending_rate_pct)
        ? `🏦 ${H('فائدة CBE (EGINTR):')}  ${C((+(macro.cbe_rate_pct ?? macro.lending_rate_pct)).toFixed(1) + '%')} (${esc(macro.cbe_rate_year ?? '')})${cbeMom}` : '',
      cbeTrend               ? `   ${C(esc(cbeTrend))}` : '',
      realRate != null       ? `${rateIcon} ${H('فائدة حقيقية:')}  ${C((+realRate).toFixed(1) + '%')} → ${realRate < 0 ? 'أسهم &gt; ودائع' : 'ودائع منافسة'}` : '',
      '',
      biasMap[macro.strategic_bias] ? biasMap[macro.strategic_bias] : '',
    ].filter(l => l !== '');

    messages.push(macroLines.join('\n'));
  }

  // ── رسالة 7: Footer ────────────────────────────────────────────────────────
  const footer = [
    LN,
    `${C('★ = جودة تاريخية v3 | Backtest: WR 19.7% | PF 2.31x')}`,
    `<i>تفسير: معدل الفوز 19.7% لكن الربح الواحد يساوي 5.7× الخسارة (نمط momentum — يتطلب انضباطاً في SL)</i>`,
    `<i>${new Date().toLocaleString('ar-EG', { timeZone: 'Africa/Cairo' })}</i>`,
  ].join('\n');
  messages.push(footer);

  return messages;
}

// ── إرسال التقرير اليومي المنسّق ─────────────────────────────────────────────
/**
 * يبني رسائل Telegram منسّقة ويُرسلها section بـ section
 *
 * @param {string|Object} reportOrData - نص التقرير القديم أو كائن البيانات الجديد
 * @param {Object} meta               - { date, scalp, swing, invest, usdEgp, _data }
 */
export async function sendDailyReport(reportOrData, meta = {}) {
  if (!isTelegramConfigured()) {
    console.warn('[notify] ⚠️  Telegram غير مضبوط — التقرير لن يُرسل');
    return { ok: false, skipped: true };
  }

  // إذا أُرسل كائن البيانات مباشرة (التنسيق الجديد)
  const dataObj = meta._data ?? null;

  let messages;
  if (dataObj) {
    messages = buildTelegramMessages(dataObj, meta);
  } else {
    // fallback: الطريقة القديمة — نرسل نص واحد مع header
    const header = buildHeader(meta);
    const full   = `${header}\n\n${typeof reportOrData === 'string' ? reportOrData : JSON.stringify(reportOrData)}`;
    messages     = splitMessage(full, 4000);
  }

  let lastResult = { ok: false };
  let sent = 0;

  for (const msg of messages) {
    if (!msg.trim()) continue;
    const res = await sendTelegram(msg, { parseMode: 'HTML' });
    if (!res.ok) {
      console.error(`[notify] ❌  فشل إرسال الجزء ${sent+1}: ${res.error}`);
      // نواصل بدلاً من الوقف
    } else {
      sent++;
      lastResult = res;
    }
    // فاصل صغير بين الرسائل لتجنب rate limiting
    if (messages.length > 1) await new Promise(r => setTimeout(r, 300));
  }

  if (lastResult.ok) {
    console.log(`[notify] ✅  التقرير أُرسل (${sent}/${messages.length} رسائل)`);
  }

  return lastResult;
}

// ── إرسال تنبيه إشارة ───────────────────────────────────────────────────────
/**
 * إرسال تنبيه إشارة تداول واحدة
 * @param {Object} signal - إشارة التداول
 */
export async function sendSignalAlert(signal) {
  if (!isTelegramConfigured()) return { ok: false, skipped: true };

  const icon = { STRONG_BUY: '🟢🟢', BUY: '🟢', WATCH: '🟡', NEUTRAL: '⚪' }[signal.action ?? 'NEUTRAL'] ?? '⚪';
  const lines = [
    `${icon} <b>إشارة جديدة — ${signal.symbol}</b>`,
    ``,
    `النوع: <b>${signal.setupType ?? signal.action ?? 'N/A'}</b>`,
    signal.score     ? `النقاط: ${signal.score}/100` : null,
    signal.entry     ? `دخول: <code>${signal.entry}</code>` : null,
    signal.sl        ? `وقف الخسارة: <code>${signal.sl}</code>` : null,
    signal.t1        ? `هدف 1: <code>${signal.t1}</code>` : null,
    signal.rr        ? `R:R: ${signal.rr}x` : null,
    signal.reason    ? `\nالسبب: ${signal.reason}` : null,
    `\n⏰ ${new Date().toLocaleString('ar-EG', { timeZone: 'Africa/Cairo' })}`,
  ].filter(Boolean);

  return sendTelegram(lines.join('\n'), { parseMode: 'HTML' });
}

// ── إرسال ملخص ماكرو ────────────────────────────────────────────────────────
/**
 * إرسال بيانات الاقتصاد الكلي المصري
 * @param {Object} macro - بيانات الماكرو
 */
export async function sendMacroUpdate(macro) {
  if (!isTelegramConfigured()) return { ok: false, skipped: true };

  const lines = [
    `🌍 <b>البيانات الاقتصادية — مصر</b>`,
    `<code>${new Date().toISOString().split('T')[0]}</code>`,
    ``,
    macro.usdEgp      ? `💵 الدولار/جنيه: <b>${macro.usdEgp.toFixed(2)}</b>` : null,
    macro.inflation   ? `📈 التضخم: <b>${macro.inflation.toFixed(2)}%</b>` : null,
    macro.cbeRate     ? `🏦 فائدة البنك المركزي: <b>${macro.cbeRate.toFixed(2)}%</b>` : null,
    macro.egx30       ? `📊 EGX30: <b>${macro.egx30.toFixed(0)}</b>` : null,
    macro.notes       ? `\n📝 ${macro.notes}` : null,
  ].filter(Boolean);

  return sendTelegram(lines.join('\n'), { parseMode: 'HTML' });
}

// ── اختبار الاتصال ──────────────────────────────────────────────────────────
/**
 * إرسال رسالة اختبار للتحقق من الإعداد
 */
export async function testTelegramConnection() {
  if (!isTelegramConfigured()) {
    return {
      ok: false,
      configured: false,
      error: 'TELEGRAM_BOT_TOKEN أو TELEGRAM_CHAT_ID غير موجودة في .env',
    };
  }

  const result = await sendTelegram(
    `✅ <b>EGX System — اختبار الاتصال</b>\n\n` +
    `النظام متصل ويعمل بشكل صحيح.\n` +
    `⏰ ${new Date().toISOString()}`,
    { parseMode: 'HTML' }
  );

  return { ...result, configured: true };
}

// ── دوال مساعدة ─────────────────────────────────────────────────────────────

/**
 * تقسيم النص الطويل إلى أجزاء
 */
function splitMessage(text, maxLen = 4000) {
  if (text.length <= maxLen) return [text];

  const chunks = [];
  const lines  = text.split('\n');
  let current  = '';

  for (const line of lines) {
    if ((current + '\n' + line).length > maxLen) {
      if (current) chunks.push(current.trim());
      current = line;
    } else {
      current += (current ? '\n' : '') + line;
    }
  }
  if (current.trim()) chunks.push(current.trim());
  return chunks;
}

/**
 * بناء رأس التقرير
 */
function buildHeader(meta) {
  const date    = meta.date ?? new Date().toISOString().split('T')[0];
  const dayName = ['الأحد','الاثنين','الثلاثاء','الأربعاء','الخميس','الجمعة','السبت'][new Date().getDay()];
  const lines   = [
    `📊 <b>EGX Daily Report</b>`,
    `📅 ${dayName} ${date}`,
  ];

  if (meta.scalp  !== undefined) lines.push(`⚡ Scalp: ${meta.scalp}`);
  if (meta.swing  !== undefined) lines.push(`🔄 Swing: ${meta.swing}`);
  if (meta.invest !== undefined) lines.push(`🏦 Invest: ${meta.invest}`);
  if (meta.usdEgp !== undefined) lines.push(`💵 USD/EGP: ${meta.usdEgp.toFixed(2)}`);

  lines.push(`${'─'.repeat(30)}`);
  return lines.join('\n');
}
