"""표시용 그리드 — Streamlit 기본 테이블(전체 폭)."""

from __future__ import annotations

import re

import pandas as pd
import streamlit as st

try:
    # Monorepo execution (workspace root on PYTHONPATH)
    from streamlit_app.column_map import COLUMN_DISPLAY_ORDER, COLUMN_MAP
except ModuleNotFoundError:
    # Railway root deployment (`/app` == `streamlit_app` directory)
    from column_map import COLUMN_DISPLAY_ORDER, COLUMN_MAP

_FULL_WIDTH_CSS_KEY = "_modiba_dataframe_full_width_css"


def _inject_full_width_dataframe_css_once() -> None:
    """Glide dataframe 등에서 max-width가 좁게 잡히는 경우 대비."""
    if st.session_state.get(_FULL_WIDTH_CSS_KEY):
        return
    st.markdown(
        """
<style>
    div[data-testid="stDataFrame"] {
        width: 100% !important;
        max-width: 100% !important;
    }
</style>
""",
        unsafe_allow_html=True,
    )
    st.session_state[_FULL_WIDTH_CSS_KEY] = True


def _comma_format_numeric_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_numeric_dtype(out[c]):
            out[c] = out[c].map(
                lambda x: f"{int(x):,}"
                if pd.notna(x) and not isinstance(x, bool)
                else ""
            )
    return out


def _ensure_dataframe(data: pd.DataFrame | list | dict) -> pd.DataFrame:
    if isinstance(data, pd.DataFrame):
        return data
    return pd.DataFrame(data)


def _order_display_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or not df.columns.size:
        return df
    seen: set[str] = set()
    primary: list[str] = []
    for c in COLUMN_DISPLAY_ORDER:
        if c in df.columns and c not in seen:
            primary.append(c)
            seen.add(c)
    rest = [c for c in df.columns if c not in seen]
    return df[primary + rest]


def _move_total_rows_to_bottom(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    total_mask = df.apply(
        lambda row: any(str(v).strip() == "합계" for v in row), axis=1
    )
    if not total_mask.any():
        return df
    body = df.loc[~total_mask]
    total = df.loc[total_mask]
    return pd.concat([body, total], ignore_index=True)


_CAMEL_BOUNDARY_RE = re.compile(r"(?<!^)(?=[A-Z])")


def _normalize_key_for_mapping(col: object) -> str:
    text = str(col)
    normalized = _CAMEL_BOUNDARY_RE.sub("_", text).replace("-", "_")
    return normalized.strip().lower()


def _to_display_column_name(col: object) -> str:
    original = str(col)
    return COLUMN_MAP.get(original, COLUMN_MAP.get(_normalize_key_for_mapping(original), original))


def _modiba_dark_styler(frame: pd.DataFrame) -> pd.io.formats.style.Styler:
    """Streamlit Glide 표가 앱 테마보다 밝게 남는 경우 대비 — Styler로 셀·헤더 톤 통일."""
    cell_bg = "#141a23"
    text_fg = "#e6edf3"
    header_bg = "#1c2636"
    header_fg = "#b1bac4"
    border = "#30363d"
    return (
        frame.style.set_properties(
            **{
                "background-color": cell_bg,
                "color": text_fg,
            }
        )
        .set_table_styles(
            [
                {
                    "selector": "thead th",
                    "props": [
                        ("background-color", header_bg),
                        ("color", header_fg),
                        ("font-weight", "600"),
                        ("border-bottom", f"1px solid {border}"),
                    ],
                },
            ],
            overwrite=False,
        )
    )


def _prepare_dataframe_for_display(
    frame: pd.DataFrame,
) -> pd.DataFrame | pd.io.formats.style.Styler:
    if frame.empty:
        return frame
    return _modiba_dark_styler(frame)


def _make_unique_column_headers(df: pd.DataFrame) -> pd.DataFrame:
    seen: dict[str, int] = {}
    new_cols: list[str] = []
    for c in df.columns:
        base = str(c)
        n = seen.get(base, 0)
        seen[base] = n + 1
        new_cols.append(base if n == 0 else f"{base} ({n + 1})")
    out = df.copy()
    out.columns = new_cols
    return out


def show_summary_table(data: pd.DataFrame | list | dict) -> None:
    show_data_grid(data)


def show_data_grid(data: pd.DataFrame | list | dict) -> None:
    _inject_full_width_dataframe_css_once()
    df_src = _ensure_dataframe(data)
    df = df_src.copy()
    df.columns = [_to_display_column_name(col) for col in df.columns]
    df = _make_unique_column_headers(df)
    df = _order_display_columns(df)
    df = _move_total_rows_to_bottom(df)
    df = _comma_format_numeric_columns(df)
    display_obj = _prepare_dataframe_for_display(df)
    try:
        st.dataframe(display_obj, width="stretch", hide_index=True)
    except TypeError:
        st.dataframe(display_obj, use_container_width=True, hide_index=True)
