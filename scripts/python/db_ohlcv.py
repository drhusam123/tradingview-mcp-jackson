"""
Canonical OHLCV table names for production reads.

Override via env for rollback:
  EGX_OHLCV_TABLE=ohlcv_history
  EGX_OHLCV_FEATURES=ohlcv_history_features
"""
import os

OHLCV_TABLE = os.environ.get('EGX_OHLCV_TABLE', 'ohlcv_history_execution')
OHLCV_FEATURES = os.environ.get('EGX_OHLCV_FEATURES', 'ohlcv_history_features')
OHLCV_RAW = 'ohlcv_history'
