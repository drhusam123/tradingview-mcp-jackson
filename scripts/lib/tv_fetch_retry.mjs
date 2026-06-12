/**
 * Unified TV/CDP fetch retry — timeout → retry ×2 with backoff.
 */
export async function withTvRetry(fn, { retries = 2, backoffMs = 1500, label = 'tv' } = {}) {
  let lastErr;
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      return await fn(attempt);
    } catch (err) {
      lastErr = err;
      if (attempt < retries) {
        await new Promise(r => setTimeout(r, backoffMs * (attempt + 1)));
      }
    }
  }
  throw lastErr ?? new Error(`${label}: fetch failed after ${retries + 1} attempts`);
}
