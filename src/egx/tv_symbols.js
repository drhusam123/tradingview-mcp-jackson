const TV_EGX_PREFIX = 'EGX_DLY:';
const LEGACY_EGX_PREFIX = 'EGX:';

const KNOWN_NON_EGX_PREFIXES = new Set([
  'BINANCE',
  'BITSTAMP',
  'BYBIT',
  'CME',
  'COMEX',
  'ECONOMICS',
  'FX',
  'FX_IDC',
  'NASDAQ',
  'NYSE',
  'TVC',
  'TVCIX',
]);

export function normalizeEgxSymbol(symbol) {
  return String(symbol || '')
    .trim()
    .toUpperCase()
    .replace(/^EGX_DLY:/, '')
    .replace(/^EGX:/, '');
}

export function toTvSymbol(symbol) {
  const raw = String(symbol || '').trim();
  if (!raw) return raw;

  const upper = raw.toUpperCase();
  if (upper.startsWith(TV_EGX_PREFIX)) return `${TV_EGX_PREFIX}${normalizeEgxSymbol(raw)}`;
  if (upper.startsWith(LEGACY_EGX_PREFIX)) return `${TV_EGX_PREFIX}${normalizeEgxSymbol(raw)}`;

  if (upper.includes(':')) {
    const prefix = upper.split(':', 1)[0];
    if (KNOWN_NON_EGX_PREFIXES.has(prefix)) return raw;
    return raw;
  }

  return `${TV_EGX_PREFIX}${normalizeEgxSymbol(raw)}`;
}

export function fromTvSymbol(symbol) {
  return normalizeEgxSymbol(symbol);
}
