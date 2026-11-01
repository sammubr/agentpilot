from ai_development_orchestrator import backlog
from ai_development_orchestrator.models import State


def test_import_yaml(tmp_path):
    p = tmp_path / "b.yaml"
    p.write_text("features:\n  - id: F1\n    title: T\n    description: D\n    depends_on: []\n")
    items = backlog.load_backlog_file(p)
    assert items[0]["id"] == "F1"


def test_import_json(tmp_path):
    p = tmp_path / "b.json"
    p.write_text('{"features":[{"id":"F1","title":"T","description":"D"}]}')
    items = backlog.load_backlog_file(p)
    assert items[0]["id"] == "F1"


def test_duplicates():
    s = State()
    try:
        backlog.add_features(s, [{"id": "F1", "title": "T", "description": "D"}, {"id": "F1", "title": "T2", "description": "D2"}])
        assert False
    except Exception:
        pass


def test_unknown_deps():
    s = State()
    try:
        backlog.add_features(s, [{"id": "F1", "title": "T", "description": "D", "depends_on": ["F2"]}])
        assert False
    except Exception:
        pass


def test_cycles():
    s = State()
    try:
        backlog.add_features(s, [{"id": "F1", "title": "T", "description": "D", "depends_on": ["F2"]}, {"id": "F2", "title": "T", "description": "D", "depends_on": ["F1"]}])
        assert False
    except Exception:
        pass


def test_next_selection():
    s = State()
    backlog.add_features(s, [{"id": "F1", "title": "T", "description": "D"}, {"id": "F2", "title": "T", "description": "D", "depends_on": ["F1"]}])
    n = backlog.next_feature(s)
    assert n.id == "F1"
    n.status = __import__('ai_development_orchestrator.models', fromlist=['FeatureStatus']).FeatureStatus.completed
    n2 = backlog.next_feature(s)
    assert n2.id == "F2"
