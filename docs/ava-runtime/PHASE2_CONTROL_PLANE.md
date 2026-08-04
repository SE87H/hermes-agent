# AVA Hermes Phase 2 Control Plane

Phase 2 turns the foundation into a fleet-level operating surface. It remains an overlay: no upstream Hermes file is modified, and no production branch is advanced automatically.

## 1. Install the non-secret fleet map

Copy the public example outside the source checkout so the reviewed tree remains clean:

```bash
sudo install -d -m 0750 -o "$USER" -g "$USER" /etc/ava/hermes
sudo install -m 0640 -o "$USER" -g "$USER" \
  config/ava-runtime/entities.example.yaml \
  /etc/ava/hermes/entities.yaml
export AVA_FLEET_CONFIG=/etc/ava/hermes/entities.yaml
```

Replace `source.expected_ref` with the exact 40-character commit under test. Moving branches such as `ava/stable` are deliberately rejected by the validator.

## 2. Validate the entire fleet before launching anything

```bash
uv run python scripts/ava_runtime/fleet.py validate
```

The validator refuses:

- an incomplete AVA/AEON runtime fleet;
- shared or nested `HERMES_HOME` roots;
- shared or nested workspaces;
- a workspace inside any entity's state root;
- duplicate profiles or session scopes;
- source, snapshot, workspace, or state roots that overlap;
- unknown YAML fields or schema versions;
- `auto_update: true`;
- a moving or abbreviated deployment reference;
- any lowering of the canonical fail-closed policy.

## 3. Render or consume one entity environment

For inspection:

```bash
uv run python scripts/ava_runtime/fleet.py env aeon
```

For a managed one-shot invocation:

```bash
AVA_OPERATOR_ID=avaeon-codex uv run python scripts/ava_runtime/fleet.py oneshot aeon -- \
  --resume <SESSION_ID> \
  "Continue the work"
```

The fleet launcher sets `AVA_ENTITY`, `HERMES_HOME`, `AVA_WORKSPACE`, the reviewed repository and commit, then executes the narrow managed one-shot surface without a shell.

## 4. Run fleet preflight and smoke tests

```bash
uv run python scripts/ava_runtime/fleet.py doctor all --require-state-db

uv run python scripts/ava_runtime/fleet.py smoke ava
uv run python scripts/ava_runtime/fleet.py smoke aeon
```

Smoke tests use disposable state by default. `--live-state` is an explicit, visible opt-in and must not be used for the first validation pass.

## 5. Create verified state snapshots

Before any migration or deployment:

```bash
uv run python scripts/ava_runtime/fleet.py snapshot all
```

Each snapshot uses SQLite's online backup API, performs `PRAGMA integrity_check`, stores a SHA-256 manifest, and sets directory/file modes to `0700/0600`. It contains only `state.db`; credentials, configuration files, logs, caches, prompts, and other `HERMES_HOME` content are not copied.

Verify a snapshot independently:

```bash
uv run python scripts/ava_runtime/state_snapshot.py verify \
  /var/backups/ava/hermes-state/<SNAPSHOT_DIRECTORY>
```

Snapshot storage must itself be protected and included in the vessel's encrypted backup policy.

## 6. Produce the promotion evidence

Copy the deliberately failing template outside the source checkout:

```bash
cp config/ava-runtime/validation-report.example.json \
  /var/lib/ava/promotion/validation-report.json
```

Fill it only with public command/status metadata. Never paste transcripts, prompts, API keys, tokens, or private OmniPulse material.

Promotion requires all of the following to be `pass`:

- `tests.ava_runtime`;
- `tests.hermes_cli`;
- `tests.upstream_relevant`;
- doctor, identity smoke, and shadow runtime for AVA;
- doctor, identity smoke, and shadow runtime for AEON;
- operator metadata is recorded separately and is not a runtime gate;
- an empty `remaining_gaps` array;
- a candidate commit exactly equal to checkout `HEAD`;
- a clean working tree;
- a resolvable rollback target different from the candidate.

Generate the evidence manifest:

```bash
uv run python scripts/ava_runtime/promotion.py prepare \
  --config "$AVA_FLEET_CONFIG" \
  --report /var/lib/ava/promotion/validation-report.json \
  --manifest /var/lib/ava/promotion/promotion-manifest.json
```

Re-verify immediately before promotion or deployment:

```bash
uv run python scripts/ava_runtime/promotion.py verify \
  --config "$AVA_FLEET_CONFIG" \
  --report /var/lib/ava/promotion/validation-report.json \
  --manifest /var/lib/ava/promotion/promotion-manifest.json
```

Any later change to the checkout, fleet YAML, validation report, candidate tree, or rollback target invalidates the manifest.

## 7. Promotion boundary

The control plane never moves `ava/stable`, creates a stable tag, or restarts a service. Those remain explicit operator actions after the manifest verifies. The first deployment proceeds one entity at a time, with doctor and smoke rerun after each restart.
