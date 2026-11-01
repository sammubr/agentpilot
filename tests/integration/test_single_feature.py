from __future__ import annotations

import os
from pathlib import Path

from ai_development_orchestrator.config import load_config
from ai_development_orchestrator.engine import Engine
from ai_development_orchestrator.models import Feature, FeatureStatus, RunStatus


def test_single_feature_all_ok(target_repo, workspace, scenario, calls, no_sleep):
    # setup scenario
    sc = scenario({"claude": {}, "opencode": {}})
    os.environ["FAKE_AGENT_SCENARIO"] = sc
    ws = workspace
    cfg = load_config(ws.config_path)
    # add a feature
    from ai_development_orchestrator import models

    state = ws.load_state()
    state.features.append(
        models.Feature(
            id="F1",
            title="Feature One",
            description="Do something",
        )
    )
    ws.save_state(state)

    eng = Engine(ws, cfg, sleep=lambda s: None, now=models.utcnow)
    res = eng.run(max_features=None)
    state = ws.load_state()
    assert state.run.status == RunStatus.finished or res == RunStatus.finished
    f = None
    for feat in state.features:
        if feat.id == "F1":
            f = feat
            break
    assert f is not None
    assert f.status == FeatureStatus.completed
    c = calls(sc)
    # calls log order
    steps = [x.get("step") for x in c if x.get("agent")]
    # expected order
    expected = ["select", "specify", "clarify", "plan", "tasks", "analyze", "implement.handoff", "implement.run", "post_analyze", "converge.0"]
    assert steps == expected
    # agent check
    for rec in c:
        if rec.get("step") == "implement.run":
            assert rec.get("agent") == "opencode"
            prompt = rec["argv"][-1]
            assert "specs/001-f1/orchestrator/handoff-implement.md" in prompt
            assert str(ws.repo) not in prompt
        elif rec.get("step"):
            assert rec.get("agent") == "claude"
    # git commits
    repo = ws.repo
    # check branch
    import subprocess as sp

    b = sp.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo, capture_output=True, text=True, check=False)
    # on orchestrator/F1
    # commits
    log = sp.run(["git", "log", "--oneline", "orchestrator/F1"], cwd=repo, capture_output=True, text=True, check=False)
    lines = [l for l in log.stdout.strip().splitlines() if l.strip()]
    # one commit per committed step (select none)
    committed = [s for s in expected if s != "select"]
    assert len(lines) >= len(committed)  # at least one per committed step
    # handoff
    sd = f.spec_dir
    assert sd
    hp = Path(repo) / sd / "orchestrator" / "handoff-implement.md"
    assert hp.exists()
    txt = hp.read_text()
    for sec in ["## Feature", "## Specification", "## Plan", "## Tasks", "## Project Instructions", "## Architecture Decisions", "## Acceptance Criteria", "## Validations", "## Restrictions"]:
        assert sec in txt


def test_single_feature_blocked_on_validation_failure_with_max_corrections_zero(target_repo, workspace, scenario, calls, no_sleep):
    sc = scenario({"claude": {}, "opencode": {"implement.run": ["break"]}})
    os.environ["FAKE_AGENT_SCENARIO"] = sc
    ws = workspace
    cfg = load_config(ws.config_path)
    cfg.convergence.max_corrections = 0
    from ai_development_orchestrator import models

    state = ws.load_state()
    state.features.append(
        models.Feature(
            id="F1",
            title="Feature One",
            description="Do something",
        )
    )
    ws.save_state(state)

    eng = Engine(ws, cfg, sleep=lambda s: None, now=models.utcnow)
    res = eng.run(max_features=None)
    state = ws.load_state()
    f = None
    for feat in state.features:
        if feat.id == "F1":
            f = feat
            break
    assert f is not None
    assert f.status == FeatureStatus.blocked
    assert f.block_reason and "BROKEN" in f.block_reason or "test" in f.block_reason


def test_bad_handoff_retry_prompt_contains_rejection_reason(workspace, scenario, calls, monkeypatch):
    state = workspace.load_state()
    state.features.append(Feature(id="F1", title="Feature", description="retry the rejected handoff"))
    workspace.save_state(state)
    scenario_path = scenario({"claude": {"implement.handoff": ["bad_handoff", "ok"]}})
    monkeypatch.setenv("FAKE_AGENT_SCENARIO", scenario_path)

    result = Engine(workspace, load_config(workspace.config_path), sleep=lambda _: None, handle_signals=False).run()

    assert result == RunStatus.finished
    handoff_calls = [record for record in calls(scenario_path) if record.get("step") == "implement.handoff"]
    assert len(handoff_calls) == 2
    retry_prompt = next(workspace.runs_dir.glob("*-F1-implement.handoff-a2/prompt.txt"))
    assert "rejected by the orchestrator: missing sections: ## Restrictions" in retry_prompt.read_text()


def test_no_changes_implementation_fails_after_execution_retries(workspace, scenario, calls, monkeypatch):
    state = workspace.load_state()
    state.features.append(Feature(id="F1", title="Feature", description="must make a change"))
    workspace.save_state(state)
    scenario_path = scenario({"opencode": {"implement.run": "no_changes"}})
    monkeypatch.setenv("FAKE_AGENT_SCENARIO", scenario_path)

    result = Engine(workspace, load_config(workspace.config_path), sleep=lambda _: None, handle_signals=False).run()

    state = workspace.load_state()
    assert result == RunStatus.halted
    assert state.features[0].status == FeatureStatus.failed
    assert state.features[0].block_reason == "implementation produced no changes"
    runs = [record for record in calls(scenario_path) if record.get("step") == "implement.run"]
    assert len(runs) == 3


def test_two_features_depend_order(target_repo, workspace, scenario, calls, no_sleep):
    sc = scenario({"claude": {}, "opencode": {}})
    os.environ["FAKE_AGENT_SCENARIO"] = sc
    ws = workspace
    cfg = load_config(ws.config_path)
    from ai_development_orchestrator import backlog, models

    state = ws.load_state()
    # add F1 and F2 with dep? or let select add dep? easier to add via backlog
    backlog.add_features(state, [
        {"id": "F1", "title": "One", "description": "d1"},
        {"id": "F2", "title": "Two", "description": "d2", "depends_on": ["F1"]},
    ])
    ws.save_state(state)
    eng = Engine(ws, cfg, sleep=lambda s: None, now=models.utcnow)
    res = eng.run(max_features=None)
    state = ws.load_state()
    f1 = next(x for x in state.features if x.id == "F1")
    f2 = next(x for x in state.features if x.id == "F2")
    assert f1.status == FeatureStatus.completed
    assert f2.status == FeatureStatus.completed
