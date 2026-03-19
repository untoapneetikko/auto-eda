"""Shared fixtures for backend engine tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure backend package is importable
_PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
