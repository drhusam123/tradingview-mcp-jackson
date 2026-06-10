import { register } from '../router.js';
import * as core from '../../core/health.js';

register('status', {
  description: 'Check CDP connection to TradingView',
  handler: () => core.healthCheck(),
});

register('launch', {
  description: 'Launch TradingView with CDP enabled',
  options: {
    port: { type: 'string', short: 'p', description: 'CDP port (default 9222)' },
    'no-kill': { type: 'boolean', description: 'Do not kill existing instances' },
    browser: { type: 'string', short: 'b', description: 'desktop|chrome|web (default from TV_CDP_BROWSER)' },
    url: { type: 'string', short: 'u', description: 'TradingView URL to open in Chrome mode' },
  },
  handler: (opts) => core.launch({
    port: opts.port ? Number(opts.port) : undefined,
    kill_existing: !opts['no-kill'],
    browser: opts.browser,
    url: opts.url,
  }),
});
