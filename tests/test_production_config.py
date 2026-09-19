import pytest
from wax.config.settings import Settings, validate_production_settings

def test_dev_allows_defaults():
    s = Settings(app_env="development", secret_key="change-me-in-production")
    validate_production_settings(s)  # must not raise

def test_production_rejects_weak_secret():
    s = Settings(app_env="production", secret_key="change-me-in-production", primary_api_key="x")
    with pytest.raises(RuntimeError):
        validate_production_settings(s)
