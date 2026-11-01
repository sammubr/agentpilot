from ai_development_orchestrator import prompts
from ai_development_orchestrator.config import build_config
from ai_development_orchestrator.models import Feature, State


def test_no_placeholders_or_datamodel():
    f = Feature(id="001-test", title="Test", description="desc")
    s = State()
    for step in ["specify", "clarify", "plan", "tasks", "analyze", "implement.handoff", "post_analyze"]:
        p = prompts.engineering_prompt(step, f, s, "specs/001-test")
        assert "<spec_dir>" not in p
        assert "<r>" not in p
        assert "data-model.md" not in p


def test_step_expected_paths():
    f = Feature(id="001-test", title="Test", description="desc")
    s = State()
    assert "orchestrator/analysis.md" in prompts.engineering_prompt("analyze", f, s, "specs/001-test")
    assert "orchestrator/handoff-implement.md" in prompts.engineering_prompt("implement.handoff", f, s, "specs/001-test")
    assert "orchestrator/post-analysis.md" in prompts.engineering_prompt("post_analyze", f, s, "specs/001-test")
    p = prompts.engineering_prompt("converge.1", f, s, "specs/001-test", round=1)
    assert "verdict-1.json" in p
    p = prompts.engineering_prompt("correct.handoff.2", f, s, "specs/001-test", round=2)
    assert "verdict-1.json" in p
    assert "handoff-correction-2.md" in p


def test_verdict_shape():
    f = Feature(id="001-test", title="Test", description="desc")
    s = State()
    p = prompts.engineering_prompt("converge.1", f, s, "specs/001-test", round=1)
    assert '"approved": true' in p
    assert '"findings"' in p
    assert '"acceptance_criteria"' in p
    for field in ('"severity"', '"location"', '"met"', '"evidence"'):
        assert field in p
    assert "Never rely on the implementation agent's" in p


def test_config_override():
    f = Feature(id="001-test", title="Test", description="desc")
    s = State()
    cfg = build_config({"phases": [{"id": "custom", "prompt": "DO_CUSTOM", "expect": ["out.txt"]}]})
    p = prompts.engineering_prompt("custom", f, s, "specs/001-test", cfg=cfg)
    assert "DO_CUSTOM" in p


def test_extra_phase():
    f = Feature(id="001-test", title="Test", description="desc")
    s = State()
    cfg = build_config({"phases": [{"id": "extra1", "prompt": "EXTRA", "expect": ["orchestrator/extra.json"]}]})
    p = prompts.engineering_prompt("extra1", f, s, "specs/001-test", cfg=cfg)
    assert "Expected outputs" in p
    assert "orchestrator/extra.json" in p


def test_opencode_prompt_has_handoff():
    f = Feature(id="001-test", title="Test", description="desc")
    p = prompts.opencode_prompt("implement", f, "specs/001-test/orchestrator/handoff-implement.md", "specs/001-test")
    assert "handoff-implement.md" in p


def test_golden_prompt_sections_paths_and_review_instructions():
    feature = Feature(id="001-test", title="Test", description="desc")
    state = State()
    spec_dir = "specs/001-test"
    handoff = prompts.engineering_prompt("implement.handoff", feature, state, spec_dir)
    correction = prompts.engineering_prompt("correct.handoff.2", feature, state, spec_dir, round=2)
    assert all(section in handoff for section in prompts.HANDOFF_SECTIONS)
    assert all(section in correction for section in prompts.HANDOFF_SECTIONS)
    assert prompts.CORRECTION_EXTRA_SECTION in correction

    converge = prompts.engineering_prompt("converge.2", feature, state, spec_dir, round=2)
    assert f"{spec_dir}/orchestrator/diff-2.patch" in converge
    assert f"{spec_dir}/orchestrator/validation-2.json" in converge
    assert '"severity": "critical|major|minor"' in converge
    assert '"location": "path:line or null"' in converge
    assert '"acceptance_criteria": [{"criterion": "...", "met": true, "evidence": "..."}]' in converge
    assert "Never rely on the implementation agent's" in converge

    post = prompts.engineering_prompt("post_analyze", feature, state, spec_dir)
    assert "speckit-analyze" in post
    assert f"{spec_dir}/orchestrator/diff-post.patch" in post
    clarify = prompts.engineering_prompt("clarify", feature, state, spec_dir)
    assert "recommended option" in clarify
