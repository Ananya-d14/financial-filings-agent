"""Pytest fixtures and global config."""

from __future__ import annotations

import os

# initial scaffold default: ensure tests don't accidentally hit live APIs.
# agent graph+ tests that genuinely need API keys will use the `live_api` marker
# and read keys from the environment explicitly.
os.environ.setdefault("GROQ_API_KEY", "")
os.environ.setdefault("ENVIRONMENT", "development")
