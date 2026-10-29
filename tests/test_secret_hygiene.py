#!/usr/bin/env python3
"""Canary secret-leak tests for the Python MCP server.

A canary is a fake secret that the test plants where a real one could travel:
the environment, a GitHub App key, a bearer header, an issue body. A hostile
fake GitHub then echoes back everything it was sent, in several shapes, and
the test collects every place output can escape: raised errors, tool replies,
stdout, stderr, log records, and the real server process. If a canary shows
up in any of it, the test fails and names the canary and the channel, never
the value.

Nothing here touches the network or a real credential. Keys are generated at
run time and canaries are assembled from parts, so no secret-shaped string is
committed. tests/secret-hygiene.manifest.json lists which source files are
covered by these tests and which are exempt, and the last class checks that
list against the tree.

The Worker has the same suite in worker/test/secret-hygiene.test.ts.
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
import os
import re
import subprocess
import sys
import threading
import traceback
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.error import URLError
from urllib.parse import unquote, urlparse

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "mcp"))
sys.path.insert(0, str(ROOT / "tests"))

import coordination as coord  # noqa: E402
from egregore_mcp import github_app  # noqa: E402
from egregore_mcp.github_api import GitHubError, RestGitHub  # noqa: E402
from egregore_mcp.service import CoordinationService, service_from_env  # noqa: E402

MANIFEST = ROOT / "tests" / "secret-hygiene.manifest.json"
REPO = "AStrasler/Egeregore"
APP_ID = "424242"
INSTALLATION_ID = "4242"
START = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
ENV_KEYS = (
    "EGREGORE_AGENT",
    "EGREGORE_REPO",
    "EGREGORE_GITHUB_API",
    "EGREGORE_GITHUB_TOKEN",
    "EGREGORE_APP_ID",
    "EGREGORE_APP_INSTALLATION_ID",
    "EGREGORE_APP_PRIVATE_KEY",
    "EGREGORE_APP_PRIVATE_KEY_PATH",
)


def piece(*parts: str) -> str:
    """Join parts so a canary never sits in the source as one secret-shaped literal."""
    return "".join(parts)


# Canaries that no pattern in coordination.SECRET_PATTERNS can catch. Only a
# scrubber that remembers the exact value keeps these out of an error.
PAT_CANARY = piece("canary-pat-", "7f3a9c2d41")
INSTALL_CANARY = piece("ghs", "_canaryInstallToken", "0123456789abcdef")

# Canaries shaped like the patterns in coordination.SECRET_PATTERNS. They arrive
# in issue text, which the service redacts by pattern.
SHAPED = {
    "ghp": piece("gh", "p_", "a" * 36),
    "pat": piece("github", "_pat_", "B" * 30),
    "sk": piece("s", "k-", "c" * 30),
    "slack": piece("xo", "xb-", "1234567890-canary"),
    "aws": piece("AK", "IA", "CANARY0123456789"),
    "pem-header": piece("-----BEGIN ", "RSA PRIVATE KEY-----"),
}

STYLES = ("inline", "bare", "json", "padded", "html", "multiline-after")


def make_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")


def pem_pieces(pem: str) -> list[str]:
    body = [line.strip() for line in pem.splitlines() if line.strip() and not line.startswith("-----")]
    return [pem.strip(), *body]


class Hostile:
    """A fake GitHub that records every secret it is sent and echoes it in errors."""

    def __init__(self, pem: str) -> None:
        self.pem = pem
        self.style = "inline"
        self.mint_ok = True
        self.repo_status = 500
        self.bearers: list[str] = []
        self.requests: list[str] = []
        self.server = _Quiet(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.api = f"http://{host}:{port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def reset(self, style: str = "inline", mint_ok: bool = True, repo_status: int = 500) -> None:
        self.style = style
        self.mint_ok = mint_ok
        self.repo_status = repo_status
        self.bearers.clear()
        self.requests.clear()

    def echo(self, header: str, body: str) -> bytes:
        """The error text. Every style puts a secret on its first line, in a different frame."""
        bearer = header.removeprefix("Bearer ").strip()
        style = self.style
        if style == "inline":
            text = f"Bad credentials for {header}"
        elif style == "bare":
            text = bearer
        elif style == "json":
            return json.dumps({"message": "Bad credentials", "errors": [{"detail": header, "body": body}]}).encode()
        elif style == "padded":
            text = ("x" * 200) + " " + header
        elif style == "html":
            text = f"<html><body>{header} {self.pem}</body></html>"
        else:
            text = f"Bad credentials\n{header}\n{self.pem}"
        return text.encode("utf-8")

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        hostile = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, fmt: str, *args: object) -> None:
                return

            def do_GET(self) -> None:  # noqa: N802
                self._route("GET")

            def do_POST(self) -> None:  # noqa: N802
                self._route("POST")

            def _route(self, method: str) -> None:
                header = self.headers.get("Authorization") or ""
                length = int(self.headers.get("Content-Length", "0") or "0")
                body = self.rfile.read(length).decode("utf-8") if length else ""
                hostile.bearers.append(header.removeprefix("Bearer ").strip())
                hostile.requests.append(body)
                parts = [unquote(p) for p in urlparse(self.path).path.split("/") if p]
                if method == "POST" and parts[:2] == ["app", "installations"]:
                    if not hostile.mint_ok:
                        self._send(401, hostile.echo(header, body))
                        return
                    # Real time, because the server process under test reads the real clock.
                    expires = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
                    payload = {
                        "token": INSTALL_CANARY,
                        "expires_at": expires,
                        "repository_selection": "selected",
                        "permissions": {"issues": "write", "pull_requests": "read", "metadata": "read"},
                        "repositories": [{"full_name": REPO, "name": "Egeregore"}],
                    }
                    self._send(201, json.dumps(payload).encode())
                    return
                self._send(hostile.repo_status, hostile.echo(header, body))

            def _send(self, status: int, data: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(data)

        return Handler


class _Quiet(ThreadingHTTPServer):
    def handle_error(self, request, client_address) -> None:  # noqa: ANN001
        return


class _Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=1)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())
        self.lines.append(repr(record.args))
        if record.exc_info:
            self.lines.append("".join(traceback.format_exception(*record.exc_info)))


class Channels:
    """Collects everything that can leave the process: stdout, stderr, logs, errors."""

    def __init__(self) -> None:
        self.parts: dict[str, list[str]] = {"stdout": [], "stderr": [], "log": [], "error": [], "reply": []}
        self._out = io.StringIO()
        self._err = io.StringIO()
        self._records = _Records()
        self._stack = contextlib.ExitStack()

    def __enter__(self) -> Channels:
        self._stack.enter_context(contextlib.redirect_stdout(self._out))
        self._stack.enter_context(contextlib.redirect_stderr(self._err))
        logging.getLogger().addHandler(self._records)
        self._previous = logging.getLogger().level
        logging.getLogger().setLevel(1)
        return self

    def __exit__(self, *exc: object) -> None:
        logging.getLogger().removeHandler(self._records)
        logging.getLogger().setLevel(self._previous)
        self._stack.close()
        self.parts["stdout"].append(self._out.getvalue())
        self.parts["stderr"].append(self._err.getvalue())
        self.parts["log"].extend(self._records.lines)

    def error(self, caught: BaseException) -> None:
        self.parts["error"].extend(
            [
                str(caught),
                repr(caught),
                repr(getattr(caught, "args", ())),
                str(getattr(caught, "message", "")),
                "".join(traceback.format_exception(caught)),
            ]
        )

    def reply(self, envelope: object) -> None:
        self.parts["reply"].append(json.dumps(envelope, default=repr))

    def add(self, channel: str, text: str) -> None:
        self.parts[channel].append(text)


class LeakCase(unittest.TestCase):
    """Shared assertions. A failure names the canary and the channel, never the value."""

    def assert_clean(self, channels: Channels, canaries: dict[str, str], where: str) -> None:
        for channel, chunks in channels.parts.items():
            blob = "\n".join(chunks)
            for name, value in canaries.items():
                if value and value in blob:
                    self.fail(f"{where}: canary '{name}' reached {channel}")
            if "eyJ" in blob and re.search(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}", blob):
                self.fail(f"{where}: a JWT-shaped string reached {channel}")


def clean_env(**values: str) -> contextlib.AbstractContextManager[None]:
    @contextlib.contextmanager
    def manager():
        previous = {key: os.environ.get(key) for key in ENV_KEYS}
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        os.environ.update(values)
        try:
            yield
        finally:
            for key, old in previous.items():
                if old is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = old

    return manager()


class HostileGitHubTests(LeakCase):
    """Errors and replies while GitHub echoes every credential it receives."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.pem = make_pem()
        cls.hostile = Hostile(cls.pem)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.hostile.close()

    def setUp(self) -> None:
        self.hostile.reset()

    def canaries(self) -> dict[str, str]:
        found = {"pat": PAT_CANARY, "installation-token": INSTALL_CANARY, "private-key": self.pem.strip()}
        for index, line in enumerate(pem_pieces(self.pem)[1:]):
            found[f"private-key-line-{index}"] = line
        for index, bearer in enumerate(dict.fromkeys(self.hostile.bearers)):
            found[f"bearer-seen-by-github-{index}"] = bearer
        return found

    def app_env(self, **extra: str) -> dict[str, str]:
        values = {
            "EGREGORE_AGENT": "cursor",
            "EGREGORE_REPO": REPO,
            "EGREGORE_GITHUB_API": self.hostile.api,
            "EGREGORE_APP_ID": APP_ID,
            "EGREGORE_APP_INSTALLATION_ID": INSTALLATION_ID,
            "EGREGORE_APP_PRIVATE_KEY": self.pem,
            "EGREGORE_GITHUB_TOKEN": PAT_CANARY,
        }
        values.update(extra)
        return values

    def pat_env(self) -> dict[str, str]:
        return {
            "EGREGORE_AGENT": "cursor",
            "EGREGORE_REPO": REPO,
            "EGREGORE_GITHUB_API": self.hostile.api,
            "EGREGORE_GITHUB_TOKEN": PAT_CANARY,
        }

    def drive(self, service: CoordinationService, channels: Channels) -> None:
        """Calls that reach GitHub: reads, writes, and the review request."""
        for call in (
            lambda: service.get_tasks(),
            lambda: service.get_task(1),
            lambda: service.get_task_history(1),
            lambda: service.get_owner_decisions(),
            lambda: service.claim_task(1, 6),
            lambda: service.post_progress(1, "done", "next"),
            lambda: service.release_task(1, "status:blocked", "reason"),
            lambda: service.request_review(1),
        ):
            try:
                channels.reply(call())
            except (SystemExit, GitHubError, github_app.GitHubAppError) as caught:
                channels.error(caught)

    def test_app_mint_failure_does_not_echo_the_jwt_or_key(self) -> None:
        for style in STYLES:
            with self.subTest(style=style):
                self.hostile.reset(style=style, mint_ok=False)
                channels = Channels()
                with clean_env(**self.app_env()), channels:
                    try:
                        service_from_env(lambda: START)
                    except (SystemExit, github_app.GitHubAppError) as caught:
                        channels.error(caught)
                    else:
                        self.fail("a failed mint must stop startup")
                self.assertTrue(self.hostile.bearers, "the hostile server saw no request")
                self.assert_clean(channels, self.canaries(), f"mint failure, style {style}")

    def test_app_mode_repo_failure_does_not_echo_the_installation_token(self) -> None:
        for style in STYLES:
            with self.subTest(style=style):
                self.hostile.reset(style=style)
                channels = Channels()
                with clean_env(**self.app_env()), channels:
                    service = service_from_env(lambda: START)
                    self.drive(service, channels)
                self.assertIn(INSTALL_CANARY, self.hostile.bearers)
                self.assert_clean(channels, self.canaries(), f"app repo failure, style {style}")

    def test_pat_mode_repo_failure_does_not_echo_the_token(self) -> None:
        for style in STYLES:
            for status in (401, 403, 422, 500, 502):
                with self.subTest(style=style, status=status):
                    self.hostile.reset(style=style, repo_status=status)
                    channels = Channels()
                    with clean_env(**self.pat_env()), channels:
                        service = service_from_env(lambda: START)
                        self.drive(service, channels)
                    self.assertIn(PAT_CANARY, self.hostile.bearers)
                    self.assert_clean(channels, self.canaries(), f"pat repo failure {status}, style {style}")

    def test_connection_failure_does_not_echo_the_bearer(self) -> None:
        """urllib's own reason text can carry the request. Simulate a transport that does."""

        def refuse(request, timeout=None):  # noqa: ANN001
            raise URLError(f"connect failed for {request.get_header('Authorization')} {request.full_url}")

        for label, env_values in (("pat", self.pat_env()), ("app", self.app_env())):
            with self.subTest(mode=label):
                self.hostile.reset()
                channels = Channels()
                with clean_env(**env_values), channels, mock.patch("urllib.request.urlopen", refuse):
                    try:
                        service = service_from_env(lambda: START)
                    except (SystemExit, github_app.GitHubAppError) as caught:
                        channels.error(caught)
                    else:
                        self.drive(service, channels)
                canaries = self.canaries()
                canaries["authorization-header"] = f"Bearer {PAT_CANARY}"
                self.assert_clean(channels, canaries, f"connection failure, {label}")

    def test_known_secret_is_scrubbed_from_a_direct_client_error(self) -> None:
        self.hostile.reset(style="padded", repo_status=500)
        client = RestGitHub(api=self.hostile.api, repo=REPO, token=PAT_CANARY)
        channels = Channels()
        with channels:
            for call in (lambda: client.get_issue(1), lambda: client.list_issues(state="open"), lambda: client.list_comments(1)):
                try:
                    call()
                except GitHubError as caught:
                    channels.error(caught)
                else:
                    self.fail("an error status must raise")
        self.assert_clean(channels, self.canaries(), "direct client")

    def test_bad_configuration_does_not_echo_what_was_set(self) -> None:
        bogus = "-----BEGIN PRIVATE KEY-----\ncanaryKeyBody0123456789abcdef\n-----END PRIVATE KEY-----\n"
        cases = {
            "unusable-key": self.app_env(EGREGORE_APP_PRIVATE_KEY=bogus),
            "not-a-pem": self.app_env(EGREGORE_APP_PRIVATE_KEY="canaryNotAPem0123456789"),
            "bad-app-id": self.app_env(EGREGORE_APP_ID=PAT_CANARY),
            "bad-installation": self.app_env(EGREGORE_APP_INSTALLATION_ID=PAT_CANARY),
            "bad-repo": self.app_env(EGREGORE_REPO=PAT_CANARY),
            "unreadable-key-path": self.app_env(
                EGREGORE_APP_PRIVATE_KEY="", EGREGORE_APP_PRIVATE_KEY_PATH="/nonexistent/" + PAT_CANARY
            ),
        }
        canaries = {
            "pat": PAT_CANARY,
            "key-body": "canaryKeyBody0123456789abcdef",
            "not-a-pem": "canaryNotAPem0123456789",
        }
        for name, values in cases.items():
            with self.subTest(case=name):
                channels = Channels()
                with clean_env(**values), channels:
                    try:
                        service_from_env(lambda: START)
                    except (SystemExit, github_app.GitHubAppError) as caught:
                        channels.error(caught)
                    else:
                        self.fail("a bad configuration must stop startup")
                self.assert_clean(channels, canaries, f"configuration {name}")

    def test_missing_settings_reply_names_variables_only(self) -> None:
        channels = Channels()
        with clean_env(EGREGORE_GITHUB_TOKEN=PAT_CANARY, EGREGORE_AGENT="nobody"), channels:
            service = service_from_env(lambda: START)
            channels.reply(service.get_tasks())
            channels.reply(service.claim_task(1, 6))
        self.assert_clean(channels, {"pat": PAT_CANARY}, "unconfigured service")
        blob = "\n".join(channels.parts["reply"])
        self.assertIn("EGREGORE_AGENT", blob)


class SecretShapedTextTests(LeakCase):
    """Secret-shaped text inside issue bodies and comments is redacted before it is returned."""

    class Stub:
        """Just enough of the GitHub client for the read tools."""

        def __init__(self, shaped: str) -> None:
            body = "\n".join(f"### {name}\n\n{value}\n" for name, value in SHAPED_FIELDS(shaped))
            self.issue = {
                "number": 1,
                "title": "[Task]: canary",
                "state": "open",
                "html_url": "https://example.test/1",
                "labels": [{"name": "type:task"}, {"name": "status:ready"}],
                "body": body,
            }
            self.comment = {
                "id": 11,
                "body": f"Agent: Cursor\n\nI pasted {shaped} by mistake",
                "created_at": "2026-10-29T12:00:00Z",
                "user": {"login": "AStrasler"},
            }

        def get_issue(self, number: int) -> dict[str, object]:
            return self.issue

        def list_issues(self, **kwargs: object) -> list[dict[str, object]]:
            return [self.issue]

        def list_comments(self, number: int) -> list[dict[str, object]]:
            return [self.comment]

    def test_service_redacts_every_shaped_canary(self) -> None:
        for name, shaped in SHAPED.items():
            with self.subTest(canary=name):
                channels = Channels()
                with channels:
                    service = CoordinationService(self.Stub(shaped), "cursor", repo=REPO, now=lambda: START)
                    for call in (
                        lambda: service.get_task(1),
                        lambda: service.get_task_history(1),
                        lambda: service.get_tasks(),
                        lambda: service.get_owner_decisions(),
                    ):
                        channels.reply(call())
                self.assert_clean(channels, {name: shaped}, f"issue text, canary {name}")
                self.assertIn("[redacted]", "\n".join(channels.parts["reply"]))

    def test_gh_failure_text_is_redacted_and_cut_to_one_line(self) -> None:
        for name, shaped in SHAPED.items():
            with self.subTest(canary=name):
                stderr = f"gh: HTTP 422 {shaped} was rejected\nsecond line {shaped}"
                completed = subprocess.CompletedProcess(["gh"], 1, stdout="", stderr=stderr)
                channels = Channels()
                with channels, mock.patch("subprocess.run", return_value=completed):
                    try:
                        coord.gh_json(["repos/x/y/issues/1"])
                    except coord.GhError as caught:
                        channels.error(caught)
                    else:
                        self.fail("a failing gh call must raise")
                self.assert_clean(channels, {name: shaped}, f"gh failure, canary {name}")


def SHAPED_FIELDS(shaped: str) -> list[tuple[str, str]]:
    return [("Role", "any"), ("Outcome", f"token {shaped}"), ("Context", shaped)]


class ProcessTests(LeakCase):
    """The real server process: startup refusals and a stdio tool call. All of stdout and stderr."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.pem = make_pem()
        cls.hostile = Hostile(cls.pem)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.hostile.close()

    def env(self, **extra: str) -> dict[str, str]:
        values = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(ROOT / "mcp"),
            "EGREGORE_AGENT": "cursor",
            "EGREGORE_REPO": REPO,
            "EGREGORE_GITHUB_API": self.hostile.api,
            "EGREGORE_TRANSPORT": "stdio",
        }
        values.update(extra)
        return values

    def canaries(self) -> dict[str, str]:
        found = {"pat": PAT_CANARY, "installation-token": INSTALL_CANARY, "private-key": self.pem.strip()}
        for index, line in enumerate(pem_pieces(self.pem)[1:]):
            found[f"private-key-line-{index}"] = line
        for index, bearer in enumerate(dict.fromkeys(self.hostile.bearers)):
            found[f"bearer-seen-by-github-{index}"] = bearer
        return found

    def converse(self, env: dict[str, str], messages: list[dict[str, object]], wanted: set[int]) -> tuple[str, str]:
        """Speak newline-delimited JSON-RPC to the server and return all of stdout and stderr."""
        process = subprocess.Popen(
            [sys.executable, "-m", "egregore_mcp"],
            cwd=ROOT,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        out: list[str] = []
        err: list[str] = []
        seen: set[int] = set()
        answered = threading.Event()

        def read_out() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                out.append(line)
                try:
                    seen.add(int(json.loads(line).get("id")))
                except (ValueError, TypeError, AttributeError):
                    pass
                if wanted <= seen:
                    answered.set()

        def read_err() -> None:
            assert process.stderr is not None
            err.extend(process.stderr)

        readers = [threading.Thread(target=read_out, daemon=True), threading.Thread(target=read_err, daemon=True)]
        for reader in readers:
            reader.start()
        assert process.stdin is not None
        try:
            for message in messages:
                process.stdin.write(json.dumps(message) + "\n")
                process.stdin.flush()
            answered.wait(timeout=45)
        finally:
            with contextlib.suppress(OSError, ValueError):
                process.stdin.close()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            for reader in readers:
                reader.join(timeout=5)
        return "".join(out), "".join(err)

    def test_startup_refusal_prints_no_secret(self) -> None:
        self.hostile.reset(style="multiline-after", mint_ok=False)
        env = self.env(
            EGREGORE_APP_ID=APP_ID,
            EGREGORE_APP_INSTALLATION_ID=INSTALLATION_ID,
            EGREGORE_APP_PRIVATE_KEY=self.pem,
            EGREGORE_GITHUB_TOKEN=PAT_CANARY,
        )
        done = subprocess.run(
            [sys.executable, "-m", "egregore_mcp"],
            cwd=ROOT,
            env=env,
            input="",
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertNotEqual(done.returncode, 0)
        channels = Channels()
        channels.add("stdout", done.stdout)
        channels.add("stderr", done.stderr)
        self.assertTrue(self.hostile.bearers, "the server never reached the hostile GitHub")
        self.assert_clean(channels, self.canaries(), "startup refusal process")

    def test_stdio_tool_calls_print_no_secret(self) -> None:
        for label, extra in (
            ("pat", {"EGREGORE_GITHUB_TOKEN": PAT_CANARY}),
            (
                "app",
                {
                    "EGREGORE_APP_ID": APP_ID,
                    "EGREGORE_APP_INSTALLATION_ID": INSTALLATION_ID,
                    "EGREGORE_APP_PRIVATE_KEY": self.pem,
                },
            ),
        ):
            with self.subTest(mode=label):
                self.hostile.reset(style="inline", mint_ok=True)
                # In app mode the first mint succeeds, so the server starts. The
                # repository calls that follow fail and echo the installation token.
                messages = [
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {},
                            "clientInfo": {"name": "canary", "version": "0"},
                        },
                    },
                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "get_task", "arguments": {"issue_number": 1}}},
                    {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "get_tasks", "arguments": {}}},
                    {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "get_task_history", "arguments": {"issue_number": 1}}},
                ]
                stdout, stderr = self.converse(self.env(**extra), messages, wanted={2, 3, 4})
                replies = [json.loads(line) for line in stdout.splitlines() if line.strip().startswith("{")]
                answered = {r.get("id") for r in replies}
                self.assertTrue({2, 3, 4} <= answered, f"tool calls were not answered: {sorted(map(str, answered))}")
                channels = Channels()
                channels.add("stdout", stdout)
                channels.add("stderr", stderr)
                canaries = self.canaries()
                self.assertTrue(
                    INSTALL_CANARY in self.hostile.bearers or PAT_CANARY in self.hostile.bearers,
                    "no credential reached the hostile GitHub",
                )
                self.assert_clean(channels, canaries, f"stdio process, {label}")


class ManifestTests(unittest.TestCase):
    """tests/secret-hygiene.manifest.json against the tree."""

    SCAN_DIRS = ("mcp/egregore_mcp", "scripts", "worker/src", ".github/workflows")
    SCAN_SUFFIXES = {".py", ".ts", ".sh", ".yml", ".yaml"}
    # A file is credential-bearing when it names an auth header, a private key,
    # or one of the token variables. Case-insensitive.
    BEARING = re.compile(
        r"authorization|bearer|private[_ ]key|egregore_github_token|gh_token|github_token|secrets\.",
        re.IGNORECASE,
    )
    DATED_REASON = re.compile(r"^\d{4}-\d{2}-\d{2}: \S.{9,}")

    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        cls.entries = {entry["path"]: entry for entry in cls.manifest["paths"]}

    def bearing_files(self) -> set[str]:
        found: set[str] = set()
        for directory in self.SCAN_DIRS:
            for path in sorted((ROOT / directory).rglob("*")):
                if path.is_file() and path.suffix in self.SCAN_SUFFIXES and "node_modules" not in path.parts:
                    if self.BEARING.search(path.read_text(encoding="utf-8")):
                        found.add(path.relative_to(ROOT).as_posix())
        return found

    def test_every_credential_bearing_file_is_listed_once(self) -> None:
        paths = [entry["path"] for entry in self.manifest["paths"]]
        self.assertEqual(len(paths), len(set(paths)), "a path is listed twice")
        found = self.bearing_files()
        missing = sorted(found - set(paths))
        stale = sorted(set(paths) - found)
        self.assertEqual(missing, [], "credential-bearing files missing from the manifest")
        self.assertEqual(stale, [], "manifest entries whose file no longer names a credential")

    def test_each_entry_is_covered_or_exempt_with_a_dated_reason(self) -> None:
        self.assertEqual(self.manifest["version"], 1)
        for path, entry in self.entries.items():
            with self.subTest(path=path):
                self.assertIn(entry["mode"], {"covered", "exempt"})
                if entry["mode"] == "exempt":
                    self.assertRegex(entry.get("reason", ""), self.DATED_REASON, "an exemption needs 'YYYY-MM-DD: reason'")
                    self.assertNotIn("tests", entry)
                else:
                    self.assertTrue(entry.get("tests"), "a covered path names its tests")
                    self.assertNotIn("reason", entry)

    def test_covered_entries_point_at_tests_that_exist(self) -> None:
        for path, entry in self.entries.items():
            if entry["mode"] != "covered":
                continue
            for ref in entry["tests"]:
                with self.subTest(path=path, test=ref):
                    file_part, _, name = ref.partition("::")
                    target = ROOT / file_part
                    self.assertTrue(target.is_file(), f"{file_part} does not exist")
                    text = target.read_text(encoding="utf-8")
                    if file_part.endswith(".py"):
                        self.assertRegex(text, rf"def {re.escape(name)}\(")
                    else:
                        self.assertIn(f'"{name}"', text)

    def test_this_suite_keeps_one_canary_test_per_listed_python_name(self) -> None:
        listed = {
            ref.partition("::")[2]
            for entry in self.entries.values()
            if entry["mode"] == "covered"
            for ref in entry["tests"]
            if ref.startswith("tests/test_secret_hygiene.py::")
        }
        defined = {name for name in dir(HostileGitHubTests) + dir(SecretShapedTextTests) + dir(ProcessTests) if name.startswith("test_")}
        self.assertEqual(sorted(listed - defined), [])


if __name__ == "__main__":
    unittest.main()
