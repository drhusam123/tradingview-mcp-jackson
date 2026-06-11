/** Parse JSON from Python stdout (handles log lines + multiline JSON). */
export function parsePythonJson(out) {
  const text = String(out || '').trim();
  const start = text.indexOf('{');
  const end = text.lastIndexOf('}');
  if (start >= 0 && end > start) {
    try {
      return JSON.parse(text.slice(start, end + 1));
    } catch { /* fall through */ }
  }
  for (const line of text.split('\n').reverse()) {
    const t = line.trim();
    if (t.startsWith('{') || t.startsWith('[')) {
      try {
        return JSON.parse(t);
      } catch { /* continue */ }
    }
  }
  throw new Error(`No JSON in python output: ${text.slice(-300)}`);
}
