from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from .models import Dependency, Feature, FeatureStatus, HistoryEvent, State, utcnow


def load_backlog_file(path: Path) -> list[dict[str, Any]]:
    text = path.read_text()
    if path.suffix in (".yaml", ".yml"):
        data = yaml.safe_load(text) or {}
    elif path.suffix == ".json":
        data = json.loads(text)
    else:
        # try yaml first
        try:
            data = yaml.safe_load(text) or {}
        except (OSError, ValueError):
            data = json.loads(text)
    if isinstance(data, dict):
        items = data.get("features") or []
    elif isinstance(data, list):
        items = data
    else:
        items = []
    return items


def find_cycle(state: State) -> list[str] | None:
    graph: dict[str, list[str]] = {}
    for f in state.features:
        graph[f.id] = [d.id for d in f.dependencies]
    visited = set()
    rec = set()

    def dfs(n: str, stack: list[str]) -> list[str] | None:
        visited.add(n)
        rec.add(n)
        stack.append(n)
        for child in graph.get(n, []):
            if child not in graph:
                continue
            if child not in visited:
                res = dfs(child, stack)
                if res:
                    return res
            elif child in rec:
                # cycle found
                idx = stack.index(child)
                return stack[idx:]
        stack.pop()
        rec.discard(n)
        return None

    for node in graph:
        if node not in visited:
            res = dfs(node, [])
            if res:
                return res
    return None


def add_features(state: State, features: list[dict[str, Any]]) -> State:
    existing = {f.id for f in state.features}
    for item in features:
        fid = str(item["id"])
        if fid in existing:
            raise ValueError("duplicate id")
        deps = []
        for d in item.get("depends_on") or []:
            deps.append(Dependency(id=str(d), rationale=None, added_by="operator"))
        f = Feature(
            id=fid,
            title=str(item["title"]),
            description=str(item["description"]),
            dependencies=deps,
        )
        state.features.append(f)
        existing.add(fid)
    # validate deps exist
    all_ids = {f.id for f in state.features}
    for f in state.features:
        for d in f.dependencies:
            if d.id not in all_ids:
                raise ValueError("unknown dependency")
    # validate cycles
    if find_cycle(state):
        raise ValueError("cycle detected")
    return state


def next_feature(state: State) -> Feature | None:
    # first in_progress
    for f in state.features:
        if f.status == FeatureStatus.in_progress:
            return f
    # first pending whose deps all completed
    completed = {f.id for f in state.features if f.status == FeatureStatus.completed}
    for f in state.features:
        if f.status == FeatureStatus.pending:
            ok = True
            for d in f.dependencies:
                if d.id not in completed:
                    ok = False
                    break
            if ok:
                return f
    return None


def waiting_on(feature: Feature, state: State) -> list[str]:
    completed = {f.id for f in state.features if f.status == FeatureStatus.completed}
    res = []
    for d in feature.dependencies:
        if d.id not in completed:
            res.append(d.id)
    return res


def add_claude_dependencies(state: State, feature: Feature, deps: list[dict[str, Any]]) -> None:
    all_ids = {f.id for f in state.features}
    for d in deps:
        did = str(d["id"])
        if did not in all_ids:
            raise ValueError("unknown dependency")
        rationale = d.get("rationale")
        dep = Dependency(id=did, rationale=rationale, added_by="claude")
        # avoid duplicates
        if any(x.id == did for x in feature.dependencies):
            continue
        feature.dependencies.append(dep)
        feature.history.append(
            HistoryEvent(
                at=utcnow(),
                step=None,
                event="dependency_added",
                detail=f"{did}:{rationale or ''}",
            )
        )
    if find_cycle(state):
        raise ValueError("cycle detected")
