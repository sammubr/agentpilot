from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from .models import Feature, FeatureStatus, State


def load_backlog_file(path: Path) -> list[dict[str, Any]]:
    text = path.read_text()
    data = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text) or {}
    if isinstance(data, dict):
        return data.get("features") or []
    return data if isinstance(data, list) else []


def find_cycle(features: list[Feature]) -> list[str] | None:
    graph = {f.id: f.depends_on for f in features}
    done: set[str] = set()

    def visit(node: str, path: list[str]) -> list[str] | None:
        if node in path:
            return path[path.index(node):]
        if node in done or node not in graph:
            return None
        for child in graph[node]:
            cycle = visit(child, [*path, node])
            if cycle:
                return cycle
        done.add(node)
        return None

    return next((cycle for node in graph if (cycle := visit(node, []))), None)


def add_features(state: State, items: list[dict[str, Any]]) -> None:
    features = list(state.features)
    for item in items:
        fid = str(item["id"])
        if any(f.id == fid for f in features):
            raise ValueError(f"duplicate feature id: {fid}")
        deps = [str(dep) for dep in item.get("depends_on") or []]
        features.append(Feature(id=fid, title=str(item["title"]), description=str(item["description"]), depends_on=deps))
    known = {f.id for f in features}
    unknown = sorted({dep for f in features for dep in f.depends_on if dep not in known})
    if unknown:
        raise ValueError(f"unknown dependency: {', '.join(unknown)}")
    cycle = find_cycle(features)
    if cycle:
        raise ValueError(f"dependency cycle: {' -> '.join(cycle)}")
    state.features = features


def next_feature(state: State) -> Feature | None:
    active = next((f for f in state.features if f.status == FeatureStatus.in_progress), None)
    if active:
        return active
    return next((f for f in state.features if f.status == FeatureStatus.pending and not waiting_on(f, state)), None)


def waiting_on(feature: Feature, state: State) -> list[str]:
    completed = {f.id for f in state.features if f.status == FeatureStatus.completed}
    return [dep for dep in feature.depends_on if dep not in completed]
