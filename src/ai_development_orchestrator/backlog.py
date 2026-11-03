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


def add_features(state: State, items: list[dict[str, Any]]) -> None:
    features = list(state.features)
    for item in items:
        fid = str(item["id"])
        if any(f.id == fid for f in features):
            raise ValueError(f"duplicate feature id: {fid}")
        features.append(Feature(id=fid, title=str(item["title"]), description=str(item["description"])))
    state.features = features


def next_feature(state: State) -> Feature | None:
    for feature in state.features:
        if feature.status == FeatureStatus.completed:
            continue
        if feature.status == FeatureStatus.blocked:
            return None
        return feature
    return None
