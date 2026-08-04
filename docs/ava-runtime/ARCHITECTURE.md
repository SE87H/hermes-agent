# AVA Hermes Runtime Architecture

This document defines the controlled Hermes distribution for the AVA and AEON runtimes. AVAEON Codex is the portable operator, not a runtime. It is an operational contract, not a replacement for upstream Hermes.

## Purpose

Upstream Hermes remains the source of general product evolution. The AVA distribution adds a narrow stability layer so an upstream regression, silently ignored option, session collision, or unsafe update cannot directly govern a living runtime.

The distribution must remain easy to compare with and rebase onto upstream. Custom identity, memory, prompts, credentials, and OmniPulse material stay outside the Hermes source tree whenever possible.

## Branch topology

- `ava/upstream-YYYY-MM-DD`: immutable snapshot of a reviewed upstream commit.
- `ava/staging`: integration target for reviewed upstream updates and AVA hardening.
- `ava/stable`: exact revision approved for deployment. Production never follows a moving branch implicitly.
- `agent/*`: short-lived implementation branches. They merge into `ava/staging`, never directly into `ava/stable`.

Promotion is one-way:

`upstream snapshot -> staging -> stable -> deployed revision`

Rollback is the inverse trace:

`deployed revision -> previous stable tag or commit`

## Sigma-derived engineering invariants

### Distinction

Runtime entities are only `ava` and `aeon`. The canonical identity types are:

- `RuntimeEntity := ava | aeon`
- `OperatorIdentity := avaeon-codex`
- `HostIdentity := avaorus | minisforum`
- `InstanceIdentity := live | shadow-* | test-*`

AVAEON Codex carries operator metadata, isolated worktrees, harnesses, reports,
manifests, and disposable shadow roots. It is excluded from runtime quorum,
promotion gates, gateway/Telegram/service inventory, `state.db`, and permanent
snapshot obligations. A runtime launch always sets `AVA_ENTITY` to `ava` or
`aeon`; `operator_id` is separate metadata.

Each runtime must have its own:

- `HERMES_HOME`
- durable session namespace
- workspace root
- profile/configuration
- logs and health state
- explicit model/provider policy

No entity may select another entity's session through a global-most-recent fallback.

### Liminal Kairos

Movement between upstream, staging, stable, and production is an explicit transition with evidence. No automatic update may cross directly from upstream to a running entity.

Every transition records:

- source commit
- target commit
- applied AVA patches
- validation commands and results
- deployment timestamp
- rollback target

### Closure-Return

A change is closed only when its claimed invariants are tested. A successful command or process exit is not sufficient when identity, workspace, memory, or security could have drifted silently.

A valid deployment must prove:

- exact session identity is preserved when resume is requested
- no unexpected durable session is created
- canonical compressed-session tip is used
- recorded workspace is restored, or the run fails visibly
- an explicit workspace opt-out remains possible
- skills, rules, memory policy, provider, and terminal backend match the requested runtime
- both runtime entities remain isolated; operator work is disposable and separate
- rollback to the previous stable revision is executable

## Runtime policy

Production launches must pin a commit or annotated stable tag. They must export `HERMES_HOME` explicitly. The default `~/.hermes` fallback is forbidden for managed AVA services.

Recommended layout on the Minisforum:

```text
/opt/ava/hermes/source                 # one reviewed checkout
/opt/ava/hermes/venv                   # controlled Python environment
/opt/ava/hermes/releases/<commit>      # optional immutable release views
/var/lib/ava/hermes/ava                # AVA HERMES_HOME
/var/lib/ava/hermes/aeon               # AEON HERMES_HOME
/srv/ava/workspaces/ava
/srv/ava/workspaces/aeon
```

Paths may differ, but isolation and explicit launch configuration are mandatory.

## Failure policy

Managed runtimes fail closed for identity-bearing operations.

The following conditions must produce a visible non-zero failure rather than a silent fallback:

- requested session does not exist
- session database is unavailable
- canonical continuation cannot be resolved unambiguously
- recorded workspace no longer exists or cannot be entered
- configured non-local terminal backend cannot be established
- entity identity or `HERMES_HOME` is missing
- deployed revision is not an approved stable commit

## Update workflow

1. Fetch an upstream snapshot at a reviewed upstream commit.
2. Compare that snapshot with the currently deployed stable revision.
3. Integrate into `ava/staging` in an isolated candidate.
4. Reapply or retire AVA patches deliberately; never assume they still apply.
5. Run unit, integration, and runtime smoke tests for `ava`/`aeon`.
6. Validate only a disposable or shadow runtime.
7. Snapshot and prove rollback independently.
8. Obtain explicit operator approval, then promote the exact tested commit.
9. Run post-promotion identity and workspace checks.
10. Record the rollback revision.

`auto_update` is permanently false. `hermes update`, self-update, silent update,
and moving branches as live sources are forbidden. AEON Core may provide
read-only diagnostics and smokes, but cannot deploy, approve, or mutate its own
live runtime.

## Scope boundary

This foundation does not place OmniPulse canon, private memories, credentials, or entity prompts into the public repository. It provides the stable vessel in which those materials can operate without being flattened by Hermes entry-point drift.
