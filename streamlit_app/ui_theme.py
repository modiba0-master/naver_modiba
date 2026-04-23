"""대시보드 UI 테마(다크 모드) 및 클래식(이전 화면) 롤백.

완전 제거 절차(신규 UI에 확정 후 롤백 코드를 없앨 때)
--------------------------------------------------------
1. 이 파일 `streamlit_app/ui_theme.py` 을 삭제한다.
2. `streamlit_app/dashboard.py` 에서 본 모듈 import 및
   `apply_dashboard_theme`, `render_ui_rollback_control` 호출을 제거한다.
3. `render_page_title` 사용을 기존 `st.title(...)` 한 줄로 되돌린다(또는 제목만 유지).
"""

from __future__ import annotations

import html

import streamlit as st

UI_MODE_MODERN = "modern"
UI_MODE_CLASSIC = "classic"
SESSION_UI_MODE_KEY = "_modiba_ui_display_mode"


def get_ui_mode() -> str:
    st.session_state.setdefault(SESSION_UI_MODE_KEY, UI_MODE_MODERN)
    mode = st.session_state.get(SESSION_UI_MODE_KEY, UI_MODE_MODERN)
    return mode if mode in (UI_MODE_MODERN, UI_MODE_CLASSIC) else UI_MODE_MODERN


def apply_dashboard_theme(mode: str | None = None) -> None:
    """현재 세션 모드에 맞춰 전역 스타일을 주입한다. 클래식은 Streamlit 기본에 가깝게 둔다."""
    m = mode or get_ui_mode()
    if m == UI_MODE_CLASSIC:
        return
    st.markdown(_dark_theme_css(), unsafe_allow_html=True)


def render_ui_rollback_control() -> None:
    """이전 UI로 되돌릴 수 있는 안전장치(익스팬더 + 라디오)."""
    get_ui_mode()  # ensure default session key exists
    with st.expander("UI 안전장치 — 이전 화면(클래식)으로 복원", expanded=False):
        st.caption(
            "신규 다크 UI가 불편하면 여기서 즉시 이전 스타일로 전환할 수 있습니다. "
            "확정 후 롤백 코드를 없애려면 이 파일 상단 docstring의 제거 절차를 따르세요."
        )
        st.radio(
            "표시 모드",
            options=[UI_MODE_MODERN, UI_MODE_CLASSIC],
            format_func=lambda x: (
                "신규 다크 UI (권장)" if x == UI_MODE_MODERN else "이전 화면 (클래식 · 롤백)"
            ),
            key=SESSION_UI_MODE_KEY,
            horizontal=True,
        )


def render_page_title(text: str, *, subtitle: str | None = None) -> None:
    """모드에 따라 제목 영역을 다르게 렌더한다."""
    if get_ui_mode() == UI_MODE_MODERN:
        sub = ""
        if subtitle:
            safe = html.escape(subtitle)
            sub = f'<p class="modiba-hero-sub">{safe}</p>'
        st.markdown(
            f'<div class="modiba-hero-wrap"><h1 class="modiba-hero-title">{text}</h1>{sub}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.title(text)
        if subtitle:
            st.caption(subtitle)


def section_heading(text: str, level: int = 2) -> None:
    tag = "h2" if level == 2 else "h3"
    if get_ui_mode() == UI_MODE_MODERN:
        cls = "modiba-section-h2" if level == 2 else "modiba-section-h3"
        safe = html.escape(text)
        st.markdown(f'<{tag} class="{cls}">{safe}</{tag}>', unsafe_allow_html=True)
    else:
        if level == 2:
            st.markdown(f"## {text}")
        else:
            st.markdown(f"### {text}")


def _dark_theme_css() -> str:
    return """
<style>
  /* Base app */
  .stApp {
    background: linear-gradient(165deg, #0a0d12 0%, #0f1419 45%, #0c1016 100%);
    color: #e6edf3;
  }
  [data-testid="stAppViewContainer"] > .main {
    background: transparent;
  }
  [data-testid="stHeader"] {
    background: rgba(10, 13, 18, 0.92);
    border-bottom: 1px solid #21262d;
  }
  div[data-testid="stDecoration"] {
    display: none;
  }
  .block-container {
    padding-top: 1.25rem;
    padding-bottom: 3rem;
    max-width: 100%;
  }
  /* Typography */
  .modiba-hero-wrap {
    margin: 0 0 1.25rem 0;
    padding: 1.1rem 1.25rem 1.15rem;
    background: linear-gradient(135deg, rgba(22, 27, 34, 0.95) 0%, rgba(15, 20, 28, 0.88) 100%);
    border: 1px solid #30363d;
    border-radius: 12px;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.35);
  }
  .modiba-hero-title {
    font-size: 1.65rem;
    font-weight: 650;
    letter-spacing: -0.02em;
    margin: 0;
    line-height: 1.25;
    color: #f0f6fc !important;
    border: none;
  }
  .modiba-hero-sub {
    margin: 0.45rem 0 0 0;
    font-size: 0.9rem;
    color: #8b949e;
    line-height: 1.45;
  }
  .modiba-section-h2 {
    font-size: 1.15rem;
    font-weight: 600;
    color: #f0f6fc !important;
    margin: 1.35rem 0 0.65rem 0;
    letter-spacing: -0.01em;
    border-left: 3px solid #58a6ff;
    padding-left: 0.65rem;
  }
  .modiba-section-h3 {
    font-size: 1rem;
    font-weight: 600;
    color: #e6edf3 !important;
    margin: 0.75rem 0 0.5rem 0;
  }
  .stMarkdown, .stMarkdown p, .stMarkdown li {
    color: #c9d1d9;
  }
  .stMarkdown strong {
    color: #f0f6fc;
  }
  /* Streamlit widgets */
  label[data-testid="stWidgetLabel"] p {
    color: #adbac7 !important;
    font-weight: 500;
  }
  .stTextInput input,
  .stDateInput input,
  div[data-baseweb="input"] > input {
    background-color: #161b22 !important;
    color: #e6edf3 !important;
    border-color: #30363d !important;
    border-radius: 8px !important;
  }
  .stRadio div[role="radiogroup"] label {
    color: #c9d1d9 !important;
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 0.35rem 0.75rem;
  }
  /* Metrics */
  [data-testid="stMetric"] {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 10px;
    padding: 0.65rem 0.85rem;
    min-height: 5.5rem;
  }
  [data-testid="stMetricLabel"] {
    color: #8b949e !important;
  }
  [data-testid="stMetricValue"] {
    color: #7ee787 !important;
    font-weight: 600;
  }
  [data-testid="stMetricDelta"] {
    color: #79c0ff !important;
  }
  [data-testid="stMetricDelta"] svg {
    fill: #79c0ff !important;
  }
  /* Bordered container (KPI block) */
  div[data-testid="stVerticalBlockBorderWrapper"] {
    background: rgba(22, 27, 34, 0.55);
    border: 1px solid #30363d !important;
    border-radius: 12px;
    padding: 0.5rem 0.35rem;
  }
  /* Tabs */
  .stTabs [data-baseweb="tab-list"] {
    background: transparent;
    gap: 4px;
    border-bottom: 1px solid #30363d;
  }
  .stTabs [data-baseweb="tab"] {
    color: #8b949e;
    border-radius: 8px 8px 0 0;
  }
  .stTabs [aria-selected="true"] {
    background: #21262d !important;
    color: #f0f6fc !important;
    border: 1px solid #30363d;
    border-bottom: none !important;
  }
  /* Dataframe / grid — 래퍼·툴바는 DOM이라 어둡게, 셀 색은 data_grid Styler로 보강 */
  div[data-testid="stDataFrame"] {
    border: 1px solid #30363d;
    border-radius: 10px;
    overflow: hidden;
    background-color: #0d1117 !important;
  }
  div[data-testid="stDataFrame"] > div {
    background-color: #0d1117 !important;
  }
  div[data-testid="stDataFrame"] [data-testid="stElementToolbar"] {
    background-color: #161b22 !important;
    border-bottom: 1px solid #30363d;
  }
  div[data-testid="stDataFrame"] [data-testid="stElementToolbar"] button,
  div[data-testid="stDataFrame"] [data-testid="stElementToolbar"] [role="button"] {
    color: #e6edf3 !important;
  }
  /* Alerts */
  div[data-testid="stAlert"] {
    border-radius: 10px;
  }
  [data-testid="stAlert"] [data-testid="stMarkdownContainer"] p {
    color: inherit;
  }
  /* Expander */
  .streamlit-expanderHeader {
    color: #c9d1d9 !important;
    font-weight: 500;
  }
  details[data-testid="stExpander"] {
    border: 1px solid #30363d;
    border-radius: 10px;
    background: rgba(22, 27, 34, 0.4);
  }
  /* Dividers */
  hr {
    border-color: #30363d !important;
    margin: 1.25rem 0;
  }
  /* Login page is separate; if user lands here after theme, buttons */
  .stButton > button {
    border-radius: 8px;
    font-weight: 500;
  }
</style>
"""

