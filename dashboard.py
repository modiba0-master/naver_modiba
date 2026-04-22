"""Legacy root entrypoint kept for compatibility.

The canonical dashboard implementation lives in `streamlit_app/dashboard.py`.
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


_ROOT = Path(__file__).resolve().parent
_STREAMLIT_DIR = _ROOT / "streamlit_app"
if str(_STREAMLIT_DIR) not in sys.path:
    sys.path.insert(0, str(_STREAMLIT_DIR))


if __name__ == "__main__":
    runpy.run_path(str(_STREAMLIT_DIR / "dashboard.py"), run_name="__main__")
