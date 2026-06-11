/**
 * Resolve which git remote to push to.
 * Default: drhusam (drhusam123 fork). Override: EGX_GIT_REMOTE=origin
 */
import { execSync } from 'child_process';
import { PROJECT_ROOT } from './load_env.mjs';

export function listRemotes() {
  try {
    const out = execSync('git remote', { cwd: PROJECT_ROOT, encoding: 'utf8' });
    return out.trim().split('\n').filter(Boolean);
  } catch {
    return [];
  }
}

export function resolvePushRemote() {
  const preferred = process.env.EGX_GIT_REMOTE?.trim();
  if (preferred) return preferred;
  const remotes = listRemotes();
  if (remotes.includes('drhusam')) return 'drhusam';
  if (remotes.includes('origin')) return 'origin';
  return remotes[0] || 'origin';
}

export function pushCommand(branch = 'main') {
  return `git push ${resolvePushRemote()} ${branch}`;
}
