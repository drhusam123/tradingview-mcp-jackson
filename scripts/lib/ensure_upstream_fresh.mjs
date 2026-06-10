/**
 * ML/upstream auto-remediation before client live send.
 */
import { execSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import {
  getUpstreamDates, upstreamIssues, logDeliveryAttempt,
} from './delivery_audit.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '../..');
const NODE = process.execPath;
const PY = process.env.PYTHON_BIN || process.env.PYTHON3 || '/usr/bin/python3';

const ML_COMMANDS = (date) => [
  { cmd: `"${NODE}" scripts/egx_explosion_ml.mjs predict --date ${date} --top-n 20`, label: 'explosion_ml predict' },
  { cmd: `"${PY}" scripts/python/egx_ml_trainer.py predict_ensemble`, label: 'predict_ensemble' },
];

function runCmd(cmd, label) {
  try {
    execSync(cmd, { cwd: ROOT, stdio: 'pipe', encoding: 'utf8', timeout: 600_000 });
    return { ok: true, label };
  } catch (e) {
    return { ok: false, label, error: e.message?.slice(0, 300) };
  }
}

/**
 * @param {string} signalDate
 * @param {{ autoRemediate?: boolean, logAudit?: boolean }} opts
 */
export function ensureUpstreamFresh(signalDate, opts = {}) {
  const { autoRemediate = true, logAudit = true } = opts;
  const before = getUpstreamDates();
  let issues = upstreamIssues(signalDate);

  if (issues.length === 0) {
    return {
      ok: true,
      remediated: false,
      dates: getUpstreamDates(),
      issues: [],
    };
  }

  const mlStale = !before.ml_pred || before.ml_pred < signalDate;
  const requiredCommands = ML_COMMANDS(signalDate).map(c => c.cmd);
  const remediationLog = [];

  if (autoRemediate && mlStale) {
    for (const step of ML_COMMANDS(signalDate)) {
      const r = runCmd(step.cmd, step.label);
      remediationLog.push(r);
    }
    issues = upstreamIssues(signalDate);
  }

  const after = getUpstreamDates();
  const stillMlStale = !after.ml_pred || after.ml_pred < signalDate;

  if (issues.length > 0) {
    const skipReason = stillMlStale ? 'ML_STALE' : 'UPSTREAM_STALE';
    const auditRow = {
      signal_date: signalDate,
      actionable: 0,
      message_generated: 0,
      send_attempted: 0,
      send_success: 0,
      skip_reason: `${skipReason}: expected=${signalDate} ml_latest=${after.ml_pred ?? 'none'}`,
      pipeline_stage: 'upstream_block',
      ml_latest_date: after.ml_pred,
      required_ml_date: signalDate,
      meta_json: {
        expected_date: signalDate,
        latest_ml_date: after.ml_pred,
        latest_scan_date: after.scan,
        latest_meta_date: after.meta,
        required_commands: requiredCommands,
        remediation_log: remediationLog,
        upstream_issues: issues,
      },
    };
    if (logAudit) logDeliveryAttempt(auditRow);
    return {
      ok: false,
      remediated: remediationLog.some(r => r.ok),
      dates: after,
      issues,
      skip_reason: skipReason,
      required_commands: requiredCommands,
      audit_id: logAudit ? undefined : null,
    };
  }

  return {
    ok: true,
    remediated: remediationLog.some(r => r.ok),
    dates: after,
    issues: [],
    remediation_log: remediationLog,
  };
}
