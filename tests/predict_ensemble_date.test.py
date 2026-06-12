"""pred_date resolution for predict_ensemble."""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts' / 'python'))

from egx_ml_trainer import _resolve_pred_date  # noqa: E402


def test_resolve_pred_date_override():
    conn = sqlite3.connect(':memory:')
    assert _resolve_pred_date(conn, '2026-06-11') == '2026-06-11'


def test_resolve_pred_date_latest_on_holiday():
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE ohlcv_history_execution (symbol TEXT, bar_time TEXT, close REAL)')
    conn.execute("INSERT INTO ohlcv_history_execution VALUES ('COMI', '2026-06-11', 100)")
    conn.commit()
    # When calendar today > latest bar, use latest session
    import datetime
    today = datetime.date.today().isoformat()
    if today > '2026-06-11':
        assert _resolve_pred_date(conn) == '2026-06-11'


if __name__ == '__main__':
    test_resolve_pred_date_override()
    test_resolve_pred_date_latest_on_holiday()
    print('OK')
