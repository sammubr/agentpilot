import pytest

from ai_development_orchestrator import backlog
from ai_development_orchestrator.models import FeatureStatus, State


def test_import_yaml(tmp_path):
    path = tmp_path / "backlog.yaml"
    path.write_text("features:\n  - id: F1\n    title: T\n    description: D\n")

    assert backlog.load_backlog_file(path)[0]["id"] == "F1"


def test_import_json(tmp_path):
    path = tmp_path / "backlog.json"
    path.write_text('{"features":[{"id":"F1","title":"T","description":"D"}]}')

    assert backlog.load_backlog_file(path)[0]["id"] == "F1"


def test_legacy_dependency_metadata_is_ignored():
    state = State()
    items = [
        {"id": "A", "title": "A", "description": "A", "depends_on": ["B"]},
        {"id": "B", "title": "B", "description": "B", "depends_on": ["A"]},
        {"id": "C", "title": "C", "description": "C", "depends_on": ["unknown"]},
    ]

    backlog.add_features(state, items)

    assert [feature.id for feature in state.features] == ["A", "B", "C"]
    assert all(not hasattr(feature, "depends_on") for feature in state.features)


def test_duplicates_are_rejected():
    state = State()
    with pytest.raises(ValueError, match="duplicate feature id"):
        backlog.add_features(state, [
            {"id": "F1", "title": "T", "description": "D"},
            {"id": "F1", "title": "T2", "description": "D2"},
        ])


def test_next_selection_follows_backlog_order():
    state = State()
    backlog.add_features(state, [
        {"id": "F1", "title": "T", "description": "D"},
        {"id": "F2", "title": "T", "description": "D", "depends_on": ["F3"]},
        {"id": "F3", "title": "T", "description": "D"},
    ])

    first = backlog.next_feature(state)
    assert first.id == "F1"
    first.status = FeatureStatus.completed
    assert backlog.next_feature(state).id == "F2"


def test_blocked_head_prevents_selecting_later_features():
    state = State()
    backlog.add_features(state, [
        {"id": "A", "title": "A", "description": "A"},
        {"id": "B", "title": "B", "description": "B"},
        {"id": "C", "title": "C", "description": "C"},
    ])
    state.features[0].status = FeatureStatus.blocked

    assert backlog.next_feature(state) is None


def test_failed_import_leaves_state_unchanged():
    state = State()
    with pytest.raises((KeyError, ValueError)):
        backlog.add_features(state, [{"id": "F1", "description": "missing title"}])

    assert state.features == []
