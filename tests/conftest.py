import os
import tempfile
import pytest


@pytest.fixture(autouse=True, scope="session")
def isolate_test_config_suite():
    """Guarantees tests run against an isolated temporary config file and never touch user Documents config."""
    if "LVR_CONFIG_PATH" not in os.environ:
        tmp_dir = tempfile.mkdtemp(prefix="lvr_test_session_")
        os.environ["LVR_CONFIG_PATH"] = os.path.join(tmp_dir, "test_config.json")
    yield os.environ["LVR_CONFIG_PATH"]
