# Staged contract migrations — T‑1032b‑2 (ADR‑013 phase C)

Django does **not** load these files, because this directory is not `migrations/`. They finish phase C after T‑1032b‑1 (`organizations 0028`, `agents 0015`):

| File | What it does |
|---|---|
| `apps/organizations/contract_migrations/0029_drop_admin_user.py` | Gate, then drops `organizations.admin_user_id` and removes `admin_user` from the model state |
| `apps/agents/contract_migrations/0016_drop_agent_organization_memberships.py` | Gate, then drops the reader‑less `agent_organization_memberships` table |

Both gates refuse while `find_drift()` reports anything in `CONTRACT_BLOCKERS`:
- `missing_owner`
- `missing_agent`
- `stale_admin_projection`
- `unlinked_collaboration`
- `extra_owner`
- `collaboration_without_membership`
- `deprecated_agent_organization_memberships`

## When and how
1. **Release N+1** ships T‑1032b‑1. Let it run until no pod of release N or older is left anywhere (web, workers, beat, one‑off jobs). Older builds still read `admin_user`.
2. On production:
   1. Run `python manage.py reconcile_memberships`.
   2. Run `--apply` to fix the repairable categories.
   3. Decide on the report‑only ones:
      - `extra_owner`: keep one owner.
      - `collaboration_without_membership`: invite the agent, or end the terms.
      - `deprecated_agent_organization_memberships`: re‑create the rows as `AgentOrgMembership` terms if any are still needed, then delete them.
   4. Repeat until every blocker is 0. `orphan_agent` alone does not block.
3. **Release N+2**, in one change:
   - Move the two files into `apps/organizations/migrations/` and `apps/agents/migrations/`. If newer migrations exist by then, renumber the files and repoint the dependencies (`tests/test_membership_phase_c.py` checks this).
   - Make these code changes, which become mechanical because `tests/test_membership_phase_c.py::AdminUserReadersTest` keeps `admin_user` out of every other module:
     - `Organization.admin_user` field: delete it (`organizations/models.py`).
     - `signals.py`: remove `_org_snapshot` / `sync_owner_membership_on_save` and `_ORG_*`.
     - `membership.py`: delete `sync_owner_membership` and `sync_admin_projection`, and the calls to them in `set_owner` / `transfer_ownership`. Remove the `admin_user` handling and the `missing_owner` / `stale_admin_projection` categories in `find_drift`. Make `owner_of` stop falling back to `admin_user`.
     - Serializers: replace the `admin_user` field with an explicit `owner_id` write field (keep the `admin_user` output key for one more release if clients use it).
     - `admin.py`: remove `admin_user` from `raw_id_fields` and the fieldsets.
     - `tests/factories.py` (`make_developer`) and tests that pass `admin_user=`: use `membership.set_owner`.
4. Deploy N+2 and migrate. Each gate refuses on its own if drift came back.
5. Afterwards: delete these directories and this README.

**Rollback:** with the N+2 build, run `migrate organizations 0028` and `migrate agents 0015`. This restores the column (refilled from owner memberships) and an empty legacy table. Then roll the code back to N+1.

## Not done (optional follow‑ups)
- Making `AgentOrgMembership.membership` NOT NULL needs a model change in the same release. It is safe once `unlinked_collaboration` and `collaboration_without_membership` are 0.
- `Agent.organization` stays as the derived primary‑org pointer (owner decision, 2026‑11‑23).
