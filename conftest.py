"""Test bootstrap: make the src/ layout importable before packaging is set up.

Once the project is installed (``uv pip install -e .`` / pyproject packaging),
this shim becomes unnecessary and can be removed.
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
