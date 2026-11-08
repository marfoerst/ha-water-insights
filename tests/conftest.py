"""Shared fixtures."""
import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    """Recorder first (it must exist before hass), then custom integrations."""
    yield
