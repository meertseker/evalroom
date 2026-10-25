from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from helpers import Clock


@pytest.fixture
def clock() -> Clock:
    return Clock()
