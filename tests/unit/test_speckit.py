from ai_development_orchestrator import speckit
from ai_development_orchestrator.models import PLANNING, AgentName, Feature, Stage

ALL = [*PLANNING, Stage.implement, Stage.converge]


def feature(tmp_path, done=(), files=(), tasks=None, **kw):
    spec = tmp_path / "specs" / "001-x"
    spec.mkdir(parents=True, exist_ok=True)
    for name in files:
        (spec / name).write_text("x")
    if tasks is not None:
        (spec / "tasks.md").write_text(tasks)
    return Feature(id="F1", title="Login", description="Users log in", spec_dir="specs/001-x", done=list(done), **kw)


def test_instructions_are_short_spec_kit_commands(tmp_path):
    f = feature(tmp_path)
    for stage in ALL:
        text = speckit.instruction(stage, f)
        expected = "Run the Spec Kit command /speckit.implement" if stage == Stage.implement else f"/speckit-{stage.value}"
        assert text.startswith(expected)
        if stage != Stage.specify:
            assert len(text) < 500
    assert speckit.instruction(Stage.specify, f).startswith("/speckit-specify Login: Users log in")
    assert "specs/001-x" in speckit.instruction(Stage.implement, f)


def test_retry_note_appended(tmp_path):
    text = speckit.instruction(Stage.plan, feature(tmp_path), "missing plan.md")
    assert text == "/speckit-plan\nPrevious attempt rejected: missing plan.md"


def test_agent_for():
    assert speckit.agent_for(Stage.implement) == AgentName.opencode
    assert all(speckit.agent_for(stage) == AgentName.claude for stage in ALL if stage != Stage.implement)


def test_open_tasks(tmp_path):
    f = feature(tmp_path, tasks="- [ ] T1 a\n- [x] T2 b\n- [X] T3 c\n  - [ ] T4 nested\n")
    assert speckit.open_tasks(tmp_path, f.spec_dir) == 2


def test_artifact_error(tmp_path):
    f = feature(tmp_path)
    assert "spec.md" in speckit.artifact_error(Stage.specify, tmp_path, f)
    assert "plan.md" in speckit.artifact_error(Stage.plan, tmp_path, f)
    assert "tasks.md" in speckit.artifact_error(Stage.converge, tmp_path, f)
    assert speckit.artifact_error(Stage.plan, tmp_path, f.model_copy(update={"spec_dir": None}))
    feature(tmp_path, files=["spec.md", "plan.md", "tasks.md"])
    assert all(speckit.artifact_error(stage, tmp_path, f) is None for stage in ALL)


def test_next_stage_planning_order(tmp_path):
    f = feature(tmp_path, done=[Stage.specify], files=["spec.md"])
    assert speckit.next_stage(f, tmp_path, 3) == Stage.clarify
    f = Feature(id="F1", title="t", description="d")
    assert speckit.next_stage(f, tmp_path, 3) == Stage.specify


def test_next_stage_implement_then_converge(tmp_path):
    f = feature(tmp_path, done=PLANNING, files=["spec.md", "plan.md"], tasks="- [ ] T1\n")
    assert speckit.next_stage(f, tmp_path, 3) == Stage.implement
    f.done.append(Stage.implement)
    assert speckit.next_stage(f, tmp_path, 3) == Stage.converge


def test_next_stage_completed_when_no_open_tasks(tmp_path):
    f = feature(tmp_path, done=ALL, files=["spec.md", "plan.md"], tasks="- [X] T1\n")
    assert speckit.next_stage(f, tmp_path, 3) == "completed"


def test_next_stage_correction_round_then_blocked(tmp_path):
    f = feature(tmp_path, done=ALL, files=["spec.md", "plan.md"], tasks="- [X] T1\n- [ ] T2\n")
    assert speckit.next_stage(f, tmp_path, 1) == Stage.implement
    assert f.round == 1 and f.done == PLANNING
    f.done = list(ALL)
    assert speckit.next_stage(f, tmp_path, 1) == "blocked"


def test_missing_plan_drops_later_stages(tmp_path):
    f = feature(tmp_path, done=ALL, files=["spec.md"], tasks="- [ ] T1\n")
    assert speckit.next_stage(f, tmp_path, 3) == Stage.plan
    assert f.done == [Stage.specify, Stage.clarify]


def test_read_spec_dir(tmp_path):
    (tmp_path / ".specify").mkdir()
    path = tmp_path / ".specify" / "feature.json"
    path.write_text('{"feature_directory": "specs/001-x"}')
    assert speckit.read_spec_dir(tmp_path) == "specs/001-x"
    path.write_text('{"feature_directory": "../escape"}')
    assert speckit.read_spec_dir(tmp_path) is None
    path.write_text("not json")
    assert speckit.read_spec_dir(tmp_path) is None
