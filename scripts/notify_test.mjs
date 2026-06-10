/**
 * اختبار نظام الإشعارات
 * ======================
 * يتحقق من:
 *   1. إعداد Telegram (BOT_TOKEN + CHAT_ID)
 *   2. إرسال رسالة اختبار
 *   3. جلب بيانات الماكرو (USD/EGP + Inflation + CBE)
 *
 * التشغيل:
 *   node scripts/notify_test.mjs
 *   node scripts/notify_test.mjs --macro-only   (بدون Telegram)
 *
 * المالك: Dr. Husam | مايو 2026
 */

import { testTelegramConnection, isTelegramConfigured, telegramStatus, sendMacroUpdate } from '../src/egx/notify.js';
import { pythonMacroData } from '../src/egx/python_bridge.js';

const MACRO_ONLY = process.argv.includes('--macro-only');
const SEP = '─'.repeat(50);

async function main() {
  console.log('\n' + '═'.repeat(50));
  console.log('  EGX Notify — اختبار النظام');
  console.log('═'.repeat(50));

  // ── 1. التحقق من إعداد Telegram ──────────────────────────────────────
  console.log('\n[1/3] إعداد Telegram...');
  const configured = isTelegramConfigured();
  const status = telegramStatus();

  if (!configured) {
    console.log(`   Token:   ${status.botUsername}`);
    console.log(`   Chat ID: ${status.chatId}`);
    if (status.hasToken && !status.hasChatId) {
      console.log('');
      console.log('   📋 خطوة واحدة فقط:');
      console.log('      1. افتح Telegram → ابحث عن @Egxnavigater_bot');
      console.log('      2. أرسل له أي رسالة (مثلاً: مرحبا)');
      console.log('      3. شغّل: node scripts/setup_telegram.mjs');
    } else {
      console.log('   📋 الخطوات: node scripts/setup_telegram.mjs --wait');
    }
  } else {
    console.log('   ✅  BOT_TOKEN و CHAT_ID موجودان');
    if (!MACRO_ONLY) {
      console.log('   📤  إرسال رسالة اختبار...');
      const result = await testTelegramConnection();
      if (result.ok) {
        console.log(`   ✅  رسالة الاختبار أُرسلت (messageId: ${result.messageId})`);
      } else {
        console.log(`   ❌  فشل الإرسال: ${result.error}`);
        if (result.code === 401) console.log('   💡  تحقق من صحة BOT_TOKEN');
        if (result.code === 400) console.log('   💡  تحقق من صحة CHAT_ID');
      }
    }
  }

  // ── 2. جلب بيانات الماكرو ─────────────────────────────────────────────
  console.log('\n[2/3] جلب بيانات الاقتصاد الكلي...');
  console.log('   (USD/EGP من ExchangeRate-API + World Bank للتضخم والفائدة)');

  try {
    const macro = await pythonMacroData();

    if (macro.success === false) {
      console.log(`   ❌  خطأ: ${macro.error}`);
    } else {
      console.log(`   ${SEP}`);
      if (macro.usd_egp)          console.log(`   💵  USD/EGP:            ${macro.usd_egp.toFixed(4)} (${macro.usd_egp_date ?? ''})`);
      if (macro.inflation_pct)    console.log(`   📈  التضخم المصري:       ${macro.inflation_pct.toFixed(2)}% (${macro.inflation_year ?? ''})`);
      if (macro.lending_rate_pct) console.log(`   🏦  فائدة الإقراض CBE:   ${macro.lending_rate_pct.toFixed(2)}% (${macro.lending_rate_year ?? ''})`);
      console.log(`   ${SEP}`);
      console.log(`   ⚖️   التوجه الاستراتيجي: ${macro.strategic_bias}`);
      if (macro.interpretation?.length) {
        console.log(`   📊  التفسير:`);
        for (const line of macro.interpretation) console.log(`       • ${line}`);
      }
      if (macro.saved_to_db) {
        console.log(`   💾  محفوظ في SQLite (macro_data)`);
      }
      if (macro.errors?.length) {
        console.log(`   ⚠️   تحذيرات: ${macro.errors.join(' | ')}`);
      }

      // ── إرسال ملخص ماكرو إلى Telegram إن كان مضبوطاً
      if (configured && !MACRO_ONLY) {
        console.log('\n[3/3] إرسال ملخص الماكرو إلى Telegram...');
        const nr = await sendMacroUpdate({
          usdEgp:    macro.usd_egp,
          inflation: macro.inflation_pct,
          cbeRate:   macro.lending_rate_pct,
          notes:     macro.strategic_bias,
        });
        if (nr.ok) {
          console.log(`   ✅  ملخص الماكرو أُرسل`);
        } else {
          console.log(`   ❌  فشل: ${nr.error}`);
        }
      } else {
        console.log('\n[3/3] تخطي إرسال الماكرو (Telegram غير مضبوط أو --macro-only)');
      }
    }
  } catch (err) {
    console.log(`   ❌  خطأ في جلب الماكرو: ${err.message}`);
  }

  console.log('\n' + '═'.repeat(50));
  console.log('  الاختبار انتهى');
  console.log('═'.repeat(50) + '\n');
}

main().catch(err => {
  console.error('💥 خطأ:', err.message);
  process.exit(1);
});
