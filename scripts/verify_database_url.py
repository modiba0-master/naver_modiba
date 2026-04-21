#!/usr/bin/env python3
"""
DATABASE_URL 형식 검증 + 실제 연결(SELECT 1). 비밀번호는 출력하지 않는다.

  python scripts/verify_database_url.py
  python scripts/verify_database_url.py --use-local-url

레포 루트의 .env 를 로드한 뒤(있으면), 환경변수 DATABASE_URL 을 사용한다.

비밀번호에 한글 등 비ASCII 문자가 있으면 URL에는 UTF-8 퍼센트 인코딩이 필요하다.
(`latin-1` codec can't encode … 오류 시 아래 진단 메시지 참고)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import quote_plus

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.chdir(_ROOT)

try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")
except ImportError:
    pass

from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url


def _at_signs_after_authority(url: str) -> int:
    """`scheme://` 이후 문자열에 등장하는 `@` 개수. 정상 MySQL URL은 보통 1개( user:pass @ host )."""
    pos = url.find("://")
    tail = url[pos + 3 :] if pos >= 0 else url
    return tail.count("@")


def _mysql_parse_hints(url: str, u) -> list[str]:
    hints: list[str] = []
    driver = (u.drivername or "").lower()
    if "mysql" not in driver:
        return hints
    n_at = _at_signs_after_authority(url)
    if n_at > 1:
        hints.append(
            f"`@`가 user:password 구간 뒤가 아닌 곳에 {n_at}번 있습니다. "
            "비밀번호에 `@`가 있으면 반드시 `%40`으로 바꾸세요."
        )
    if u.username is None or str(u.username or "").strip() == "":
        hints.append("사용자명(username)이 비어 있습니다. `mysql+pymysql://사용자:비밀번호@호스트/DB` 형식인지 확인하세요.")
    if u.database is None or str(u.database or "").strip() == "":
        hints.append("데이터베이스 이름이 비어 있습니다. URL 끝에 `/railway` 처럼 DB명이 있는지 확인하세요.")
    if u.host is not None:
        h = str(u.host).strip()
        if h in ("", ".", "..") or (len(h) == 3 and h.replace(".", "") == ""):
            hints.append(f"호스트 값이 비정상입니다({h!r}). 복사·붙여넣기 오류 또는 미완성 URL일 수 있습니다.")
        if len(h) > 253:
            hints.append("호스트 문자열이 너무 깁니다. URL이 잘렸는지 확인하세요.")
    return hints


def _redacted_summary(url: str) -> str:
    u = make_url(url)
    parts = [
        f"driver={u.drivername!r}",
        f"username={u.username!r}",
        f"host={u.host!r}",
        f"port={u.port!r}",
        f"database={u.database!r}",
    ]
    return ", ".join(parts)


def _masked_url(url: str) -> str:
    """로그용 마스킹 URL. 비밀번호는 항상 *** 로 숨긴다."""
    u = make_url(url)
    user = u.username or ""
    host = u.host or ""
    port = f":{u.port}" if u.port else ""
    database = f"/{u.database}" if u.database else ""
    if user:
        return f"{u.drivername}://{user}:***@{host}{port}{database}"
    return f"{u.drivername}://{host}{port}{database}"


def _build_mariadb_public_url(
    password: str,
    *,
    user: str,
    host: str,
    port: int,
    database: str,
) -> str:
    encoded_password = quote_plus(password, safe="")
    return f"mysql+pymysql://{user}:{encoded_password}@{host}:{port}/{database}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="DATABASE_URL/MariaDB Public 비밀번호로 DB 연결을 검증합니다.",
    )
    parser.add_argument(
        "--prefer-mariadb-public",
        action="store_true",
        help="MARIADB_PUBLIC_PASSWORD가 있으면 DATABASE_URL보다 우선 사용",
    )
    parser.add_argument(
        "--mariadb-user",
        default=os.environ.get("MARIADB_USER", "railway"),
        help="자동 생성 URL 사용자명 (기본: railway)",
    )
    parser.add_argument(
        "--mariadb-host",
        default=os.environ.get("MARIADB_PUBLIC_HOST", "monorail.proxy.rlwy.net"),
        help="자동 생성 URL 호스트",
    )
    parser.add_argument(
        "--mariadb-port",
        type=int,
        default=int(os.environ.get("MARIADB_PUBLIC_PORT", "19572")),
        help="자동 생성 URL 포트",
    )
    parser.add_argument(
        "--mariadb-database",
        default=os.environ.get("MARIADB_DATABASE", "railway"),
        help="자동 생성 URL DB명",
    )
    parser.add_argument(
        "--show-masked-url",
        action="store_true",
        help="실제 시도 URL을 비밀번호 마스킹 형태로 출력",
    )
    parser.add_argument(
        "--use-local-url",
        action="store_true",
        help="DATABASE_URL_LOCAL(로컬 외부접속용)를 DATABASE_URL보다 우선 사용",
    )
    args = parser.parse_args()

    env_path = _ROOT / ".env"
    url = os.environ.get("DATABASE_URL", "").strip().strip('"').strip("'")
    source = "환경변수 DATABASE_URL"
    local_url = os.environ.get("DATABASE_URL_LOCAL", "").strip().strip('"').strip("'")
    public_pw = os.environ.get("MARIADB_PUBLIC_PASSWORD", "").strip().strip('"').strip("'")

    if args.use_local_url and local_url:
        url = local_url
        source = "환경변수 DATABASE_URL_LOCAL"

    if args.prefer_mariadb_public and public_pw:
        url = _build_mariadb_public_url(
            public_pw,
            user=args.mariadb_user,
            host=args.mariadb_host,
            port=args.mariadb_port,
            database=args.mariadb_database,
        )
        source = "환경변수 MARIADB_PUBLIC_PASSWORD (+ 기본 호스트/포트/DB)"

    if not url and public_pw:
        url = _build_mariadb_public_url(
            public_pw,
            user=args.mariadb_user,
            host=args.mariadb_host,
            port=args.mariadb_port,
            database=args.mariadb_database,
        )
        source = "환경변수 MARIADB_PUBLIC_PASSWORD (+ 기본 호스트/포트/DB)"

    if not url and env_path.is_file():
        # load_dotenv 이후에도 비어 있으면 .env 안에 키가 없는 것
        print(f"참고: {_ROOT / '.env'} 파일은 있으나 DATABASE_URL이 비어 있거나 키가 없습니다.", file=sys.stderr)
    if not url:
        try:
            from app.config import settings as app_settings

            url = str(app_settings.database_url or "").strip()
            source = "app.config.settings.database_url (.env / 환경변수 경유)"
        except Exception:
            url = ""

    if url.startswith("sqlite") and not os.environ.get("DATABASE_URL", "").strip():
        print(
            "경고: 환경변수 DATABASE_URL이 없어 SQLite 기본값만 검증합니다. "
            "MariaDB를 확인하려면 DATABASE_URL을 설정하세요.",
            file=sys.stderr,
        )

    if not url:
        print(
            "DATABASE_URL이 비어 있습니다.\n"
            "  - 레포 루트 `.env` 에 `DATABASE_URL=mysql+pymysql://...` 를 넣거나\n"
            "  - 로컬 확인은 `.env` 의 `DATABASE_URL_LOCAL=mysql+pymysql://...` 를 사용하거나\n"
            "  - PowerShell: `$env:DATABASE_URL='...'` 또는 `$env:MARIADB_PUBLIC_PASSWORD='...'` 후 다시 실행하세요.\n"
            "  - 자동 URL 생성: `python scripts/verify_database_url.py --prefer-mariadb-public`\n"
            "  - 로컬 URL 우선: `python scripts/verify_database_url.py --use-local-url`",
            file=sys.stderr,
        )
        return 1
    try:
        u = make_url(url)
    except Exception as exc:
        print(f"URL 파싱 실패: {exc}", file=sys.stderr)
        return 1

    driver = (u.drivername or "").lower()
    if "mysql" in driver and not (u.host and str(u.host).strip()):
        print(
            "MySQL URL에 호스트가 없습니다. 비밀번호의 @ 는 %40 로 인코딩했는지 확인하세요.",
            file=sys.stderr,
        )
        return 1

    print("사용 출처:", source)
    print("파싱 결과:", _redacted_summary(url))
    if args.show_masked_url:
        print("시도 URL(마스킹):", _masked_url(url))
    hints = _mysql_parse_hints(url, u)
    for line in hints:
        print("진단:", line, file=sys.stderr)
    if hints:
        print(
            "\n올바른 형식 예시(값은 Railway에서 복사):\n"
            "  mysql+pymysql://USER:PASSWORD@HOSTNAME:PORT/DATABASE\n"
            "예: mysql+pymysql://railway:비밀번호@monorail.proxy.rlwy.net:19572/railway",
            file=sys.stderr,
        )
        return 1

    def _root_exc(exc: BaseException) -> BaseException:
        r: BaseException = exc
        while r.__cause__ is not None:
            r = r.__cause__
        return r

    try:
        engine = create_engine(
            url,
            pool_pre_ping=True,
            connect_args={"charset": "utf8mb4"},
        )
        with engine.connect() as conn:
            one = conn.execute(text("SELECT 1")).scalar()
    except Exception as exc:
        root = _root_exc(exc)
        msg = str(exc).lower()
        is_latin1_auth = isinstance(root, UnicodeEncodeError) or (
            "latin-1" in msg and "codec" in msg and "encode" in msg
        )
        if is_latin1_auth:
            print(f"연결 실패: {type(exc).__name__}: {exc}", file=sys.stderr)
            print(
                "원인: MySQL 인증 경로에서 latin-1로만내려다 실패했습니다.\n"
                "  - 비밀번호·사용자명에 한글·스마트따옴표·보이지 않는 문자가 있으면 그대로는 안 됩니다.\n"
                "  - Railway에서 복사한 **실제 비밀번호**만 quote_plus에 넣으세요. "
                "문구 '여기에실제비밀번호'는 예시일 뿐입니다.\n"
                "  - ASCII만 있는 비밀번호라면, 앞뒤 공백·따옴표가 URL에 섞였는지 확인하세요.",
                file=sys.stderr,
            )
            user = u.username or ""
            pw = str(u.password or "")
            if any(ord(c) > 127 for c in pw) or any(ord(c) > 127 for c in user):
                enc_user = quote_plus(user, safe="") if user else ""
                enc_pw = quote_plus(pw, safe="")
                host = u.host or ""
                port = u.port or 3306
                db = u.database or ""
                print(
                    "인코딩된 URL 예시(터미널에만 출력):\n"
                    f"  mysql+pymysql://{enc_user}:{enc_pw}@{host}:{port}/{db}",
                    file=sys.stderr,
                )
            return 2
        if isinstance(root, UnicodeError):
            print(
                f"연결 실패: {type(exc).__name__}: {exc}\n"
                "호스트 DNS(IDNA) 등 유니코드 관련 오류일 수 있습니다. host·`@`(%40)를 확인하세요.",
                file=sys.stderr,
            )
            return 2
        print(f"연결 실패: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if one != 1:
        print(f"SELECT 1 비정상 응답: {one!r}", file=sys.stderr)
        return 2

    print("연결 성공: SELECT 1 OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
