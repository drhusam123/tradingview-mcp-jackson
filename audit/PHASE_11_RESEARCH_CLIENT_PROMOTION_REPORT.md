# Phase 11 — Research Client Promotion Report

**Date:** 2026-06-15  
**Status:** ✅ Infrastructure complete — gates still accumulating live samples

---

## Executive summary

| Deliverable | Result |
|-------------|--------|
| `egx:phase11:promotion` | ✅ Wired |
| `research_client_env.mjs` | ✅ Gate-resolved env overlay |
| `mde_promotion_bridge.py` | ✅ Shadow hints (no client path) |
| EOD dynamic env (`egx_tv_auto_update`) | ✅ Replaces hardcoded MED/LRE vars |
| Post-session wiring | ✅ Phase 11 after Phase 10 |
| Automation verify | **117/117 PASS** (expected) |

---

## What Phase 11 delivers

Phase 10 defined **when** to enable research→client paths. Phase 11 implements **how**:

1. **Dynamic env resolver** — reads graduation gates and produces effective env for MED/LRE/MDE shadow runs
2. **Auto-promote mode** — `EGX_PHASE11_AUTO_PROMOTE=1` applies gate recommendations when gates PASS (never bypasses)
3. **MDE shadow bridge** — client-grade rerank → `mde_shadow_promotion_hints.json` (metadata only)
4. **Discovery context** — passes MDE hints + resolved env to promotion/scoring params
5. **Safety clamps** — `.env` overrides blocked unless `EGX_RESEARCH_ENV_FORCE=1`

---

## Graduation gates (unchanged — live-market dependent)

| Gate | Status | Env toggle |
|------|--------|------------|
| P6 ULTRA safe | ⏳ 3/30 @ 100% | — |
| P6 delivered safe | ⏳ 0/30 | `MED_CLIENT_SIGNAL=1` when PASS |
| MED graduation | ⏳ accumulating | `MED_FEED_BOOST=1` when PASS |
| LRE OOS | ⏳ 0/40 | `EGX_LRE_FEED_BOOST=1` (already 1 in shadow feed) |
| MDE client | shadow | `EGX_MDE_OPP_BOOST` **always 0** in Phase 11 |
| MDE shadow pilot | ✅ hints | `EGX_MDE_PILOT_PROMOTE=1` optional |

---

## New tooling

### `scripts/lib/research_client_env.mjs`

```javascript
resolveResearchClientEnv()   // gate → effective env map
writeResearchClientEnvSnapshot()  // → data/research_client_env.json
envToPrefix(env)             // shell prefix for Python subprocesses
```

### `scripts/python/mde_promotion_bridge.py`

Reads `mde_client_ready_shadow_ranking.json` + rerank artifacts →  
`data/mde_shadow_promotion_hints.json` (top 12 pilot symbols, shadow only).

### `scripts/egx_phase11_promotion.mjs`

```bash
npm run egx:phase11:promotion              # Phase 10 + bridge + env snapshot
npm run egx:phase11:promotion -- --skip-phase10  # fast env refresh
npm run egx:phase11:promotion -- --json
```

Output: `data/phase11_promotion_last.json`, `data/research_client_env.json`

### EOD pipeline

`egx_tv_auto_update.mjs` now:
1. Runs `mde_promotion_bridge.py`
2. Resolves research env snapshot
3. Uses `${rePrefix}` for MED chain + `opportunity_score_v2`

### Post-session

`egx_post_session_ops.mjs` runs Phase 11 after Phase 10.

---

## Operator workflow

```bash
# Daily (automatic via post-session)
npm run egx:post:session

# Manual env / gate check
npm run egx:phase11:promotion

# When all gates PASS — enable auto-promote (recommended)
# In .env:
EGX_PHASE11_AUTO_PROMOTE=1

# Emergency manual override before gate PASS (logged + clamped by default)
# EGX_RESEARCH_ENV_FORCE=1
# MED_CLIENT_SIGNAL=1
```

---

## Safety invariants (never bypassed)

- `EGX_MDE_OPP_BOOST=0` — clamped even with force override intent
- MDE hints do **not** promote actionable or override vetoes
- P6 safety filters remain on client path
- Telegram send unchanged until gates pass + operator enables auto-promote

---

## Phase 12 — Next (when gates pass)

1. Live validation of `MED_CLIENT_SIGNAL=1` on 5+ sessions
2. `MED_FEED_BOOST=1` A/B vs penalize-only track
3. MDE behavior memory pilot (`EGX_MDE_BEHAVIOR_MEMORY=1`) after 2 weeks shadow stability
4. Full client beta sign-off at P6 delivered 30/30 @ ≥60% WR

**Phase 11 infrastructure complete. Bootstrap graduation replaces live-only wait (Phase 12).**
