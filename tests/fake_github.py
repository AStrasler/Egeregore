"""In-memory GitHub and a local HTTP facade for MCP tests. No network."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from egregore_mcp.github_api import GitHubError


def iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeGitHub:
    """The method surface RestGitHub uses, backed by dicts."""

    def __init__(
        self,
        *,
        now: Callable[[], datetime] | None = None,
        login: str = "AStrasler",
        repo: str = "AStrasler/Egeregore",
    ) -> None:
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.login = login
        self.repo = repo
        self.issues: dict[int, dict[str, object]] = {}
        self.comments: dict[int, list[dict[str, object]]] = {}
        self.pulls: dict[int, str] = {}
        self._issue_seq = 1
        self._comment_seq = 1
        self._lock = threading.Lock()

    def seed_issue(
        self,
        *,
        title: str,
        body: str,
        labels: list[str],
        number: int | None = None,
        state: str = "open",
    ) -> int:
        with self._lock:
            if number is None:
                number = self._issue_seq
                self._issue_seq += 1
            else:
                self._issue_seq = max(self._issue_seq, number + 1)
            self.issues[number] = {
                "title": title,
                "body": body,
                "state": state,
                "labels": list(labels),
            }
            self.comments.setdefault(number, [])
            return number

    def seed_pull(self, number: int, head_sha: str, *, title: str = "Review me") -> None:
        self.seed_issue(title=title, body="pull request", labels=[], number=number)
        with self._lock:
            self.issues[number]["pull_request"] = {"url": f"https://github.com/{self.repo}/pull/{number}"}
            self.pulls[number] = head_sha

    def list_issues(self, *, state: str) -> list[dict[str, object]]:
        with self._lock:
            rows = []
            for number, issue in self.issues.items():
                if state != "all" and issue["state"] != state:
                    continue
                rows.append(self._issue_payload(number))
            return rows

    def get_issue(self, number: int) -> dict[str, object]:
        with self._lock:
            self._require(number)
            return self._issue_payload(number)

    def create_issue(self, *, title: str, body: str, labels: list[str]) -> dict[str, object]:
        number = self.seed_issue(title=title, body=body, labels=labels)
        return self.get_issue(number)

    def list_comments(self, number: int) -> list[dict[str, object]]:
        with self._lock:
            self._require(number)
            return [dict(comment) for comment in self.comments[number]]

    def create_comment(self, number: int, body: str) -> dict[str, object]:
        with self._lock:
            self._require(number)
            comment_id = self._comment_seq
            self._comment_seq += 1
            created = iso(self.now())
            comment = {
                "id": comment_id,
                "body": body,
                "created_at": created,
                "updated_at": created,
                "user": {"login": self.login},
            }
            self.comments[number].append(comment)
            return dict(comment)

    def add_labels(self, number: int, labels: list[str]) -> None:
        if not labels:
            return
        with self._lock:
            self._require(number)
            current = self.issues[number]["labels"]
            assert isinstance(current, list)
            for name in labels:
                if name not in current:
                    current.append(name)

    def remove_label(self, number: int, name: str) -> None:
        with self._lock:
            self._require(number)
            current = self.issues[number]["labels"]
            assert isinstance(current, list)
            if name in current:
                current.remove(name)

    def get_pull(self, number: int) -> dict[str, object]:
        with self._lock:
            if number not in self.pulls:
                raise GitHubError(404, "GitHub HTTP 404: Not Found")
            return {"number": number, "head": {"sha": self.pulls[number]}, "title": self.issues[number]["title"]}

    def list_reviews(self, number: int) -> list[dict[str, object]]:
        with self._lock:
            if number not in self.pulls:
                raise GitHubError(404, "GitHub HTTP 404: Not Found")
            return []

    def label_names(self, number: int) -> list[str]:
        with self._lock:
            labels = self.issues[number]["labels"]
            assert isinstance(labels, list)
            return list(labels)

    def _require(self, number: int) -> None:
        if number not in self.issues:
            raise GitHubError(404, "GitHub HTTP 404: Not Found")

    def _issue_payload(self, number: int) -> dict[str, object]:
        issue = self.issues[number]
        payload: dict[str, object] = {
            "number": number,
            "title": issue["title"],
            "body": issue["body"],
            "state": issue["state"],
            "labels": [{"name": name} for name in issue["labels"]],  # type: ignore[union-attr]
            "html_url": f"https://github.com/{self.repo}/issues/{number}",
            "user": {"login": self.login},
        }
        if "pull_request" in issue:
            payload["pull_request"] = issue["pull_request"]
        return payload


class _QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address) -> None:  # noqa: ANN001
        return


def _handler(store: FakeGitHub, token: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, fmt: str, *args: object) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            self._route("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._route("POST")

        def do_DELETE(self) -> None:  # noqa: N802
            self._route("DELETE")

        def _route(self, method: str) -> None:
            if self.headers.get("Authorization") != f"Bearer {token}":
                self._send(401, {"message": "Bad credentials"})
                return
            parsed = urlparse(self.path)
            parts = [unquote(part) for part in parsed.path.split("/") if part]
            if len(parts) < 3 or parts[0] != "repos":
                self._send(404, {"message": "not found"})
                return
            rest = parts[3:]
            if rest == ["echo-auth"]:
                self._send(200, {"authorization": self.headers.get("Authorization")})
                return
            if rest == ["fail-with-auth"]:
                self._send(500, {"message": self.headers.get("Authorization") or ""})
                return
            try:
                payload = self._dispatch(method, rest, parse_qs(parsed.query))
            except GitHubError as err:
                self._send(err.status or 502, {"message": err.message})
                return
            except KeyError:
                self._send(404, {"message": "Not Found"})
                return
            self._send(200, payload)

        def _dispatch(self, method: str, rest: list[str], query: dict[str, list[str]]) -> object:
            if rest == ["issues"] and method == "GET":
                state = query.get("state", ["open"])[0]
                return store.list_issues(state=state)
            if rest == ["issues"] and method == "POST":
                body = self._json()
                return store.create_issue(
                    title=str(body["title"]),
                    body=str(body.get("body") or ""),
                    labels=list(body.get("labels") or []),
                )
            if len(rest) == 2 and rest[0] == "issues" and method == "GET":
                return store.get_issue(int(rest[1]))
            if len(rest) == 3 and rest[0] == "issues" and rest[2] == "comments" and method == "GET":
                return store.list_comments(int(rest[1]))
            if len(rest) == 3 and rest[0] == "issues" and rest[2] == "comments" and method == "POST":
                body = self._json()
                return store.create_comment(int(rest[1]), str(body["body"]))
            if len(rest) == 3 and rest[0] == "issues" and rest[2] == "labels" and method == "POST":
                body = self._json()
                store.add_labels(int(rest[1]), list(body.get("labels") or []))
                return store.get_issue(int(rest[1]))["labels"]
            if len(rest) == 4 and rest[0] == "issues" and rest[2] == "labels" and method == "DELETE":
                store.remove_label(int(rest[1]), rest[3])
                return None
            if len(rest) == 2 and rest[0] == "pulls" and method == "GET":
                return store.get_pull(int(rest[1]))
            if len(rest) == 3 and rest[0] == "pulls" and rest[2] == "reviews" and method == "GET":
                return store.list_reviews(int(rest[1]))
            raise KeyError(rest)

        def _json(self) -> dict[str, object]:
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length else b""
            if not raw:
                return {}
            value = json.loads(raw.decode("utf-8"))
            return value if isinstance(value, dict) else {}

        def _send(self, status: int, payload: object) -> None:
            if payload is None:
                data = b""
            else:
                data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)

    return Handler


@contextmanager
def serve(store: FakeGitHub, token: str = "test-token") -> Iterator[str]:
    server = _QuietServer(("127.0.0.1", 0), _handler(store, token))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
