# Session History

## Session 1 — 2026-11-21 — Product assessment & PRD
- Created 11 documents in `docs/product/ai-real-estate-agent/`. No source changes.
- Tests not executed: the host had no Python 3.12 and no Django.

## Session 2 — 2026-11-21 — M0 stabilisation, first pass
- **Objective:** set up tracking and a test baseline, then fix M0 P0 issues.
- **Completed:** T‑0002, T‑0004, T‑0005, T‑0006, T‑0007, T‑0008, T‑0012; partial T‑0003 and T‑0015.
- **Tests:** baseline 1361 passed, 1 skipped, 1 failed → end 1425 passed, 1 skipped, 0 failed (executed in Docker).
- **Found:**
  - `list_property` constraint bug (KI‑021): WhatsApp self‑listing never worked.
  - Test cache leakage (KI‑022).
  - The assessment wrongly called `use_membership_rbac` unused (corrected in KI‑010).
- **Not committed.**

### Handoff → session 3
1. Start with `git status`. All session‑2 work is uncommitted on `development`; do not discard it.
2. Run `scripts/dev/test.sh`. Expect 1425 passed, 1 skipped.
3. Next: T‑0009 (wire `ComplianceEngine` from `apps/ai/compliance_filters.py` into `AIServiceManager.process` after the output guard, then add tests). Then T‑0017 and T‑0013.
4. Open owner questions: see DEVELOPMENT_STATUS → Blockers.

## Session 3 — 2026-11-21 — M0 completion
- **Owner instruction:** no commits; all other decisions approved.
- **Completed:** T‑0003, T‑0009, T‑0010, T‑0011, T‑0013, T‑0014, T‑0015, T‑0016, T‑0017. All M0 tasks are now done in code.
- **Tests:** 1452 passed, 1 skipped, 0 failed. The migration rehearsal passed forward/reverse.
- **Found and fixed:** WhatsApp audits always failed (KI‑026); non‑constant‑time token compare (KI‑027).

### Handoff → session 4
1. `git status`: everything is uncommitted on `development`, including 7 deleted artifact files. **Do not commit** (owner rule).
2. `scripts/dev/test.sh` → expect 1452 passed, 1 skipped.
3. M0 exit is pending only a GitHub Actions run (owner pushes). Start M1: create `milestones/MILESTONE_01_CORE_FOUNDATION.md` from PRD Phase 1, then T‑1010 OTP hashing.
