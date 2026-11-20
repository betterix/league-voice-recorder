"""
Automated Test Suite for League Voice Recorder.
Guarantees test isolation so test runs never mutate the user's real config.json.
"""
import os
import tempfile

if "LVR_CONFIG_PATH" not in os.environ:
    _test_tmp_dir = tempfile.mkdtemp(prefix="lvr_test_")
    os.environ["LVR_CONFIG_PATH"] = os.path.join(_test_tmp_dir, "test_config.json")

