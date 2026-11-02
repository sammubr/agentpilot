# AgentPilot

AgentPilot orchestrates a backlog of features in a repository that uses GitHub Spec Kit. It decides
which feature runs next, which Spec Kit stage comes next and which agent runs it, and when to retry,
wait or stop. Spec Kit owns everything about each feature: AgentPilot never writes under `specs/` or
`.specify/` and keeps no copies or summaries of spec content.

## Overview

Each feature goes through seven Spec Kit stages:

```text
specify → clarify → plan → tasks → analyze → implement → converge
                                              ↑______________|  (while converge leaves open tasks)
```

Claude Code runs every stage except implement, which OpenCode runs. Each stage is delegated as a
one-line Spec Kit command, for example `/speckit-plan`, and the agent reads the original artifacts
itself. A feature is complete only when converge, run after the last implementation round, leaves
no open task (`- [ ]`) in `tasks.md`.

## Requirements

- Linux, Python 3.12 or newer, `uv`, and Git.
- Claude Code and OpenCode installed and authenticated.
- A Git repository initialized with Spec Kit: `.specify/`, the Claude skills
  `.claude/skills/speckit-{specify,clarify,plan,tasks,analyze,converge}` and the OpenCode command
  `.opencode/commands/speckit.implement.md`.

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

Control a long run from another terminal:

```bash
agentpilot status
agentpilot pause       # suspend after the current agent call; the process keeps waiting
agentpilot resume      # continue the paused run
agentpilot stop        # end the run now; progress is kept
agentpilot continue    # start a new run session from the kept progress
agentpilot retry F3    # requeue a blocked feature
```

Use `--repo PATH` before a command to operate on another repository, for example
`agentpilot --repo ~/src/project status --json`.

## Commands

| Command | Arguments and options | Purpose |
|---|---|---|
| `init` | `[--force]` | Create `.orchestrator/` with config, empty state and the event log. Existing config is refused unless forced. |
| `add` | `ID TITLE --description TEXT [--depends-on ID]...` | Append one pending feature. |
| `import` | `FILE` | Append features from YAML or JSON in file order. |
| `check` | | Check Git, Spec Kit commands, agent `--version`, config and backlog validity. |
| `run` | `[--max-features N]` | Start a run session. |
| `continue` | `[--max-features N]` | Start a new run session from the preserved progress, for example after `stop`. Each feature's stage is re-derived from its Spec Kit artifacts. |
| `pause` | | Suspend the active run after the current agent call. The process keeps the lock and waits. |
| `resume` | | Continue a paused run (same run id). If the paused process is gone, it is restarted. Refused after `stop`; use `continue`. |
| `stop` | | End the run, terminating the current agent call. Progress is kept. |
| `status` | `[--json]` | Show the run, current feature/stage/agent, unavailable agents and every feature's status. |
| `retry` | `ID` | Requeue a blocked feature with fresh attempt and round counters. |

Exit codes are 0 for success, 1 for operational or validation failure, 2 for command usage errors,
and 3 when another orchestrator holds the lock.

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
| `usage_limit_max_wait_s` | `21600` | Maximum single usage-limit wait; waiting repeats until the agent works again. |
| `usage_limit_margin_s` | `60` | Extra wait after a reported reset time. |
| `resume_sessions` | `true` | Resume the agent session when a stage is retried. |
| `[convergence].max_corrections` | `3` | Implement → converge rounds allowed after the first implementation before the feature is blocked. |
| `[git].allow_dirty_start` | `false` | Refuse a new run on a dirty working tree unless enabled. |
| `branch_per_feature` | `true` | Use a separate `<branch_prefix><feature-id>` branch per feature. |
| `branch_prefix` | `"orchestrator/"` | Prefix for feature branches. |
| `push` | `false` | Push completed feature branches when enabled. |
| `remote` | `"origin"` | Git remote used for configured pushes. |
| `[safety].allow_unrestricted` | `false` | Allow otherwise-rejected unrestricted agent permissions. Keep false unless intentional. |

Permission safeguards reject Claude's `bypassPermissions` mode and
`--dangerously-skip-permissions`, and reject an OpenCode Bash policy without a deny rule, unless
`safety.allow_unrestricted = true`. Numeric retry delays must be positive, the multiplier at least 1,
and `max_corrections` non-negative. Unknown keys, and the removed `[validation]` and `[[phases]]`
sections, are rejected.

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

Features run in backlog order once their dependencies are completed. A blocked feature does not stop
the run: features that do not depend on it continue.

## How a feature is processed

| Stage | Agent | Message sent | Required result |
|---|---|---|---|
| specify | Claude Code | `/speckit-specify <title>: <description>` | `.specify/feature.json` points to a new feature directory with `spec.md` |
| clarify | Claude Code | `/speckit-clarify` (answer questions with the recommended option) | `spec.md` |
| plan | Claude Code | `/speckit-plan` | `plan.md` |
| tasks | Claude Code | `/speckit-tasks` | `tasks.md` |
| analyze | Claude Code | `/speckit-analyze` (then apply fixes for the findings) | `tasks.md` |
| implement | OpenCode | Run `/speckit.implement` for `<spec_dir>` | `tasks.md` |
| converge | Claude Code | `/speckit-converge` | `tasks.md` |

Agents receive the feature directory through `SPECIFY_FEATURE_DIRECTORY`. After converge, open tasks
start another implement → converge round, up to `max_corrections`; then the feature is blocked.
Every stage that changed files is committed on the feature branch as `orchestrator(<id>): <stage>`.

Before each stage the next step is derived from the artifacts: a stage recorded as done whose
artifact is missing (for example a deleted `plan.md`) is repeated together with the stages after it.

## Recovery and usage limits

- **Usage limits.** The agent is recorded as unavailable until the reported reset time (plus a margin)
  or a growing fallback delay. No call is made to it before then, even across restarts, and the
  same stage is then repeated. Usage limits never block a feature.
- **Network and execution failures.** These are retried with bounded backoff. When attempts run
  out, the feature is blocked with the reason and the run continues with independent features.
- **Authentication failures.** These pause the run. Fix the credentials and run `resume`.
- **Interruptions.** After a crash, reboot or `stop`, `continue` repeats only the interrupted stage
  and keeps its partial changes.

## Safety model

Claude Code is configured for Spec Kit and spec-artifact work, with Git mutation and other risky shell
commands denied. OpenCode runs with `--standalone` so its configured permission rules apply, and
receives a permission map that denies destructive operations, Git history
mutation/push, publishing, and selected infrastructure commands. Review and adjust these permissions
for your environment; `safety.allow_unrestricted` disables the configuration guardrails.

The orchestrator owns Git checkpoints, feature branches, and optional pushes. Agents must not commit,
push, or switch branches. Unexpected edits, a moved Git HEAD or a branch switch by an agent pause the
run. `git.push` is disabled by default. A dirty working tree at startup is refused by default; enabling `allow_dirty_start` avoids
branch switching when the tree is dirty.

`.orchestrator/config.toml` is intentionally committed on feature branches, alongside the orchestrator's
checkpoints, so the configured workspace remains available when switching branches.

## Observability

`agentpilot status` (or `status --json`) is the only status view. `.orchestrator/logs/events.jsonl`
is the structured event stream; `agent_finished` events include the tail of each agent's output.
AgentPilot owns only `.orchestrator/`: `config.toml`, `state.json`, `lock`, the transient `control`
file and `logs/events.jsonl`.

## Long runs

Run interactively, or keep a run attached to a terminal multiplexer such as `tmux`. For detached
operation, use `nohup agentpilot continue >agentpilot.log 2>&1 &` and inspect it with `agentpilot
status`. A systemd user-unit example is in [`docs/agentpilot.service`](docs/agentpilot.service).

## Development and tests

```bash
uv sync
uv run pytest -q
uv run ruff check .
```

The automated tests use fake Claude and OpenCode commands and temporary Git repositories; they do not
call real AI providers.
