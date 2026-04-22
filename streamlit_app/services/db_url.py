"""Streamlit 전용 `DATABASE_URL` — Railway가 `streamlit_app`만 배포해도 `app` 패키지가 없어도 동작.

`app.config`·`app.db_url_utils`와 동일한 규칙을 유지한다(중복 — 변경 시 양쪽 맞출 것)."""

from __future__ import annotations

import os
from urllib.parse import quote_plus

from sqlalchemy.engine.url import make_url


def encode_mysql_password_in_url(url: str) -> str:
    if not url or url.startswith("sqlite"):
        return url
    if "://" not in url:
        return url
    try:
        make_url(url)
        return url
    except Exception:
        pass
    scheme, rest = url.split("://", 1)
    if "/" in rest:
        cred_host, path_tail = rest.split("/", 1)
        path = "/" + path_tail
    else:
        cred_host, path = rest, ""
    if "@" not in cred_host:
        return url
    userpass, hostport = cred_host.rsplit("@", 1)
    user, sep, password = userpass.partition(":")
    if not sep:
        return url
    enc = quote_plus(password, safe="")
    return f"{scheme}://{user}:{enc}@{hostport}{path}"


def get_streamlit_database_url() -> str:
    value = os.getenv("DATABASE_URL", "")
    if os.getenv("DATABASE_URL_USE_PUBLIC", "").lower() in ("1", "true", "yes"):
        pub = os.getenv("DATABASE_PUBLIC_URL", "").strip().strip('"').strip("'")
        if pub:
            value = pub
    url = str(value or "").strip().strip('"').strip("'")
    if url.startswith("mariadb://"):
        url = url.replace("mariadb://", "mysql+pymysql://", 1)
    if url.startswith("mysql://") and "pymysql" not in url:
        url = url.replace("mysql://", "mysql+pymysql://", 1)
    url = encode_mysql_password_in_url(url)
    if not url or url.startswith("sqlite"):
        return url
    if "${{" in url or "{{" in url:
        raise ValueError(
            "DATABASE_URL에 ${{...}} 참조가 그대로 들어가 있습니다. "
            "Railway에서 치환된 완성 문자열을 사용하세요."
        )
    try:
        make_url(url)
    except Exception as exc:
        raise ValueError(
            "DATABASE_URL을 SQLAlchemy가 파싱할 수 없습니다. "
            "비밀번호에 특수문자가 있으면 인코딩이 필요할 수 있습니다. "
            f"원인: {exc}"
        ) from exc
    return url
