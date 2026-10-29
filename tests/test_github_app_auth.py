#!/usr/bin/env python3
"""GitHub App auth tests. The token endpoint is local. No live GitHub, no private key file in the repo."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator
from urllib.parse import parse_qs, unquote, urlparse

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "mcp"))

from egregore_mcp import github_app  # noqa: E402
from egregore_mcp.github_api import GitHubError, RestGitHub  # noqa: E402
from egregore_mcp.service import service_from_env  # noqa: E402

START = datetime(2026, 10, 28, 0, 0, tzinfo=timezone.utc)
APP_ID = "424242"
INSTALLATION_ID = "4242"
PAT = "local-test-token-not-a-real-secret"
REPO = "AStrasler/Egeregore"
OTHER = "AStrasler/Other"
ALLOWED = {"issues": "write", "pull_requests": "read", "metadata": "read"}
APP_KEYS = (
    "EGREGORE_APP_ID",
    "EGREGORE_APP_INSTALLATION_ID",
    "EGREGORE_APP_PRIVATE_KEY",
    "EGREGORE_APP_PRIVATE_KEY_PATH",
    "EGREGORE_GITHUB_TOKEN",
    "EGREGORE_AGENT",
    "EGREGORE_REPO",
    "EGREGORE_GITHUB_API",
)


def iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class AppState:
    def __init__(self, public_key: str) -> None:
        self.public_key = public_key
        self.default_expiry = iso(START + timedelta(hours=1))
        self.reset()

    def reset(self) -> None:
        self.token_calls = 0
        self.discovery_calls = 0
        self.list_calls = 0
        self.token_bodies: list[dict[str, object]] = []
        self.token_headers: list[dict[str, str]] = []
        self.jwts: list[str] = []
        self.issued: list[str] = []
        self.repo_bearers: list[str] = []
        self.list_bearers: list[str] = []
        self.permissions: dict[str, str] | None = dict(ALLOWED)
        self.repositories: list[dict[str, str]] = [{"full_name": REPO, "name": "Egeregore"}]
        self.page2: list[dict[str, str]] = []
        self.include_repositories = True
        self.repository_selection = "selected"
        self.fail_on: set[int] = set()
        self.leak = ""
        self.expiry_list: list[str] = []
        self.fail_repo = False
        self.tokens = [
            "ghs_install_token_one_aaaaaaaa",
            "ghs_install_token_two_bbbbbbbb",
            "ghs_install_token_three_cccccc",
        ]
        self.next_token = 0

    def expiry_for(self, index: int) -> str:
        if index < len(self.expiry_list):
            return self.expiry_list[index]
        return self.default_expiry


def _pem_pair() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    return private, public


@contextmanager
def env(**values: str) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in APP_KEYS}
    for key in APP_KEYS:
        os.environ.pop(key, None)
    for key, value in values.items():
        os.environ[key] = value
    try:
        yield
    finally:
        for key, old in previous.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


def _handler(state: AppState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, fmt: str, *args: object) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            self._route("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._route("POST")

        def _route(self, method: str) -> None:
            parsed = urlparse(self.path)
            parts = [unquote(part) for part in parsed.path.split("/") if part]
            if (
                len(parts) == 4
                and parts[0] == "app"
                and parts[1] == "installations"
                and parts[3] == "access_tokens"
            ):
                self._token(method, parts[2])
                return
            if len(parts) == 4 and parts[0] == "repos" and parts[3] == "installation":
                self._discovery(method)
                return
            if parts == ["installation", "repositories"]:
                self._repo_list(method, parse_qs(parsed.query))
                return
            if parts[:3] == ["repos", "AStrasler", "Egeregore"]:
                self._repo(method, parts[3:])
                return
            self._send(404, {"message": "not found"})

        def _token(self, method: str, installation_id: str) -> None:
            if method != "POST" or not self._jwt_ok():
                self._send(401, {"message": "bad jwt"})
                return
            state.jwts.append(self._bearer())
            body = self._json()
            state.token_bodies.append(body)
            state.token_headers.append(
                {
                    "accept": self.headers.get("Accept") or "",
                    "version": self.headers.get("X-GitHub-Api-Version") or "",
                    "user_agent": self.headers.get("User-Agent") or "",
                    "content_type": self.headers.get("Content-Type") or "",
                }
            )
            state.token_calls += 1
            if state.token_calls in state.fail_on:
                leaked = f"{self.headers.get('Authorization') or ''} {state.leak}"
                self._send(401, {"message": leaked})
                return
            if installation_id not in {INSTALLATION_ID, "7777"}:
                self._send(404, {"message": "unknown installation"})
                return
            token = state.tokens[min(state.next_token, len(state.tokens) - 1)]
            state.next_token += 1
            state.issued.append(token)
            payload: dict[str, object] = {
                "token": token,
                "expires_at": state.expiry_for(state.token_calls - 1),
                "repository_selection": state.repository_selection,
            }
            if state.permissions is not None:
                payload["permissions"] = state.permissions
            if state.include_repositories:
                payload["repositories"] = state.repositories
            self._send(201, payload)

        def _discovery(self, method: str) -> None:
            state.discovery_calls += 1
            if method != "GET" or not self._jwt_ok():
                self._send(401, {"message": "bad jwt"})
                return
            state.jwts.append(self._bearer())
            self._send(200, {"id": int(INSTALLATION_ID), "repository_selection": "selected"})

        def _repo_list(self, method: str, query: dict[str, list[str]]) -> None:
            state.list_calls += 1
            token = self._bearer()
            if token not in state.issued:
                self._send(401, {"message": "bad installation token"})
                return
            state.list_bearers.append(token)
            if method != "GET":
                self._send(404, {"message": "not found"})
                return
            page = query.get("page", ["1"])[0]
            if page == "2":
                repos = state.page2
                link = None
            else:
                repos = state.repositories
                link = None
                if state.page2:
                    host, port = self.server.server_address[:2]
                    link = f'<http://{host}:{port}/installation/repositories?page=2>; rel="next"'
            total = len(state.repositories) + len(state.page2)
            self._send(200, {"total_count": total, "repositories": repos}, link=link)

        def _repo(self, method: str, rest: list[str]) -> None:
            token = self._bearer()
            if token not in state.issued:
                self._send(401, {"message": "bad installation token"})
                return
            state.repo_bearers.append(token)
            if state.fail_repo:
                self._send(500, {"message": self.headers.get("Authorization") or ""})
                return
            if method == "GET" and rest == ["issues"]:
                self._send(200, [])
                return
            if method == "GET" and rest == ["issues", "1"]:
                self._send(200, {"number": 1, "title": "One", "body": "", "state": "open", "labels": []})
                return
            self._send(404, {"message": "not found"})

        def _jwt_ok(self) -> bool:
            token = self._bearer()
            try:
                claims = jwt.decode(
                    token,
                    state.public_key,
                    algorithms=["RS256"],
                    options={"verify_exp": False, "verify_iat": False},
                )
            except Exception:
                return False
            header = jwt.get_unverified_header(token)
            return header.get("alg") == "RS256" and str(claims.get("iss")) == APP_ID

        def _bearer(self) -> str:
            header = self.headers.get("Authorization") or ""
            if not header.startswith("Bearer "):
                return ""
            return header.removeprefix("Bearer ").strip()

        def _json(self) -> dict[str, object]:
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length else b""
            if not raw:
                return {}
            value = json.loads(raw.decode("utf-8"))
            return value if isinstance(value, dict) else {}

        def _send(self, status: int, payload: object, link: str | None = None) -> None:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            if link:
                self.send_header("Link", link)
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)

    return Handler


class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address) -> None:  # noqa: ANN001
        return


class ConfigTests(unittest.TestCase):
    def test_gitignore_ignores_pem_files(self) -> None:
        text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("*.pem", text)

    def test_permission_constant_is_the_ceiling(self) -> None:
        self.assertEqual(github_app.REQUESTED_PERMISSIONS, ALLOWED)

    def test_pat_fallback_does_not_call_the_app(self) -> None:
        with env(
            EGREGORE_AGENT="cursor",
            EGREGORE_REPO=REPO,
            EGREGORE_GITHUB_TOKEN=PAT,
            EGREGORE_GITHUB_API="http://127.0.0.1:9",
        ):
            service = service_from_env()
        self.assertEqual(service.auth_mode, "token")
        self.assertIsNotNone(service.github)
        assert service.github is not None
        self.assertIsNone(service.github._token_provider)
        self.assertEqual(service.github._token, PAT)
        identity = service._identity()
        self.assertEqual(identity["auth"], "token")
        self.assertFalse(identity["github_login_is_separate"])

    def test_partial_app_config_does_not_fall_back_to_the_pat(self) -> None:
        with env(EGREGORE_AGENT="cursor", EGREGORE_REPO=REPO, EGREGORE_APP_ID=APP_ID, EGREGORE_GITHUB_TOKEN=PAT):
            with self.assertRaises(SystemExit) as caught:
                service_from_env()
        message = str(caught.exception)
        self.assertIn("EGREGORE_APP_PRIVATE_KEY", message)
        self.assertNotIn(PAT, message)

    def test_bad_repo_refuses_before_a_token_request(self) -> None:
        with env(
            EGREGORE_AGENT="cursor",
            EGREGORE_REPO="not a repo",
            EGREGORE_APP_ID=APP_ID,
            EGREGORE_APP_PRIVATE_KEY="unused",
            EGREGORE_GITHUB_API="http://127.0.0.1:9",
        ):
            with self.assertRaises(SystemExit) as caught:
                service_from_env()
        self.assertIn("owner/name", str(caught.exception))


class FakeEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pem, public = _pem_pair()
        cls.pem_line = next(line for line in cls.pem.splitlines() if not line.startswith("-----"))
        cls.state = AppState(public)
        cls.server = QuietServer(("127.0.0.1", 0), _handler(cls.state))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.server.server_address[:2]
        cls.api = f"http://{host}:{port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        self.state.reset()
        self.state.default_expiry = iso(START + timedelta(hours=1))

    def test_discovers_installation_and_caches_until_the_skew(self) -> None:
        clock = Clock(START)
        with env(**self._app_env()):
            service = service_from_env(clock)
        self.assertEqual(service.auth_mode, "github_app")
        self.assertEqual(self.state.discovery_calls, 1)
        self.assertEqual(self.state.token_calls, 1)
        self.assertEqual(self.state.list_calls, 0)
        body = self.state.token_bodies[0]
        self.assertEqual(set(body), {"repositories", "permissions"})
        self.assertEqual(body["repositories"], ["Egeregore"])
        self.assertEqual(body["permissions"], ALLOWED)
        header = self.state.token_headers[0]
        self.assertEqual(header["accept"], "application/vnd.github+json")
        self.assertEqual(header["version"], "2022-11-28")
        self.assertEqual(header["user_agent"], "egregore-mcp/0.1")
        self.assertEqual(header["content_type"], "application/json")
        self.assertGreaterEqual(len(self.state.jwts), 2)
        for encoded in self.state.jwts:
            self._assert_jwt(encoded, clock)
        listed = service.get_owner_decisions()
        self.assertTrue(listed["ok"], listed)
        self.assertEqual(listed["result"]["identity"]["auth"], "github_app")
        self.assertTrue(listed["result"]["identity"]["github_login_is_separate"])
        self.assertEqual(self.state.token_calls, 1)
        self.assertEqual(self.state.repo_bearers, [self.state.issued[0]])
        self.assertNotIn(PAT, self.state.repo_bearers)
        clock.now = START + timedelta(minutes=54)
        service.github.get_issue(1)  # type: ignore[union-attr]
        self.assertEqual(self.state.token_calls, 1)
        clock.now = START + timedelta(minutes=55)
        service.github.get_issue(1)  # type: ignore[union-attr]
        self.assertEqual(self.state.token_calls, 2)
        self.assertEqual(self.state.repo_bearers[-2:], self.state.issued)
        self.assertNotEqual(self.state.issued[0], self.state.issued[1])

    def test_explicit_installation_id_skips_discovery(self) -> None:
        with env(**self._app_env(EGREGORE_APP_INSTALLATION_ID="7777")):
            service_from_env(Clock(START))
        self.assertEqual(self.state.discovery_calls, 0)
        self.assertEqual(self.state.token_calls, 1)

    def test_short_lived_token_is_used_until_it_expires(self) -> None:
        self.state.expiry_list = [iso(START + timedelta(minutes=3)), iso(START + timedelta(hours=1))]
        clock = Clock(START)
        with env(**self._app_env(EGREGORE_APP_INSTALLATION_ID=INSTALLATION_ID)):
            service = service_from_env(clock)
        self.assertEqual(self.state.token_calls, 1)
        clock.now = START + timedelta(minutes=2)
        service.github.get_issue(1)  # type: ignore[union-attr]
        self.assertEqual(self.state.token_calls, 1)
        clock.now = START + timedelta(minutes=3)
        service.github.get_issue(1)  # type: ignore[union-attr]
        self.assertEqual(self.state.token_calls, 2)

    def test_failed_refresh_keeps_an_unexpired_token_and_scrubs_it(self) -> None:
        clock = Clock(START)
        with env(**self._app_env(EGREGORE_APP_INSTALLATION_ID=INSTALLATION_ID)):
            service = service_from_env(clock)
        clock.now = START + timedelta(minutes=55)
        self.state.fail_on = {2, 3}
        self.state.leak = self.pem
        issue = service.github.get_issue(1)  # type: ignore[union-attr]
        self.assertEqual(issue["number"], 1)
        self.assertEqual(self.state.token_calls, 2)
        self.assertEqual(self.state.repo_bearers[-1], self.state.issued[0])
        clock.now = START + timedelta(minutes=61)
        with self.assertRaises(GitHubError) as caught:
            service.github.get_issue(1)  # type: ignore[union-attr]
        message = caught.exception.message
        self.assertIn("[redacted]", message)
        self.assertNotIn(self.state.issued[0], message)
        self.assertNotIn("eyJ", message)
        self.assertNotIn(self.pem_line, message)
        self.assertNotIn("PRIVATE KEY", message)

    def test_repo_error_scrubs_the_installation_token(self) -> None:
        with env(**self._app_env(EGREGORE_APP_INSTALLATION_ID=INSTALLATION_ID)):
            service = service_from_env(Clock(START))
        self.state.fail_repo = True
        with self.assertRaises(GitHubError) as caught:
            service.github.get_issue(1)  # type: ignore[union-attr]
        self.assertNotIn(self.state.issued[0], caught.exception.message)
        self.assertIn("[redacted]", caught.exception.message)

    def test_extra_repository_on_the_token_refuses_to_start(self) -> None:
        self.state.repositories = [
            {"full_name": REPO, "name": "Egeregore"},
            {"full_name": OTHER, "name": "Other"},
        ]
        message = self._refused()
        self.assertIn(OTHER, message)
        self.assertIn("only AStrasler/Egeregore", message)
        self.assertEqual(self.state.list_calls, 0)
        self._assert_clean(message)

    def test_extra_repository_on_a_later_page_refuses_to_start(self) -> None:
        self.state.include_repositories = False
        self.state.page2 = [{"full_name": OTHER, "name": "Other"}]
        message = self._refused()
        self.assertIn(OTHER, message)
        self.assertEqual(self.state.list_calls, 2)
        self.assertEqual(self.state.list_bearers, [self.state.issued[0], self.state.issued[0]])
        self._assert_clean(message)

    def test_repository_selection_all_refuses_to_start(self) -> None:
        self.state.repository_selection = "all"
        message = self._refused()
        self.assertIn("repository_selection is all", message)
        self._assert_clean(message)

    def test_empty_repository_list_refuses_to_start(self) -> None:
        self.state.repositories = []
        message = self._refused()
        self.assertIn("(empty)", message)

    def test_broader_or_narrower_permissions_refuse_to_start(self) -> None:
        cases = [
            {"issues": "write", "pull_requests": "write", "metadata": "read"},
            {"issues": "write", "pull_requests": "read", "metadata": "read", "contents": "read"},
            {"issues": "admin", "pull_requests": "read", "metadata": "read"},
            {"issues": "read", "pull_requests": "read", "metadata": "read"},
            {"issues": "write", "pull_requests": "read"},
            {},
        ]
        for permissions in cases:
            with self.subTest(permissions=permissions):
                self.state.reset()
                self.state.permissions = permissions
                message = self._refused(installation_id=INSTALLATION_ID)
                self.assertIn("permissions must be", message)
                self.assertEqual(self.state.token_bodies[-1]["permissions"], ALLOWED)
                self._assert_clean(message)

    def test_missing_permissions_object_refuses_to_start(self) -> None:
        self.state.permissions = None
        message = self._refused(installation_id=INSTALLATION_ID)
        self.assertIn("permissions were missing", message)

    def test_startup_error_scrubs_the_jwt_and_the_private_key(self) -> None:
        self.state.fail_on = {1}
        self.state.leak = self.pem
        message = self._refused(installation_id=INSTALLATION_ID)
        self.assertIn("GitHub HTTP 401", message)
        self.assertIn("[redacted]", message)
        self.assertEqual(self.state.token_bodies[-1]["permissions"], ALLOWED)
        self.assertEqual(self.state.token_bodies[-1]["repositories"], ["Egeregore"])
        self._assert_clean(message)

    def test_unusable_private_key_does_not_echo_the_material(self) -> None:
        bogus = "-----BEGIN PRIVATE KEY-----\nnot-a-real-key-body\n-----END PRIVATE KEY-----\n"
        with env(
            **self._app_env(EGREGORE_APP_INSTALLATION_ID=INSTALLATION_ID, EGREGORE_APP_PRIVATE_KEY=bogus)
        ):
            with self.assertRaises(SystemExit) as caught:
                service_from_env(Clock(START))
        message = str(caught.exception)
        self.assertIn("not a usable RSA private key", message)
        self.assertNotIn("not-a-real-key-body", message)
        self.assertEqual(self.state.token_calls, 0)

    def test_inline_pem_with_escaped_newlines_and_path_fallback(self) -> None:
        escaped = self.pem.replace("\n", "\\n")
        with env(
            **self._app_env(
                EGREGORE_APP_INSTALLATION_ID=INSTALLATION_ID,
                EGREGORE_APP_PRIVATE_KEY=escaped,
                EGREGORE_APP_PRIVATE_KEY_PATH="/tmp/does-not-exist-egregore.pem",
            )
        ):
            service_from_env(Clock(START))
        self.assertEqual(self.state.token_calls, 1)

        self.state.reset()
        with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False, encoding="utf-8") as handle:
            handle.write(self.pem)
            key_path = handle.name
        self.addCleanup(lambda: Path(key_path).unlink(missing_ok=True))
        self.assertFalse(str(ROOT) in str(Path(key_path).resolve()))
        values = self._app_env(EGREGORE_APP_INSTALLATION_ID=INSTALLATION_ID)
        values.pop("EGREGORE_APP_PRIVATE_KEY")
        values["EGREGORE_APP_PRIVATE_KEY_PATH"] = key_path
        with env(**values):
            service = service_from_env(Clock(START))
        self.assertEqual(service.auth_mode, "github_app")
        self.assertEqual(self.state.token_calls, 1)

    def test_app_mode_ignores_a_personal_token(self) -> None:
        with env(**self._app_env(EGREGORE_APP_INSTALLATION_ID=INSTALLATION_ID, EGREGORE_GITHUB_TOKEN=PAT)):
            service = service_from_env(Clock(START))
        service.github.get_issue(1)  # type: ignore[union-attr]
        self.assertEqual(self.state.repo_bearers, [self.state.issued[0]])
        self.assertNotIn(PAT, self.state.repo_bearers)

    def test_refresh_that_widens_the_repository_list_stops_the_process(self) -> None:
        clock = Clock(START)
        with env(**self._app_env(EGREGORE_APP_INSTALLATION_ID=INSTALLATION_ID)):
            service = service_from_env(clock)
        self.state.repositories = [
            {"full_name": REPO, "name": "Egeregore"},
            {"full_name": OTHER, "name": "Other"},
        ]
        clock.now = START + timedelta(minutes=55)
        with self.assertRaises(SystemExit) as caught:
            service.github.get_issue(1)  # type: ignore[union-attr]
        self.assertIn(OTHER, str(caught.exception))
        self._assert_clean(str(caught.exception))

    def test_direct_client_uses_the_provider_token(self) -> None:
        clock = Clock(START)
        auth = github_app.GitHubAppAuth(
            api=self.api,
            app_id=APP_ID,
            private_key=self.pem,
            repo=REPO,
            installation_id=INSTALLATION_ID,
            now=clock,
        )
        auth.authenticate()
        client = RestGitHub(api=self.api, repo=REPO, token_provider=auth.token)
        client.get_issue(1)
        self.assertEqual(self.state.repo_bearers, [self.state.issued[0]])

    def _app_env(self, **extra: str) -> dict[str, str]:
        values = {
            "EGREGORE_AGENT": "cursor",
            "EGREGORE_REPO": REPO,
            "EGREGORE_GITHUB_API": self.api,
            "EGREGORE_APP_ID": APP_ID,
            "EGREGORE_APP_PRIVATE_KEY": self.pem,
            "EGREGORE_GITHUB_TOKEN": PAT,
        }
        values.update(extra)
        return values

    def _refused(self, installation_id: str | None = None) -> str:
        extra = {}
        if installation_id is not None:
            extra["EGREGORE_APP_INSTALLATION_ID"] = installation_id
        with env(**self._app_env(**extra)):
            with self.assertRaises(SystemExit) as caught:
                service_from_env(Clock(START))
        return str(caught.exception)

    def _assert_clean(self, message: str) -> None:
        self.assertNotIn(self.pem_line, message)
        self.assertNotIn("PRIVATE KEY", message)
        self.assertNotIn("eyJ", message)
        self.assertNotIn(PAT, message)
        for token in self.state.issued:
            self.assertNotIn(token, message)

    def _assert_jwt(self, encoded: str, clock: Clock) -> None:
        claims = jwt.decode(
            encoded,
            self.state.public_key,
            algorithms=["RS256"],
            options={"verify_exp": False, "verify_iat": False},
        )
        issued = int(clock.now.timestamp()) - 60
        self.assertEqual(claims["iss"], APP_ID)
        self.assertEqual(claims["iat"], issued)
        self.assertEqual(claims["exp"], int(clock.now.timestamp()) + 8 * 60)
        self.assertEqual(claims["exp"] - claims["iat"], 540)
        self.assertLessEqual(claims["exp"] - claims["iat"], 600)
        self.assertEqual(jwt.get_unverified_header(encoded)["alg"], "RS256")


if __name__ == "__main__":
    unittest.main()
