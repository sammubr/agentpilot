from ai_development_orchestrator.config import DEFAULT_CONFIG_TOML, load_config


def test_defaults_load(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(DEFAULT_CONFIG_TOML)
    cfg = load_config(p)
    assert cfg.git.branch_prefix == "orchestrator/"
    assert cfg.retry.max_attempts_network == 10


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
    assert c1.validation.commands == c2.validation.commands


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
    config.validate_config(c, data)
