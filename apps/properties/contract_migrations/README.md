# Staged contract migrations — T-1070g-2

These are **not** loaded by Django (this directory is not `migrations/`).
They drop what T-1070g-1 (`properties 0040/0041`) stopped using:

| File | Does |
|---|---|
| `0042_drop_listing_v1_columns.py` | Gate (refuses unless `check_listing_contract` passes), drops the v1 mirror trigger and the columns `area_marla`, `area_unit`, `area_sqm`, `property_type` |
| `0043_drop_prop_fts_gin.py` | `DROP INDEX CONCURRENTLY prop_fts_gin` (non-atomic) |

## When and how

1. Release **N+1** ships g-1 (0040/0041). Let it run until no pod of release N
   or older is left anywhere (web, workers, beat, one-off jobs).
2. On production, run `python manage.py check_listing_contract`. It must
   print `OK`. If it reports drift, the trigger was bypassed — investigate
   before going on.
3. For release **N+2**, move both files into place:
   `git mv apps/properties/contract_migrations/004*.py apps/properties/migrations/`
   If other `properties` migrations were added in between, renumber these and
   point 0042's dependency at the latest one.
4. Deploy N+2; migrate with a direct (non-pooled) DB connection, because 0043
   is `CONCURRENTLY`. 0042 refuses on its own if the gate fails.
5. Afterwards: delete this directory, `listing_contract.py`'s trigger SQL and
   the `check_listing_contract` command (follow-up cleanup).

Rollback: **with the N+2 build** (an older build does not contain 0042/0043
and cannot unapply them), run `migrate properties 0041`. This restores the
columns (refilled from `size_*`/`category`), their index, the trigger and
`prop_fts_gin`. Then roll the code back to N+1.
