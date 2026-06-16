"""Pytest configuration.

Putting a conftest at the project root adds the root to ``sys.path`` during
collection, so the tests under ``tests/`` can import the top-level modules
(``catalog``, ``epg_guide``, ``player`` ...) whether you run ``pytest`` or
``python -m pytest``.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
