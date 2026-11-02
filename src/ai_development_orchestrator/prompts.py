from __future__ import annotations

from pathlib import Path

from .config import Config
from .models import Feature, State

MARKER = "[orchestrator feature={feature} step={step} spec_dir={spec_dir} round={round}]"
HANDOFF_SECTIONS = [
    "## Feature", "## Specification", "## Plan", "## Tasks", "## Project Instructions",
    "## Architecture Decisions", "## Acceptance Criteria", "## Validations", "## Restrictions",
]
CORRECTION_EXTRA_SECTION = "## Findings To Fix"
VALIDATION_COMMANDS_FILE = "validation-commands.json"
PREAMBLE = """You are the engineering agent of an autonomous development orchestrator working in this repository.
Rules:
- Work non-interactively. Never ask questions or wait for confirmation. When information is missing,
  choose the simplest option consistent with the project's conventions and record the decision in the
  artifact you write. When a Spec Kit skill asks for approval or answers, proceed with its
  recommended option.
- Do not modify application source code, tests or project configuration; a separate agent implements.
  Only create or edit the files this step asks for.
- Do not create, switch or delete Git branches and do not commit; the orchestrator manages Git.

Feature {id}: {title}
{description}
"""

SELECT_PROMPT = """Step: Select. Read-only: do not create or modify any file.
Review this feature against the other backlog features below and the current code base. Determine
whether it depends on backlog features that are not declared yet (declared: {declared}).
Other features in the backlog:
{backlog}
Reply with JSON only, as the last thing in your answer:
{{"dependencies": [{{"id": "<feature id>", "rationale": "<why this feature needs it first>"}}]}}
Use an empty list when there are no additional dependencies.
"""

SPECIFY_PROMPT = '''Step: Specify.
Use the Skill tool to run the `speckit-specify` skill with this feature description as its argument:
"{title}: {description}"
Do not create a Git branch. Make sure .specify/feature.json points to the feature directory you created.
'''

CLARIFY_PROMPT = """Step: Clarify. Feature directory: {spec_dir}
Use the Skill tool to run the `speckit-clarify` skill in autonomous mode: answer every question yourself
with the recommended option and record each question and answer in the spec's Clarifications section.
"""

PLAN_PROMPT = """Step: Plan. Feature directory: {spec_dir}
Use the Skill tool to run the `speckit-plan` skill. Base technical decisions on the existing code base,
AGENTS.md, CLAUDE.md and .specify/memory/constitution.md when present.
"""

TASKS_PROMPT = """Step: Tasks. Feature directory: {spec_dir}
Use the Skill tool to run the `speckit-tasks` skill. Include automated test tasks for every user story.
"""

ANALYZE_PROMPT = """Step: Analyse. Feature directory: {spec_dir}
Use the Skill tool to run the `speckit-analyze` skill. Then, without waiting for approval, fix every
CRITICAL and HIGH finding directly in spec.md, plan.md and tasks.md. Write the analysis report,
including the fixes you applied, to {spec_dir}/orchestrator/analysis.md.
"""

IMPLEMENT_HANDOFF_PROMPT = """Step: Implementation handoff. Feature directory: {spec_dir}
Write {spec_dir}/orchestrator/handoff-implement.md for a separate implementation agent that does NOT
share your context. It must be self-contained and contain these level-2 sections:
## Feature - feature id, title and feature directory.
## Specification - path of spec.md and a summary of the requirements.
## Plan - path of plan.md, key technical decisions and file layout.
## Tasks - path of tasks.md; implement every unchecked task in order and check each one off.
## Project Instructions - the relevant rules copied from AGENTS.md, CLAUDE.md and the constitution.
## Architecture Decisions - decisions and constraints the implementation must respect.
## Acceptance Criteria - a numbered, testable list derived from the spec.
## Validations - commands to run and make pass: {validation_commands}; plus the tests the tasks require.
## Restrictions - implement only this feature; do not commit, push or switch branches; do not modify
files under specs/ except checking off tasks in tasks.md; no destructive commands; no new
dependencies unless the plan requires them.
{validation_file}"""

VALIDATION_FILE_PROMPT = """Also write {spec_dir}/orchestrator/validation-commands.json: a JSON list of the shell commands
that prove this feature works and nothing else broke (tests, lint, type checks, build), taken from the
project's own build files and instructions. The orchestrator runs each one with `bash -c` from the
repository root during every review, so include any `cd` into a subproject. Keep them non-interactive
and free of side effects outside the repository; never include commands that start servers, deploy,
install packages or call external services.
"""

POST_ANALYZE_PROMPT = """Step: Post-implementation analysis. Feature directory: {spec_dir}
The implementation diff since the feature started is in {spec_dir}/orchestrator/diff-post.patch.
Use the Skill tool to run the `speckit-analyze` skill and extend the analysis to the implemented code:
compare the diff with spec.md, plan.md and tasks.md and report missing requirements, unchecked tasks,
defects and architectural inconsistencies. Do not modify any file except writing the report to
{spec_dir}/orchestrator/post-analysis.md.
"""

CONVERGE_PROMPT = """Step: Converge, review round {round}. Feature directory: {spec_dir}
Inputs written by the orchestrator (authoritative): implementation diff
{spec_dir}/orchestrator/diff-{round}.patch and validation results
{spec_dir}/orchestrator/validation-{round}.json. Also read {spec_dir}/orchestrator/post-analysis.md and
any earlier verdict files in {spec_dir}/orchestrator/.
1. Use the Skill tool to run the `speckit-converge` skill (it appends unbuilt work to tasks.md).
2. Review the implementation against every requirement and acceptance criterion in spec.md, the plan's
   architecture, test coverage, the validation results and changes outside the feature's scope.
3. Write {spec_dir}/orchestrator/verdict-{round}.json as valid JSON in exactly this shape:
{{"approved": true, "summary": "...", "findings": [{{"severity": "critical|major|minor", "description": "...", "location": "path:line or null"}}], "acceptance_criteria": [{{"criterion": "...", "met": true, "evidence": "..."}}]}}
Set "approved" to true only if every acceptance criterion is met, no critical or major finding remains,
converge appended no new tasks and every validation passed. Never rely on the implementation agent's
own claims.
"""

CORRECTION_PROMPT = """Step: Correction handoff {round}. Feature directory: {spec_dir}
Read {spec_dir}/orchestrator/verdict-{previous}.json and {spec_dir}/orchestrator/validation-{previous}.json.
Write {spec_dir}/orchestrator/handoff-correction-{round}.md for the implementation agent, which does
NOT share your context. Use the same level-2 sections as handoff-implement.md (## Feature,
## Specification, ## Plan, ## Tasks, ## Project Instructions, ## Architecture Decisions,
## Acceptance Criteria, ## Validations, ## Restrictions) plus ## Findings To Fix, listing each
problem with its location and the expected fix.
"""

STEP_PROMPTS = {
    "clarify": CLARIFY_PROMPT,
    "plan": PLAN_PROMPT,
    "tasks": TASKS_PROMPT,
    "analyze": ANALYZE_PROMPT,
    "implement.handoff": IMPLEMENT_HANDOFF_PROMPT,
    "post_analyze": POST_ANALYZE_PROMPT,
}


def marker(feature_id: str, step: str, spec_dir: str | None, round: int | None = None) -> str:
    return MARKER.format(feature=feature_id, step=step, spec_dir=spec_dir or "-", round=round or 0)


def engineering_prompt(
    step: str,
    feature: Feature,
    state: State,
    spec_dir: str | None,
    round: int = 0,
    cfg: Config | None = None,
    retry_note: str | None = None,
) -> str:
    base = marker(feature.id, step, spec_dir, round) + "\n" + PREAMBLE.format(
        id=feature.id, title=feature.title, description=feature.description
    )
    instructions = _configured_prompt(step, feature, spec_dir, state, round, cfg)
    prompt = base + (instructions if instructions is not None else _step_prompt(step, feature, state, spec_dir, round, cfg))
    return _append_retry_note(prompt, retry_note)


def _configured_prompt(
    step: str, feature: Feature, spec_dir: str | None, state: State, round: int, cfg: Config | None
) -> str | None:
    if cfg is None:
        return None
    phase = next((item for item in cfg.phases if item.id == step), None)
    if phase is None:
        return None
    if step in {*STEP_PROMPTS, "specify", "select", "converge", "correct.handoff"} or step.startswith(("converge.", "correct.handoff.", "implement")):
        return phase.prompt
    expected = ", ".join(phase.expect)
    directory = spec_dir or "-"
    return f"Step: {phase.id}. Feature directory: {directory}\n{phase.prompt}\nExpected outputs (relative to the feature directory): {expected}\n"


def _step_prompt(step: str, feature: Feature, state: State, spec_dir: str | None, round: int, cfg: Config | None) -> str:
    directory = spec_dir or "-"
    if step == "select":
        return SELECT_PROMPT.format(declared=_declared(feature), backlog=_backlog(feature, state))
    if step == "specify":
        return SPECIFY_PROMPT.format(title=feature.title, description=feature.description)
    if step in STEP_PROMPTS:
        return STEP_PROMPTS[step].format(
            spec_dir=directory, validation_commands=_validation_commands(cfg),
            validation_file=_validation_file(cfg, directory),
        )
    if step.startswith("converge."):
        return CONVERGE_PROMPT.format(spec_dir=directory, round=round)
    if step.startswith("correct.handoff."):
        return CORRECTION_PROMPT.format(spec_dir=directory, round=round, previous=round - 1)
    return _extra_phase(step, directory, cfg)


def _declared(feature: Feature) -> str:
    return ", ".join(item.id for item in feature.dependencies) or "none"


def _backlog(feature: Feature, state: State) -> str:
    return "\n".join(
        f"- {item.id}: {item.title} [{item.status.value}]" for item in state.features if item.id != feature.id
    )


def _validation_commands(cfg: Config | None) -> str:
    return "; ".join(cfg.validation.commands) if cfg and cfg.validation.commands else "the commands you list in validation-commands.json"


def _validation_file(cfg: Config | None, directory: str) -> str:
    return "" if cfg and cfg.validation.commands else VALIDATION_FILE_PROMPT.format(spec_dir=directory)


def _extra_phase(step: str, directory: str, cfg: Config | None) -> str:
    if cfg:
        phase = next((item for item in cfg.phases if item.id == step), None)
        if phase:
            expected = ", ".join(phase.expect)
            return f"Step: {step}. Feature directory: {directory}\n{phase.prompt}\nExpected outputs (relative to the feature directory): {expected}\n"
    return ""


def _append_retry_note(prompt: str, retry_note: str | None) -> str:
    if not retry_note:
        return prompt
    return prompt + f"\nYour previous attempt at this step was rejected by the orchestrator: {retry_note}\nFix that problem in this attempt.\n"


def opencode_prompt(
    step: str, feature: Feature, handoff_path: Path | str, spec_dir: str, round: int = 0,
    retry_note: str | None = None,
) -> str:
    prompt = f"""{marker(feature.id, step, spec_dir, round)}
You are the implementation agent. Read and follow the handoff file {handoff_path} exactly.
Only implement what it describes. Do not commit, push, switch branches or modify files under specs/ except checking off completed tasks in tasks.md. Run the validations listed in the handoff and fix failures before finishing. End with a short summary of what you changed and the validation results.
"""
    return _append_retry_note(prompt, retry_note)


def missing_sections(text: str, correction: bool = False) -> list[str]:
    required = HANDOFF_SECTIONS + ([CORRECTION_EXTRA_SECTION] if correction else [])
    return [section for section in required if section not in text]
