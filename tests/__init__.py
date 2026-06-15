"""Test package bootstrap.

Keep unit-test discovery hermetic even when local LLM credentials are set.
"""

from __future__ import annotations

import os


os.environ["LLM_PROVIDER"] = "disabled"
os.environ.pop("LLM_API_KEY", None)
os.environ.pop("DEEPSEEK_API_KEY", None)
os.environ.pop("QWEN_API_KEY", None)
