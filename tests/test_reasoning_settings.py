import os
import unittest
from unittest import mock

from backend.config import get_reasoning_settings


class ReasoningSettingsTest(unittest.TestCase):
    def test_default_is_three(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_reasoning_settings().max_steps, 3)

    def test_env_override(self):
        with mock.patch.dict(os.environ, {"AGENT_MAX_REASONING_STEPS": "5"}, clear=True):
            self.assertEqual(get_reasoning_settings().max_steps, 5)

    def test_invalid_falls_back_to_default(self):
        with mock.patch.dict(os.environ, {"AGENT_MAX_REASONING_STEPS": "abc"}, clear=True):
            self.assertEqual(get_reasoning_settings().max_steps, 3)

    def test_clamped_to_upper_bound(self):
        with mock.patch.dict(os.environ, {"AGENT_MAX_REASONING_STEPS": "99"}, clear=True):
            self.assertEqual(get_reasoning_settings().max_steps, 10)


if __name__ == "__main__":
    unittest.main()
