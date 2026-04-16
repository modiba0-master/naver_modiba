"""표시 전용 그리드.

Railway/FastAPI/DB로부터 받은 DataFrame·쿼리 결과는 **컬럼명·값을 변경하지 않는다**.
`st.dataframe`에 넘기기 직전에만 `.copy()`에 한글 헤더·숫자 콤마 포맷을 적용한다.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from column_map import COLUMN_DISPLAY_ORDER, COLUMN_MAP


def _comma_format_numeric_columns(df: pd.DataFrame) -> pd.DataFrame:
    """숫자 dtype 컬럼만 천 단위 콤마 문자열로 변환."""
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
    """st.dataframe 직전에 항상 DataFrame으로 통일."""
    if isinstance(data, pd.DataFrame):
        return data
    return pd.DataFrame(data)


def _order_display_columns(df: pd.DataFrame) -> pd.DataFrame:
    """COLUMN_DISPLAY_ORDER 순으로 앞에 두고, 나머지는 기존 열 순서를 유지해 뒤에 둔다."""
    if df.empty or not df.columns.size:
        return df
    primary = [c for c in COLUMN_DISPLAY_ORDER if c in df.columns]
    rest = [c for c in df.columns if c not in primary]
    return df[primary + rest]


_CAMEL_BOUNDARY_RE = re.compile(r"(?<!^)(?=[A-Z])")


def _normalize_key_for_mapping(col: object) -> str:
    """컬럼명 매핑용 표준 키(snake_case)로 정규화."""
    text = str(col)
    normalized = _CAMEL_BOUNDARY_RE.sub("_", text).replace("-", "_")
    return normalized.strip().lower()


def _to_display_column_name(col: object) -> str:
    """원본 이름 유지 + snake/camel 케이스 모두 COLUMN_MAP에서 조회."""
    original = str(col)
    return COLUMN_MAP.get(original, COLUMN_MAP.get(_normalize_key_for_mapping(original), original))


def show_summary_table(data: pd.DataFrame | list | dict) -> None:
    """소형 요약(orders/daily_summary 등): show_data_grid와 동일."""
    show_data_grid(data)


def show_data_grid(data: pd.DataFrame | list | dict) -> None:
    """일반 표: 인자로 받은 DataFrame/객체는 변형하지 않고, 복사본 헤더만 한글화 후 표시."""
    df_src = _ensure_dataframe(data)
    # 원본(동일 객체) 컬럼은 절대 덮어쓰지 않음 — 항상 사본에만 표시명 반영
    df = df_src.copy()
    df.columns = [_to_display_column_name(col) for col in df.columns]
    # 매핑 테이블에 없는 컬럼은 영문 등 원래 이름 그대로 표시 (열을 버리지 않음)
    df = _order_display_columns(df)
    df = _comma_format_numeric_columns(df)
    st.dataframe(df, use_container_width=True, hide_index=True)
