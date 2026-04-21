"""DATABASE_URL 정규화·진단 (FastAPI·Streamlit 공통)."""

from __future__ import annotations

import os
from urllib.parse import quote_plus

from sqlalchemy.engine.url import make_url


def encode_mysql_password_in_url(url: str) -> str:
    """파싱 실패 시 ``user:password@host`` 의 password만 ``quote_plus`` (``@ # :`` 등)."""
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


def mask_database_url(url: str) -> str:
    if not url or url.startswith("sqlite"):
        return url
    try:
        u = make_url(url)
        if u.password:
            return url.replace(u.password, "***", 1)
    except Exception:
        pass
    return url


def print_database_url_diagnostics(
    url: str,
    *,
    label: str = "DATABASE_URL",
    print_public_sibling: bool = True,
) -> None:
    """표준 출력으로 호스트·public 여부·마스킹 URL 표시."""
    print(f"[config] --- {label} ---")
    if not url:
        print(f"[config] {label} is empty")
        return
    if url.startswith("sqlite"):
        print(f"[config] {label}: {url}")
        return
    try:
        u = make_url(url)
        host = u.host
        port = u.port
        is_public = True
        if host:
            h = str(host).lower()
            if h.endswith(".internal") or ".internal." in h or h.endswith(".railway.internal"):
                is_public = False
        print(f"[config] {label} host={host!r} port={port!r} public_host={is_public}")
        print(f"[config] {label} (masked): {mask_database_url(url)}")
    except Exception as exc:
        print(f"[config] {label} parse error: {exc}")
        print(f"[config] {label} (raw masked attempt): {url[:80]}..." if len(url) > 80 else f"[config] raw: {url}")

    if print_public_sibling and label == "DATABASE_URL":
        pub = os.getenv("DATABASE_PUBLIC_URL", "").strip()
        if pub:
            print_database_url_diagnostics(
                pub, label="DATABASE_PUBLIC_URL", print_public_sibling=False
            )
