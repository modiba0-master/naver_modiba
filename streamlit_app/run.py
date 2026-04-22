"""Streamlit deployment entrypoint.

`streamlit run run.py` always forwards to `streamlit_app/dashboard.py`.
Keeping this file tiny avoids accidental logic drift from the real dashboard.
"""

from __future__ import annotations

import runpy
from pathlib import Path


if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).with_name("dashboard.py")), run_name="__main__")
