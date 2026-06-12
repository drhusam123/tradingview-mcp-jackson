#!/usr/bin/env python3
"""
Health Monitor — رقيب صحة النظام التلقائي
==========================================
يفحص كل صباح:
  1. freshness اليوجات الحيوية (daily / night_lab / telegram / evolution / cognition)
  2. أحداث قاعدة البيانات (آخر scan / signal / breadth)
  3. حالة TradingView CDP
  4. لا يرسل للعميل افتراضياً؛ تنبيهات الصحة داخلية فقط عند EGX_INTERNAL_TELEGRAM_OK=1

التشغيل:
  python3 scripts/python/health_monitor.py check
  python3 scripts/python/health_monitor.py report    (داخلي فقط إذا EGX_INTERNAL_TELEGRAM_OK=1)
"""
import os, sys, json, sqlite3, datetime, time, subprocess, requests
from pathlib import Path

ROOT     = Path(__file__).parent.parent.parent
DB_PATH  = str(ROOT / 'data' / 'egx_trading.db')
LOGS_DIR = ROOT / 'logs'

# ── Telegram ─────────────────────────────────────────────────────────────────
def _tg_send(text: str):
    if os.environ.get('EGX_INTERNAL_TELEGRAM_OK') != '1':
        print(json.dumps({
            "warn": "Health Telegram delivery blocked by default. Set EGX_INTERNAL_TELEGRAM_OK=1 for internal-only alerts."
        }, ensure_ascii=False))
        return False
    token   = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID',   '')
    if not token or not chat_id:
        # Try to read from .env file
        env_path = ROOT / '.env'
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if '=' in line and not line.startswith('#'):
                    k, _, v = line.partition('=')
                    k = k.strip(); v = v.strip().strip('"').strip("'")
                    if k == 'TELEGRAM_BOT_TOKEN': token = v
                    if k == 'TELEGRAM_CHAT_ID':   chat_id = v
    if not token or not chat_id:
        print(json.dumps({"warn": "Telegram not configured — skipping alert"}))
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        r = requests.post(url, json={'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}, timeout=10)
        return r.ok
    except Exception as e:
        print(json.dumps({"warn": f"Telegram send failed: {e}"}))
        return False

# ── Log freshness ─────────────────────────────────────────────────────────────
LOG_CHECKS = [
    # (log_file, max_age_hours_weekday, max_age_hours_weekend, label)
    ('tv_auto_daily.log', 26, 72,  'Daily Run'),
    ('night_lab.log',  26, 72,  'Night Lab'),
    ('telegram.log',   26, 72,  'Telegram Delivery'),
    ('evolution.log',  26, 168, 'Evolution Engine'),
    ('cognition.log',  26, 168, 'Cognition'),
]

def check_log_freshness():
    issues = []
    now = datetime.datetime.utcnow()
    is_weekend = now.weekday() >= 5  # Sat/Sun UTC

    for log_file, max_wday, max_wend, label in LOG_CHECKS:
        path = LOGS_DIR / log_file
        max_age = max_wend if is_weekend else max_wday
        if not path.exists():
            issues.append(f"⚠️ {label}: log file missing")
            continue
        mtime = datetime.datetime.utcfromtimestamp(path.stat().st_mtime)
        age_h = (now - mtime).total_seconds() / 3600
        if age_h > max_age:
            issues.append(f"❌ {label}: آخر تشغيل منذ {age_h:.0f} ساعة (حد: {max_age}h)")
        else:
            pass  # OK

    return issues

# ── DB freshness ──────────────────────────────────────────────────────────────
def check_db_freshness():
    issues = []
    now_str = datetime.date.today().isoformat()
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    is_weekend = datetime.date.today().weekday() >= 5

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        # OHLCV — should have data from yesterday (or today if EGX traded)
        last_ohlcv = conn.execute(
            "SELECT MAX(date(bar_time,'unixepoch')) as d FROM ohlcv_history_execution"
        ).fetchone()['d']
        if last_ohlcv and last_ohlcv < yesterday and not is_weekend:
            issues.append(f"❌ OHLCV: آخر بيانات {last_ohlcv} (قديمة)")

        # Signals — should have data from today
        last_scan = conn.execute(
            "SELECT MAX(scan_date) as d FROM scans"
        ).fetchone()['d']
        if last_scan and last_scan < yesterday and not is_weekend:
            issues.append(f"⚠️ Scans: آخر مسح {last_scan}")

        last_pred = conn.execute(
            "SELECT MAX(pred_date) as d FROM explosion_predictions"
        ).fetchone()['d']
        if last_pred and last_scan and last_pred < last_scan:
            issues.append(f"⚠️ ML predictions ({last_pred}) أقدم من scans ({last_scan})")

        # Pipeline step audit fallback when log file is stale
        try:
            last_pipe = conn.execute(
                "SELECT MAX(started_at) as t FROM pipeline_step_runs"
            ).fetchone()['t']
            if last_pipe:
                pipe_age_h = (
                    datetime.datetime.utcnow()
                    - datetime.datetime.fromisoformat(last_pipe.replace('Z', ''))
                ).total_seconds() / 3600
                if pipe_age_h > 30 and not is_weekend:
                    issues.append(f"⚠️ Pipeline: آخر خطوة منذ {pipe_age_h:.0f}h ({last_pipe})")
        except Exception:
            pass

        # UES — should have data from today or yesterday
        last_ues = conn.execute(
            "SELECT MAX(signal_date) as d FROM unified_signals"
        ).fetchone()['d']
        if last_ues and last_ues < yesterday and not is_weekend:
            issues.append(f"⚠️ UES Signals: آخر حساب {last_ues}")

        # Breadth — should be recent
        last_breadth = conn.execute(
            "SELECT MAX(date) as d FROM market_breadth_daily"
        ).fetchone()['d']
        if last_breadth and last_breadth < yesterday and not is_weekend:
            issues.append(f"⚠️ Market Breadth: آخر بيانات {last_breadth}")

        # Per-stock profiles — check computed_date
        last_dna = conn.execute(
            "SELECT MAX(computed_date) as d FROM stock_profiles_deep"
        ).fetchone()['d']
        if last_dna:
            dna_age = (datetime.date.today() - datetime.date.fromisoformat(last_dna)).days
            if dna_age > 7:
                issues.append(f"⚠️ Stock DNA: آخر تحديث {last_dna} ({dna_age} يوم)")

        conn.close()
    except Exception as e:
        issues.append(f"❌ DB Error: {e}")

    return issues

# ── TradingView CDP ───────────────────────────────────────────────────────────
def check_tradingview():
    try:
        import urllib.request
        urllib.request.urlopen('http://localhost:9222/json', timeout=3)
        return None  # OK
    except Exception:
        return "⚠️ TradingView CDP غير متاح (port 9222) — البيانات لن تُجلب"

# ── Night lab last run ────────────────────────────────────────────────────────
def check_night_lab():
    issues = []
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT run_date, command, signal_integration_status, total_duration_seconds "
            "FROM night_lab_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if not row:
            issues.append("⚠️ Night Lab لم يُشغَّل بعد")
            return issues
        age = (datetime.date.today() - datetime.date.fromisoformat(row['run_date'])).days
        if age > 2 and datetime.date.today().weekday() < 5:  # weekday
            issues.append(f"❌ Night Lab: آخر تشغيل {row['run_date']} (منذ {age} أيام)")
        if row['signal_integration_status'] not in ('ok', 'skipped', None):
            issues.append(f"⚠️ Night Lab signal_integration: {row['signal_integration_status']}")
    except Exception as e:
        issues.append(f"⚠️ Night Lab DB error: {e}")
    return issues

# ── Main report ───────────────────────────────────────────────────────────────
def cmd_check(force_notify=False):
    all_issues = []
    all_issues += check_log_freshness()
    all_issues += check_db_freshness()
    all_issues += check_night_lab()

    tv_issue = check_tradingview()
    if tv_issue:
        all_issues.append(tv_issue)

    today = datetime.date.today().isoformat()
    status = "✅ النظام سليم" if not all_issues else f"⚠️ {len(all_issues)} مشكلة"

    report = {
        "date":   today,
        "status": status,
        "issues": all_issues,
        "n_issues": len(all_issues),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    # تنبيه داخلي فقط إذا وجدت مشاكل أو طُلب صراحةً، ولا يرسل افتراضياً.
    if all_issues or force_notify:
        lines = [f"<b>🏥 EGX System Health — {today}</b>", f"Status: {status}"]
        if all_issues:
            lines.append("")
            lines.append("<b>المشاكل المكتشفة:</b>")
            for issue in all_issues:
                lines.append(f"  {issue}")
        else:
            lines.append("كل الأنظمة تعمل طبيعياً ✅")
        lines.append("")
        lines.append("تشغيل: python3 scripts/python/night_lab.py run")
        _tg_send('\n'.join(lines))

    return len(all_issues)

if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'check'
    if cmd == 'check':
        n = cmd_check(force_notify=False)
        sys.exit(0 if n == 0 else 1)
    elif cmd == 'report':
        cmd_check(force_notify=True)
    else:
        print(json.dumps({"error": "unknown command", "usage": "check | report"}))
        sys.exit(1)
