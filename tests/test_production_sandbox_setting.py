from wax.config.settings import Settings


def test_production_forces_require_sandbox_property():
    s = Settings(app_env="production", terminal_require_sandbox=False, secret_key="x" * 32)
    assert s.effective_terminal_require_sandbox is True


def test_dev_respects_false():
    s = Settings(app_env="development", terminal_require_sandbox=False)
    assert s.effective_terminal_require_sandbox is False
