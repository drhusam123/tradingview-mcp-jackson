#!/usr/bin/env node
/**
 * Remove duplicate EGX cron lines (same # EGX-* marker).
 */
import { execSync } from 'child_process';

function dedupeCronLines(content) {
  const seen = new Set();
  let removed = 0;
  const out = content.split('\n').filter((line) => {
    const trimmed = line.trim();
    if (!trimmed) return true;
    const marker = trimmed.match(/#\s*(EGX-[^\s]+)/)?.[1];
    if (!marker) return true;
    if (seen.has(marker)) {
      removed += 1;
      return false;
    }
    seen.add(marker);
    return true;
  });
  return { text: out.join('\n'), removed, markers: seen.size };
}

const before = execSync('crontab -l 2>/dev/null', { encoding: 'utf8' });
const { text, removed, markers } = dedupeCronLines(before);
if (removed === 0) {
  console.log(`cron clean: ${markers} unique EGX markers, 0 duplicates`);
  process.exit(0);
}
execSync('crontab -', { input: `${text.trim()}\n` });
console.log(`cron deduped: removed ${removed} duplicate(s), ${markers} unique EGX markers`);
