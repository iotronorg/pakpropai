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

## Session 4 — 2026-11-21 — M1 start
- **Focus:** the pasted prompt had no focus filled in, so the tracker's next task was taken (M1 kickoff → T‑1010, T‑1020).
- **Start state:** owner had committed sessions 1–3 (`d283324`); tree clean; baseline 1452/1/0.
- **Completed:** M1 milestone file; T‑1010 OTP hashing (ADR‑011); T‑1020 inbound WhatsApp retry/dead‑letter (ADR‑012). The outbound outbox was split out as T‑1021.
- **Tests:** 1477 passed, 1 skipped, 0 failed; no migration drift; Bandit 0 HIGH.
- **Found:**
  - Under DB errors the inbound task fell back to `org=None` (wrong tenant credentials).
  - An enqueue failure left the idempotency key set, so Meta's retry was dropped.
  - The OTP admin listed plaintext codes.
  - New issues logged: KI‑029/030/031.
- **Not committed.**

### Handoff → session 5
1. `git status`: session‑4 changes are uncommitted. **Do not commit.**
2. `scripts/dev/test.sh` → expect 1477 passed, 1 skipped.
3. Next: **T‑1030**. Write the membership consolidation ADR: inventory `OrganizationMembership`, `AgentOrgMembership`, `AgentOrganizationMembership`, `Agent.organization`, `Organization.admin_user`, and every reader of each (`get_user_org`, permissions, the `use_membership_rbac` flag). Choose the canonical model and a dual‑read/backfill plan. Then T‑1031 with permission‑matrix tests. KI‑030 can be fixed alongside.
