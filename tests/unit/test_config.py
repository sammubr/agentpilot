import pytest

from ai_development_orchestrator.config import DEFAULT_CONFIG_TOML, load_config


def test_defaults_load(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(DEFAULT_CONFIG_TOML)
    cfg = load_config(p)
    assert cfg.git.branch_prefix == "orchestrator/"
    assert cfg.retry.max_attempts_network == 10


def test_empty_legacy_base_branch_is_accepted(tmp_path):
    p = tmp_path / "config.toml"
    text = DEFAULT_CONFIG_TOML.replace("[git]\n", '[git]\nbase_branch = ""\n')
    p.write_text(text)

    assert load_config(p).git.branch_per_feature


def test_nonempty_legacy_base_branch_is_rejected(tmp_path):
    p = tmp_path / "config.toml"
    text = DEFAULT_CONFIG_TOML.replace("[git]\n", '[git]\nbase_branch = "main"\n')
    p.write_text(text)

    with pytest.raises(ValueError, match="repository default branch"):
        load_config(p)


def test_disabling_per_feature_branches_is_rejected(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(DEFAULT_CONFIG_TOML.replace("branch_per_feature = true", "branch_per_feature = false"))

    with pytest.raises(ValueError, match="branch_per_feature"):
        load_config(p)


def test_unrestricted_rejected(tmp_path):
    p = tmp_path / "config.toml"
    s = DEFAULT_CONFIG_TOML.replace('permission_mode = "dontAsk"', 'permission_mode = "bypassPermissions"')
    p.write_text(s)
    try:
        load_config(p)
        assert False
    except Exception:
        pass


def test_override_accepted_with_allow_unrestricted(tmp_path):
    p = tmp_path / "config.toml"
    s = DEFAULT_CONFIG_TOML.replace('permission_mode = "dontAsk"', 'permission_mode = "bypassPermissions"')
    s2 = s.replace('allow_unrestricted = false', 'allow_unrestricted = true')
    p.write_text(s2)
    cfg = load_config(p)
    assert cfg.safety.allow_unrestricted


def test_build_config_defaults_equal(tmp_path):
    import tomllib

    from ai_development_orchestrator.config import DEFAULT_CONFIG_TOML, build_config

    c1 = build_config({})
    c2 = build_config(tomllib.loads(DEFAULT_CONFIG_TOML))
    assert c1.retry.initial_delay_s == c2.retry.initial_delay_s
    assert c1.agents["claude"].allowed_tools == c2.agents["claude"].allowed_tools
    assert c1.agents["opencode"].permission == c2.agents["opencode"].permission


def test_config_permission_dict_passthrough(tmp_path):
    from ai_development_orchestrator.config import build_config

    data = {"agents": {"opencode": {"permission": {"bash": {"ls": "allow"}, "edit": "deny"}}}}
    c = build_config(data)
    assert c.agents["opencode"].permission["edit"] == "deny"
    assert c.agents["opencode"].permission["bash"]["ls"] == "allow"


def test_retry_floats_validation(tmp_path):
    import tomllib

    from ai_development_orchestrator import config

    data = tomllib.loads(config.DEFAULT_CONFIG_TOML)
    data["retry"]["initial_delay_s"] = 0.01
    data["retry"]["usage_limit_margin_s"] = 0
    c = config.build_config(data)
    config.validate_config(c)


def test_removed_sections_rejected(tmp_path):
    import pytest

    p = tmp_path / "config.toml"
    for section in ("[validation]\ncommands = []\n", "[[phases]]\nid = \"x\"\nprompt = \"y\"\n"):
        p.write_text(DEFAULT_CONFIG_TOML + section)
        with pytest.raises(ValueError, match="validation|phases"):
            load_config(p)


def test_unknown_key_rejected(tmp_path):
    import pytest

    p = tmp_path / "config.toml"
    p.write_text(DEFAULT_CONFIG_TOML.replace("push = false", "push = false\nbogus = 1"))
    with pytest.raises(ValueError, match="bogus"):
        load_config(p)
