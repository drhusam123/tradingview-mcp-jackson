#!/usr/bin/env node
/**
 * EGX Telegram Setup — إعداد البوت تلقائياً
 * ============================================
 * يكتشف chat_id تلقائياً ويحدّث .env
 * ثم يرسل رسالة ترحيب للتأكيد
 *
 * التشغيل:
 *   node scripts/setup_telegram.mjs
 *   node scripts/setup_telegram.mjs --wait   (ينتظر حتى تصل رسالة — 60 ثانية)
 *
 * المالك: Dr. Husam | مايو 2026
 */

import { readFileSync, writeFileSync, existsSync } from 'fs';
import { join, dirname }                           from 'path';
import { fileURLToPath }                           from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT      = join(__dirname, '..');
const ENV_PATH  = join(ROOT, '.env');
const WAIT_MODE = process.argv.includes('--wait');
const TOKEN     = process.env.TELEGRAM_BOT_TOKEN ?? readEnvKey('TELEGRAM_BOT_TOKEN');

// ── قراءة .env ─────────────────────────────────────────────────────────────
function readEnvKey(key) {
  if (!existsSync(ENV_PATH)) return '';
  const lines = readFileSync(ENV_PATH, 'utf8').split('\n');
  for (const line of lines) {
    const t = line.trim();
    if (t.startsWith(key + '=')) return t.slice(key.length + 1).trim();
  }
  return '';
}

function writeEnvKey(key, value) {
  let content = existsSync(ENV_PATH) ? readFileSync(ENV_PATH, 'utf8') : '';
  const regex = new RegExp(`^(${key}=).*$`, 'm');
  if (regex.test(content)) {
    content = content.replace(regex, `${key}=${value}`);
  } else {
    content += `\n${key}=${value}`;
  }
  writeFileSync(ENV_PATH, content, 'utf8');
}

// ── Telegram API ────────────────────────────────────────────────────────────
async function tg(method, body = {}) {
  const res = await fetch(`https://api.telegram.org/bot${TOKEN}/${method}`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(body),
  });
  return res.json();
}

async function getUpdates(offset = 0) {
  return tg('getUpdates', { offset, limit: 10, timeout: 0 });
}

// ── رسالة الترحيب ──────────────────────────────────────────────────────────
async function sendWelcome(chatId) {
  const msg = [
    `🎉 <b>EGX Navigator — متصل بنجاح!</b>`,
    ``,
    `✅ البوت يعمل ومتصل بنظام التداول`,
    `📊 ستصلك تقارير EGX اليومية هنا تلقائياً`,
    ``,
    `<b>الأوامر المتاحة قريباً:</b>`,
    `/report  — التقرير اليومي الآن`,
    `/macro   — بيانات الاقتصاد الكلي`,
    `/status  — حالة النظام`,
    ``,
    `🔄 يعمل بعد إغلاق البورصة كل يوم تجارة`,
  ].join('\n');

  return tg('sendMessage', { chat_id: chatId, text: msg, parse_mode: 'HTML' });
}

// ── Main ────────────────────────────────────────────────────────────────────
async function main() {
  console.log('\n' + '═'.repeat(50));
  console.log('  EGX Telegram Setup');
  console.log('═'.repeat(50));

  if (!TOKEN) {
    console.error('❌  TELEGRAM_BOT_TOKEN غير موجود في .env');
    process.exit(1);
  }

  // تحقق أن التوكن صحيح
  const me = await tg('getMe');
  if (!me.ok) {
    console.error(`❌  Token خاطئ: ${me.description}`);
    process.exit(1);
  }
  console.log(`✅  البوت: @${me.result.username} (${me.result.first_name})`);

  // هل chat_id موجود أصلاً؟
  const existingChatId = readEnvKey('TELEGRAM_CHAT_ID');
  if (existingChatId) {
    console.log(`✅  CHAT_ID موجود: ${existingChatId}`);
    const test = await tg('sendMessage', {
      chat_id: existingChatId,
      text: `✅ EGX Navigator متصل ويعمل — ${new Date().toISOString()}`,
    });
    if (test.ok) {
      console.log('✅  رسالة اختبار أُرسلت');
    } else {
      console.log(`⚠️  فشل الاختبار: ${test.description}`);
    }
    return;
  }

  // ابحث في الـ updates الموجودة
  const upd = await getUpdates();
  if (upd.ok && upd.result?.length > 0) {
    const last   = upd.result[upd.result.length - 1];
    const chatId = (last.message ?? last.channel_post)?.chat?.id;
    if (chatId) {
      console.log(`✅  وجدت chat_id: ${chatId}`);
      writeEnvKey('TELEGRAM_CHAT_ID', String(chatId));
      console.log('💾  محفوظ في .env');
      await sendWelcome(chatId);
      console.log('✅  رسالة الترحيب أُرسلت');
      return;
    }
  }

  // لم تصل رسائل بعد
  if (!WAIT_MODE) {
    console.log('\n⏳  لم تصل رسائل للبوت بعد');
    console.log(`   أرسل أي رسالة لـ @${me.result.username} على Telegram`);
    console.log(`   ثم أعد تشغيل: node scripts/setup_telegram.mjs`);
    console.log(`   أو استخدم: node scripts/setup_telegram.mjs --wait`);
    return;
  }

  // وضع الانتظار — تحقق كل 3 ثوانٍ لمدة 60 ثانية
  console.log(`\n⏳  في انتظار رسالة من @${me.result.username} ...`);
  console.log(`   افتح Telegram وأرسل أي رسالة للبوت الآن`);

  let offset = 0;
  let found  = false;
  const end  = Date.now() + 60_000;

  while (Date.now() < end && !found) {
    await new Promise(r => setTimeout(r, 3000));
    const u = await getUpdates(offset);
    if (u.ok && u.result?.length > 0) {
      for (const item of u.result) {
        offset = item.update_id + 1;
        const chatId = (item.message ?? item.channel_post)?.chat?.id;
        if (chatId) {
          console.log(`\n✅  رسالة وصلت! chat_id: ${chatId}`);
          writeEnvKey('TELEGRAM_CHAT_ID', String(chatId));
          console.log('💾  محفوظ في .env');
          await sendWelcome(chatId);
          console.log('✅  رسالة الترحيب أُرسلت — البوت جاهز!');
          found = true;
          break;
        }
      }
    } else {
      process.stdout.write('.');
    }
  }

  if (!found) {
    console.log('\n⏰  انتهى الوقت — أرسل رسالة وأعد التشغيل');
  }
}

main().catch(err => {
  console.error('💥', err.message);
  process.exit(1);
});
