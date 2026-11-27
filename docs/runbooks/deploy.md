# Runbook: deploys

Staging and production are each a server with Docker (owner decision 2026‑11‑25, ADR‑053). A push to `main` that passes every check:

1. builds the web and worker images once, tagged with the commit (`ghcr.io/<repo>/web:<sha>`);
2. deploys them to **staging** automatically and smoke-tests the public URL;
3. waits for a **reviewer to approve** the production deployment in GitHub;
4. deploys **the same images** to production and smoke-tests it.

On each server `remote_deploy.sh` pulls the images, starts the database and Redis, migrates, runs the release check (`manage.py check_release`), and starts the new containers. It then waits until `/health/` reports the new version. If health never comes, it starts the previous version again and the run fails. A failed migration stops it before anything switches.

## One-time setup

### A server (staging and production alike)
1. A Linux host with Docker Engine and the Compose plugin. Open ports 80/443 to the public, via your HTTPS proxy in front of `WEB_PORT`, and SSH only from where you need it.
2. A deploy user in the `docker` group, with an SSH key used for nothing else (`ssh-keygen -t ed25519 -f realtron-deploy`).
3. A directory, e.g. `/srv/realtron`, holding the `.env` built from `.env.example` with production values. CI never copies `.env`. Include `localhost` in `ALLOWED_HOSTS`: the health probe inside the container uses it.
4. Set a fixed `FIELD_HASH_KEY` (checklist §53).
5. Set `ENVIRONMENT=production` (or `staging`) and the other settings the release check needs. Run it before the first deploy: `docker compose -p realtron --env-file .env -f docker/docker-compose.prod.yml run --rm --no-deps migrate python manage.py check_release`.

### GitHub
1. **Settings → Environments:** create `staging` and `production`.
2. **production → Required reviewers:** add at least one person. Optionally tick *Prevent self-review* and restrict deployments to the `main` branch. CI refuses to deploy production while no reviewer is required. Required reviewers need GitHub Team or Enterprise for a private repository.
3. In **each** environment, add these secrets:
   - `SSH_HOST`, `SSH_USER`, `SSH_PRIVATE_KEY`.
   - `SSH_KNOWN_HOSTS`: the output of `ssh-keyscan -t ed25519 <host>`. Compare it with the server's own key (`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`).
   - `DEPLOY_DIR`.
   - `REGISTRY_USER` and `REGISTRY_TOKEN`: a token with `read:packages` only.
   - `SMOKE_WA_VERIFY_TOKEN` (optional).
4. In each environment, add the variable `PUBLIC_URL` (`https://…`). Add `COMPOSE_EXTRA_FILES` if that server also runs other compose files in the same project.
5. If the production gate says it cannot read the environment, add a repository secret `ENVIRONMENTS_READ_TOKEN`: a fine-grained token with read access to *Environments* (or *Administration*) of this repository.

## Approving a production deploy
Open the run (Actions → *RealTron AI — CI/CD*) → *Review deployments* → tick `production` → **Approve**. Before you approve, check:
- the staging job is green;
- you have looked at staging;
- no alert is firing ([README.md](README.md));
- the release has no step listed in GO_LIVE_CHECKLIST that must happen first.

Reject a deploy to skip it. A newer commit gets its own run.

## When a deploy fails
- **"missing secrets or variables":** add the named items to that environment.
- **Migration failed:** nothing switched. Read the job log; fix and push. Migrations must stay expand-only (ADR‑023) so the previous build keeps working on the new schema.
- **Release check failed:** nothing switched (migrations already ran; they are expand-only). The log lists each `FAIL` by setting name, never its value: fix `.env` or the platform setting and deploy again. `FAIL frozen.<flag>` means a module that is frozen for the MVP (deal lock, voice calls, syndication, portal scraping) is on in production: switch it off in the admin unless the owner has lifted the freeze (MVP_SCOPE §4.2). `WARN` lines do not block; `--strict` makes them fail. Builds older than the check skip it, so rollbacks still deploy.
- **Did not become healthy → rolled back:** the previous version runs again. On the server, `docker compose -p realtron --env-file .env -f docker/docker-compose.prod.yml logs web --tail 200` shows why. `/health/` lists each check: database, Redis, AI key.
- **Smoke test failed after a healthy deploy:** the build is live but the public route is wrong (proxy, TLS, `ALLOWED_HOSTS`) or the webhook accepted an unsigned call. Roll back by hand if users are affected.

## Rolling back by hand
On the server, in `DEPLOY_DIR`, with the tag of the build to return to (`cat .deployed_tag` shows the current one; earlier tags are in the Actions history):

```sh
IMAGE_TAG=<previous sha> WEB_IMAGE=ghcr.io/<repo>/web WORKER_IMAGE=ghcr.io/<repo>/worker sh remote_deploy.sh
```

This runs the same steps and checks. It does not reverse migrations; that is safe because they are expand-only.
