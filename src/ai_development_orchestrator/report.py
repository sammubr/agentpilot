from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .backlog import next_feature, waiting_on
from .models import Feature, FeatureStatus, State, utcnow
from .workspace import Workspace


def status_data(workspace: Workspace, state: State, now: datetime) -> dict:
    current = next((item for item in state.features if item.id == state.run.current_feature), None)
    if current is None:
        current = next_feature(state)
    position = state.features.index(current) + 1 if current in state.features else 0
    pending = [_feature_data(item, state, pending=True) for item in state.features if item.status == FeatureStatus.pending]
    recovery = state.run.pending_recovery
    return {
        "run_status": state.run.status.value,
        "status_reason": state.run.status_reason,
        "running": workspace.is_locked(),
        "backlog_position": position,
        "backlog_total": len(state.features),
        "current_feature": current.id if current else None,
        "current_feature_title": current.title if current else None,
        "current_phase": state.run.current_phase,
        "current_step": state.run.current_step,
        "current_agent": state.run.current_agent.value if state.run.current_agent else None,
        "run_elapsed_s": _elapsed(state.run.started_at, now),
        "feature_elapsed_s": _elapsed(current.started_at, now) if current else None,
        "step_attempts": current.attempts.get(state.run.current_step or "", 0) if current else 0,
        "last_error": state.run.last_error,
        "next_retry_at": recovery.next_retry_at.isoformat() if recovery else None,
        "completed": [item.id for item in state.features if item.status == FeatureStatus.completed],
        "blocked": [item.id for item in state.features if item.status == FeatureStatus.blocked],
        "failed": [item.id for item in state.features if item.status == FeatureStatus.failed],
        "pending": pending,
    }


def _elapsed(start: datetime | None, now: datetime) -> float | None:
    return max((now - start).total_seconds(), 0.0) if start else None


def _feature_data(feature: Feature, state: State, pending: bool = False) -> dict:
    data = {"id": feature.id, "title": feature.title}
    if pending:
        data["waiting_on"] = waiting_on(feature, state)
        data["dependencies"] = [dependency.model_dump() for dependency in feature.dependencies]
    return data


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    minutes, seconds = divmod(max(int(seconds), 0), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02}m"
    return f"{minutes}m" if minutes else f"{seconds}s"


def format_status(data: dict) -> str:
    current = data["current_feature"]
    title = data["current_feature_title"]
    label = f'{current} "{title}"' if current else "-"
    reason = f' ({data["status_reason"]})' if data["status_reason"] else ""
    lines = [f'Run: {data["run_status"]}{reason} — running: {"yes" if data["running"] else "no"}']
    lines.append(f'Backlog: {data["backlog_position"]}/{data["backlog_total"]}   Current: {label}   Phase: {data["current_phase"] or "-"}   Step: {data["current_step"] or "-"}   Agent: {data["current_agent"] or "-"}')
    lines.append(f'Run elapsed: {_format_duration(data["run_elapsed_s"])}   Feature elapsed: {_format_duration(data["feature_elapsed_s"])}   Step attempts: {data["step_attempts"]}')
    lines.append(f'Last error: {data["last_error"] or "-"}')
    lines.append(f'Next retry: {_retry_text(data["next_retry_at"])}')
    lines.append("Completed: " + _ids(data["completed"]) + "   Blocked: " + _ids(data["blocked"]) + "   Failed: " + _ids(data["failed"]) + "   Pending: " + _pending_text(data["pending"]))
    return "\n".join(lines)


def _ids(items: list[str]) -> str:
    return ", ".join(items) if items else "-"


def _pending_text(items: list[dict]) -> str:
    return ", ".join(_pending_item(item) for item in items) or "-"


def _pending_item(item: dict) -> str:
    dependencies = [
        f'{dep["id"]} ({dep["added_by"]}: {dep["rationale"]})' if dep.get("rationale") else dep["id"]
        for dep in item["dependencies"]
    ]
    return f'{item["id"]} (depends on {", ".join(dependencies)})' if dependencies else item["id"]


def _retry_text(value: str | None) -> str:
    if value is None:
        return "-"
    try:
        retry_at = datetime.fromisoformat(value)
    except ValueError:
        return value
    remaining = max((retry_at - utcnow()).total_seconds(), 0)
    return f"{value} (in {_format_duration(remaining)})"


def render_report(state: State, now: datetime) -> str:
    counts = {status.value: sum(feature.status == status for feature in state.features) for status in FeatureStatus}
    lines = ["# AgentPilot Run Report", "", f"Generated: {now.isoformat()}", f"Run status: {state.run.status.value}", f"Reason: {state.run.status_reason or '-'}", "", "## Totals", ""]
    lines.extend(f"- {status}: {count}" for status, count in counts.items())
    for feature in state.features:
        lines.extend(_feature_report(feature, now))
    return "\n".join(lines) + "\n"


def _feature_report(feature: Feature, now: datetime) -> list[str]:
    duration = _elapsed(feature.started_at, feature.finished_at or now)
    dependencies = ", ".join(f"{item.id} ({item.added_by}{': ' + item.rationale if item.rationale else ''})" for item in feature.dependencies) or "-"
    lines = ["", f"## {feature.id}: {feature.title}", "", f"- Status: {feature.status.value}", f"- Branch: {feature.branch or '-'}", f"- Spec directory: {feature.spec_dir or '-'}", f"- Dependencies: {dependencies}", f"- Completed steps: {', '.join(feature.completed_steps) or '-'}", f"- Attempts: {json.dumps(feature.attempts, sort_keys=True) or '{}'}", f"- Duration: {_format_duration(duration)}", f"- Correction rounds: {feature.correction_round}", f"- Last verdict: {_last_verdict(feature) or '-'}", f"- Block/failure reason: {feature.block_reason or '-'}", "- Recent history:"]
    history = feature.history[-10:]
    lines.extend(f"  - {item.at.isoformat()} {item.event} ({item.step or '-'}) — {item.detail}" for item in history)
    if not history:
        lines.append("  - -")
    return lines


def _last_verdict(feature: Feature) -> str | None:
    events = [
        item for item in feature.history
        if item.step == "converge" and item.event in ("feature_completed", "verdict")
    ]
    return events[-1].detail if events else None


def write_report(workspace: Workspace, state: State, path: Path | None = None) -> Path:
    destination = path or workspace.report_path
    workspace.atomic_write_text(destination, render_report(state, utcnow()))
    return destination
