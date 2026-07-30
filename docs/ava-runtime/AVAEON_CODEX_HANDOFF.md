# AVAEON Codex Minisforum Handoff

This runbook begins only after a commit has been reviewed on `ava/staging`. Do not replace the live Hermes installation while inspecting it.

## 1. Capture the current vessel

Record without publishing secrets:

```bash
hostnamectl
uname -a
python3 --version
uv --version || true
hermes --version || true
command -v hermes || true
systemctl --user list-units --type=service | grep -i hermes || true
systemctl list-units --type=service | grep -i hermes || true
```

For every running entity, record:

- launch command or service unit
- current source checkout and commit
- `HERMES_HOME`
- workspace
- profile
- model/provider names, without credentials
- state database path
- log path

Do not copy `.env`, API keys, OAuth tokens, session transcripts, or private OmniPulse files into GitHub.

## 2. Create isolated state and workspace roots

Example:

```bash
sudo install -d -m 0750 -o "$USER" -g "$USER" \
  /var/lib/ava/hermes/ava \
  /var/lib/ava/hermes/aeon \
  /var/lib/ava/hermes/avaeon-codex \
  /srv/ava/workspaces/ava \
  /srv/ava/workspaces/aeon \
  /srv/ava/workspaces/avaeon-codex
```

Existing state must be backed up before migration. Never point two managed entities at the same `HERMES_HOME`.

## 3. Prepare a separate reviewed checkout

```bash
sudo install -d -m 0755 -o "$USER" -g "$USER" /opt/ava/hermes
git clone https://github.com/SE87H/hermes-agent.git /opt/ava/hermes/source
cd /opt/ava/hermes/source
git fetch --all --tags --prune
git checkout --detach <APPROVED_STAGING_COMMIT>
```

Use detached HEAD or an exact stable tag for validation. Do not run production from a moving remote branch. The existence of the `ava/stable` branch alone is not approval; only a recorded, tested promotion commit is deployable.

## 4. Build the controlled environment

Follow the upstream installation method appropriate to the vessel, but place the environment outside the existing live installation. Example:

```bash
cd /opt/ava/hermes/source
uv sync --frozen
```

If the lockfile or dependencies cannot be reproduced, stop. Do not repair the live service in place.

## 5. Run static and focused tests

```bash
uv run pytest -q tests/ava_runtime
uv run pytest -q tests/hermes_cli
```

Then run any upstream suite required by the changed files. Record commands, commit, and results.

## 6. Run the AVA doctor for each entity

Example for AVAEON Codex:

```bash
export AVA_ENTITY=avaeon-codex
export AVA_HERMES_REPO=/opt/ava/hermes/source
export AVA_WORKSPACE=/srv/ava/workspaces/avaeon-codex
export HERMES_HOME=/var/lib/ava/hermes/avaeon-codex
export AVA_HERMES_EXPECTED_REF=<APPROVED_STAGING_COMMIT>

uv run python scripts/ava_runtime/doctor.py \
  --require-clean \
  --expected-ref "$AVA_HERMES_EXPECTED_REF"
```

Repeat with the corresponding paths for AVA and AEON.

## 7. Run the real session-identity smoke test

Use the actual configured local/provider runtime, preferably on a disposable state root first. The default mode exercises the managed overlay rather than the defective upstream `hermes -z` dispatch:

```bash
uv run python scripts/ava_runtime/smoke_session_identity.py \
  --entity avaeon-codex \
  --workspace /srv/ava/workspaces/avaeon-codex
```

For a deliberate comparison against upstream:

```bash
uv run python scripts/ava_runtime/smoke_session_identity.py \
  --mode upstream \
  --hermes-command "uv run hermes" \
  --entity avaeon-codex \
  --workspace /srv/ava/workspaces/avaeon-codex
```

Required managed result:

```text
STATUS_CLOSURE=PASS
mode=managed
context_restored=true
stable_session_id=true
no_session_fork=true
```

A failure must not be bypassed by manually copying session data or selecting the newest global session.

## 8. Managed oneshot launch command

Until the upstream dispatch carries the same tested contract, identity-bearing one-shot services use:

```bash
uv run python -m hermes_cli.ava_runtime.managed_oneshot \
  --resume <SESSION_ID> \
  "<PROMPT>"
```

Other supported selections are explicit:

```bash
# Continue a session by exact ID or title
uv run python -m hermes_cli.ava_runtime.managed_oneshot \
  --continue "<ID_OR_TITLE>" "<PROMPT>"

# Continue latest session in AVA_WORKSPACE only
uv run python -m hermes_cli.ava_runtime.managed_oneshot \
  --continue-last "<PROMPT>"
```

The launcher rejects unknown options and refuses upstream signature drift. It must not be silently replaced with `hermes -z` in a service unit.

## 9. Shadow launch

Launch one disposable/shadow instance with separate ports, logs, and `HERMES_HOME`. Validate:

- correct entity profile
- correct workspace
- correct provider/model
- terminal backend
- skills and memory policy
- Telegram or other gateway routing
- cron jobs without duplication
- restart persistence
- clean shutdown and session closure

## 10. Promote and deploy

Only after all evidence passes:

1. record the exact staging commit
2. advance `ava/stable` to that commit through a reviewed promotion
3. create a stable tag
4. stop one entity at a time
5. back up its state
6. switch the service to the pinned stable commit
7. restart and rerun the doctor and smoke checks

AVA, AEON, and AVAEON Codex must not be migrated simultaneously on the first deployment.

## 11. Rollback

Before deployment, record:

```text
previous_source_commit
previous_environment_path
previous_service_unit
state_backup_path
```

Rollback means restoring the previous code and compatible state snapshot, restarting the entity, and rerunning the doctor. An apology, successful process start, or apparently coherent response is not proof of restoration.

## Result report

Return a compact machine-readable report containing:

```yaml
host:
source_before:
source_tested:
tests:
doctor:
identity_smoke:
shadow_runtime:
state_backups:
rollback_ref:
remaining_gaps:
status_closure:
```
