# AgentPilot

AgentPilot is an autonomous development orchestrator for repositories that use GitHub Spec Kit. It
processes a dependency-aware feature backlog, keeps checkpoints in Git, and records run state and
results under `.orchestrator/`.

## Overview

Claude Code is the engineering agent: it selects dependencies and runs the Spec Kit specification,
clarification, planning, task, and review phases. OpenCode is the implementation agent: it implements
the approved handoff and later correction handoffs. AgentPilot coordinates the phases, validates
outputs, commits step checkpoints, retries recoverable failures, and stops honestly when work is
blocked. The eight processing phases are specify, clarify, plan, tasks, analyze, implementation,
post-implementation analyze, and converge.

## Requirements

- Linux, Python 3.12 or newer, `uv`, and Git.
- Claude Code and OpenCode installed and authenticated.
- A Git repository initialized with Spec Kit, including `.specify/` and the
  `.claude/skills/speckit-*` skills.

## Installation

Install the command-line tool:

```bash
uv tool install .
```

For development in this checkout:

```bash
uv sync
uv run agentpilot --help
```

Both `agentpilot` and `ai-development-orchestrator` invoke the same CLI. The module entry point
`python -m ai_development_orchestrator` does too.

## Quick start

From the target repository:

```bash
agentpilot init
$EDITOR .orchestrator/config.toml
agentpilot import backlog.yaml
agentpilot check
agentpilot run
```

Inspect and control a long run from another terminal:

```bash
agentpilot status
agentpilot pause       # pause at a step boundary or while waiting to retry
agentpilot resume
agentpilot retry F3
agentpilot report
```

Use `--repo PATH` before a command to operate on another repository, for example
`agentpilot --repo ~/src/project status --json`.

## Commands

| Command | Arguments and options | Purpose |
|---|---|---|
| `init` | `[--force]` | Create `.orchestrator/`, defaults, empty state, logs, and runs. Existing config is refused unless forced. |
| `add` | `ID TITLE --description TEXT [--depends-on ID]...` | Append one pending feature. |
| `import` | `FILE` | Append features from YAML or JSON in file order. |
| `check` | | Check Git, Spec Kit files, agent `--version`, config, and backlog validity. |
| `run` | `[--max-features N]` | Run eligible features; stops at completion, a halt, pause, or stop. |
| `resume` | `[--max-features N]` | Clear pause/stop requests and resume the current run. |
| `pause` | | Request a graceful pause. |
| `stop` | | Request a stop and signal the lock-holding orchestrator. |
| `status` | `[--json]` | Show current run, timing, attempts, retry, and backlog state. |
| `retry` | `ID [--from STEP]` | Requeue a blocked or failed feature, from the beginning or selected step. |
| `report` | `[--output PATH]` | Write a Markdown report (default `.orchestrator/report.md`). |

Exit codes are 0 for success, 1 for operational or validation failure, 2 for command usage errors,
and 3 when an operation is refused because another orchestrator holds the lock.

## Configuration reference

`agentpilot init` writes `.orchestrator/config.toml`. Defaults and meanings:

| Section/key | Default | Meaning |
|---|---|---|
| `[agents.claude].command` | `["claude"]` | Executable and arguments for Claude Code. |
| `model` | `""` | Optional model override; empty uses the CLI default. |
| `timeout_s` | `3600` | Maximum duration for one Claude call. |
| `permission_mode` | `"dontAsk"` | Claude permission mode; unrestricted bypass is rejected by default. |
| `allowed_tools` | `Read`, `Glob`, `Grep`, `Skill`, `TodoWrite`, `Write(specs/**)`, `Edit(specs/**)`, `Write(.specify/feature.json)`, `Edit(.specify/feature.json)`, `Bash(.specify/scripts/bash/*)`, `Bash(git status*)`, `Bash(git diff*)`, `Bash(git log*)`, `Bash(git show*)` | Claude's allowed tool list. |
| `disallowed_tools` | `Bash(git push*)`, `Bash(git commit*)`, `Bash(git reset*)`, `Bash(git checkout*)`, `Bash(git clean*)`, `Bash(git switch*)`, `Bash(git branch*)`, `Bash(rm *)`, `Bash(sudo *)` | Claude's explicitly disallowed tools. |
| `extra_args` | `[]` | Additional Claude CLI arguments. |
| `[agents.opencode].command` | `["opencode"]` | Executable and arguments for OpenCode. |
| `model` | `""` | Optional OpenCode model override. |
| `timeout_s` | `5400` | Maximum duration for one OpenCode call. |
| `extra_args` | `[]` | Additional OpenCode CLI arguments. |
| `[agents.opencode.permission].edit` | `"allow"` | OpenCode edit permission. |
| `[agents.opencode.permission].webfetch` | `"deny"` | OpenCode web-fetch permission. |
| `[agents.opencode.permission].external_directory` | `"deny"` | Access outside the repository. |
| `[agents.opencode.permission.bash]` | `"*" = "allow"`; explicit denies listed below | OpenCode Bash policy; at least one deny is required unless unrestricted use is explicitly enabled. |
| `[retry].initial_delay_s` | `30` | First exponential-backoff delay for retryable failures. |
| `multiplier` | `2.0` | Backoff multiplier, at least 1. |
| `max_delay_s` | `1800` | Maximum ordinary backoff delay. |
| `max_attempts_network` | `10` | Maximum consecutive network retry attempts. |
| `max_attempts_execution` | `3` | Maximum execution/timeout/invalid-output attempts. |
| `usage_limit_initial_delay_s` | `300` | Fallback wait when no usage reset time is available. |
| `usage_limit_max_wait_s` | `21600` | Maximum one usage-limit wait; polling continues afterwards. |
| `usage_limit_margin_s` | `60` | Extra wait after a reported reset time. |
| `resume_sessions` | `true` | Resume agent sessions on retries when a session ID is available. |
| `[convergence].max_corrections` | `3` | Maximum correction rounds before the feature is blocked. |
| `[validation].commands` | `[]` | Shell commands run during every convergence review. |
| `timeout_s` | `1800` | Timeout for each validation command. |
| `[git].allow_dirty_start` | `false` | Refuse a new run on a dirty working tree unless enabled. |
| `branch_per_feature` | `true` | Use a separate `<branch_prefix><feature-id>` branch per feature. |
| `branch_prefix` | `"orchestrator/"` | Prefix for feature branches. |
| `push` | `false` | Push completed feature branches when enabled. |
| `remote` | `"origin"` | Git remote used for configured pushes. |
| `[safety].allow_unrestricted` | `false` | Allow otherwise-rejected unrestricted agent permissions. Keep false unless intentional. |
| `[[phases]]` | none | Optional prompt overrides for built-in steps or extra Claude engineering steps. Extra steps run after analyze and before implementation; `expect` lists required output paths. |

Permission safeguards reject Claude's `bypassPermissions` mode and
`--dangerously-skip-permissions`, and reject an OpenCode Bash policy without a deny rule, unless
`safety.allow_unrestricted = true`. Numeric retry delays must be positive, the multiplier at least 1,
and `max_corrections` non-negative.

Set model overrides in `.orchestrator/config.toml` with `model = "..."` under either
`[agents.claude]` or `[agents.opencode]`; an empty value uses that CLI's default model.

The default OpenCode Bash deny rules are `rm -rf *`, `rm -r *`, `rm -fr *`, `sudo *`, `git push*`,
`git commit*`, `git reset*`, `git checkout*`, `git switch*`, `git clean*`, `git rebase*`,
`git merge*`, `git branch -D*`, `git tag*`, `npm publish*`, `uv publish*`, `twine *`,
`docker push*`, `kubectl *`, `helm *`, `terraform *`, `ssh *`, `scp *`, `chown *`, `mkfs*`, and
`dd *`.

## Backlog format

YAML, YML, or JSON input has a `features` list. Dependencies refer to feature IDs in the combined
backlog and must be acyclic.

```yaml
features:
  - id: F1
    title: User login
    description: Users can log in with email and password.
    depends_on: []
  - id: F2
    title: Profile page
    description: Logged-in users can edit their profile.
    depends_on: [F1]
```

Features run in backlog order when their dependencies have completed. Claude may propose additional
dependencies; they are stored with their rationale and can defer a feature until its dependency is
complete.

## How a feature is processed

| Step | Agent | Result |
|---|---|---|
| `select` | Claude Code | Review and record undeclared dependencies. |
| `specify`, `clarify`, `plan`, `tasks`, `analyze` | Claude Code | Create and review the Spec Kit artifacts. |
| Configured extra phases | Claude Code | Run in config order and verify expected files. |
| `implement.handoff` | Claude Code | Write a structured implementation handoff. |
| `implement.run` | OpenCode | Implement the handoff. |
| `post_analyze` | Claude Code | Review the implementation diff. |
| `converge.N` | Claude Code | Run validations, review evidence and approve or request correction. |
| `correct.handoff.N`, `correct.run.N` | Claude Code, OpenCode | Apply a requested correction before the next review. |

Each successful step is checkpointed in Git. A feature is completed only when the verdict is
approved and all configured validation commands pass. Rejected work is corrected up to the configured
limit, then marked blocked with the reason and findings.

## Recovery and usage limits

Transient network and execution failures are retried with bounded backoff. Usage-limit messages wait
until their reset time when available, with a configured margin and maximum wait per wait; usage-limit
retries have no attempt cap and continue waiting until the provider permits work. Authentication
failures pause the run. State is saved during transitions; after interruption, the current step is
repeated if it was not checkpointed. Use `resume` after fixing credentials or a pause/stop. Use
`retry ID` to preserve implementation while restarting review/correction, or `retry ID --from STEP`
to discard that step and all later completed steps. `--max-features` returns an idle status after the
requested number of completions.

## Safety model

Claude Code is configured for Spec Kit and spec-artifact work, with Git mutation and other risky shell
commands denied. OpenCode runs with `--standalone` so its configured permission rules apply, and
receives a permission map that denies destructive operations, Git history
mutation/push, publishing, and selected infrastructure commands. Review and adjust these permissions
for your environment; `safety.allow_unrestricted` disables the configuration guardrails.

The orchestrator owns Git checkpoints, feature branches, and optional pushes. Agents must not commit,
push, or switch branches. Unexpected edits or a moved Git HEAD pause the run. `git.push` is disabled by
default. A dirty working tree at startup is refused by default; enabling `allow_dirty_start` avoids
branch switching when the tree is dirty.

`.orchestrator/config.toml` is intentionally committed on feature branches, alongside the orchestrator's
checkpoints, so the configured workspace remains available when switching branches.

## Observability

`agentpilot status` reports run status/reason, lock state, backlog position, current feature and step,
elapsed times, attempts, last error, next retry, and feature statuses. Add `--json` for structured
data. `.orchestrator/logs/events.jsonl` is the structured event stream. `.orchestrator/runs/` contains
per-attempt prompts and agent output. `.orchestrator/report.md` is updated at the end of every run;
`agentpilot report --output PATH` writes a report elsewhere.

## Long runs

Run interactively, or keep a run attached to a terminal multiplexer such as `tmux`. For detached
operation, use `nohup agentpilot resume >agentpilot.log 2>&1 &` and inspect it with `agentpilot
status`. A systemd user-unit example is in [`docs/agentpilot.service`](docs/agentpilot.service).

## Development and tests

```bash
uv sync
uv run pytest -q
uv run ruff check .
```

The automated tests use fake Claude and OpenCode commands and temporary Git repositories; they do not
call real AI providers.
