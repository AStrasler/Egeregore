"""GitHub REST client for the coordination MCP server.

The token comes from the environment at process start. This module does not
read a token file, and it does not put the token into errors or logs.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Mapping


class GitHubError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class RestGitHub:
    """A small slice of the GitHub REST API. Stdlib only, no extra package."""

    def __init__(self, *, api: str, token: str, repo: str) -> None:
        self.api = api.rstrip("/")
        self._token = token
        self.repo = repo

    def list_issues(self, *, state: str) -> list[dict[str, object]]:
        payload = self._request("GET", "issues", query={"state": state, "per_page": "100"})
        if not isinstance(payload, list):
            raise GitHubError(502, "GitHub HTTP 502: issues response was not a list")
        return [item for item in payload if isinstance(item, dict)]

    def get_issue(self, number: int) -> dict[str, object]:
        payload = self._request("GET", f"issues/{number}")
        if not isinstance(payload, dict):
            raise GitHubError(502, "GitHub HTTP 502: issue response was not an object")
        return payload

    def create_issue(self, *, title: str, body: str, labels: list[str]) -> dict[str, object]:
        payload = self._request(
            "POST",
            "issues",
            body={"title": title, "body": body, "labels": labels},
        )
        if not isinstance(payload, dict):
            raise GitHubError(502, "GitHub HTTP 502: create-issue response was not an object")
        return payload

    def list_comments(self, number: int) -> list[dict[str, object]]:
        payload = self._request("GET", f"issues/{number}/comments", query={"per_page": "100"})
        if not isinstance(payload, list):
            raise GitHubError(502, "GitHub HTTP 502: comments response was not a list")
        return [item for item in payload if isinstance(item, dict)]

    def create_comment(self, number: int, body: str) -> dict[str, object]:
        payload = self._request("POST", f"issues/{number}/comments", body={"body": body})
        if not isinstance(payload, dict):
            raise GitHubError(502, "GitHub HTTP 502: create-comment response was not an object")
        return payload

    def add_labels(self, number: int, labels: list[str]) -> None:
        if not labels:
            return
        self._request("POST", f"issues/{number}/labels", body={"labels": labels})

    def remove_label(self, number: int, name: str) -> None:
        encoded = urllib.parse.quote(name, safe="")
        try:
            self._request("DELETE", f"issues/{number}/labels/{encoded}")
        except GitHubError as err:
            if err.status == 404:
                return
            raise

    def get_pull(self, number: int) -> dict[str, object]:
        payload = self._request("GET", f"pulls/{number}")
        if not isinstance(payload, dict):
            raise GitHubError(502, "GitHub HTTP 502: pull response was not an object")
        return payload

    def list_reviews(self, number: int) -> list[dict[str, object]]:
        payload = self._request("GET", f"pulls/{number}/reviews", query={"per_page": "100"})
        if not isinstance(payload, list):
            raise GitHubError(502, "GitHub HTTP 502: reviews response was not a list")
        return [item for item in payload if isinstance(item, dict)]

    def list_reactions(self, number: int) -> list[dict[str, object]]:
        payload = self._request("GET", f"issues/{number}/reactions", query={"per_page": "100"})
        if not isinstance(payload, list):
            raise GitHubError(502, "GitHub HTTP 502: reactions response was not a list")
        return [item for item in payload if isinstance(item, dict)]

    def _request(
        self,
        method: str,
        suffix: str,
        *,
        query: Mapping[str, str] | None = None,
        body: Mapping[str, object] | None = None,
    ) -> object:
        pages: list[object] = []
        next_url: str | None = self._url(suffix, query)
        hops = 0
        while next_url is not None:
            hops += 1
            if hops > 10:
                raise GitHubError(502, "GitHub HTTP 502: stopped after 10 pages")
            payload, next_url = self._once(method, next_url, body if hops == 1 else None)
            pages.append(payload)
            if method != "GET":
                break
        if len(pages) == 1:
            return pages[0]
        if all(isinstance(page, list) for page in pages):
            flat: list[object] = []
            for page in pages:
                flat.extend(page)  # type: ignore[arg-type]
            return flat
        raise GitHubError(502, "GitHub HTTP 502: paginated response was not a list")

    def _once(self, method: str, url: str, body: Mapping[str, object] | None) -> tuple[object, str | None]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self._token}")
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        request.add_header("User-Agent", "egregore-mcp/0.1")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
                link = response.headers.get("Link")
        except urllib.error.HTTPError as err:
            detail = err.read().decode("utf-8", "replace")
            raise GitHubError(err.code, self._safe_error(err.code, detail)) from None
        except urllib.error.URLError as err:
            reason = self._scrub(str(err.reason))
            raise GitHubError(0, f"GitHub connection failed: {reason}") from None
        if not raw:
            return None, self._next_link(link)
        try:
            return json.loads(raw.decode("utf-8")), self._next_link(link)
        except json.JSONDecodeError:
            raise GitHubError(502, "GitHub HTTP 502: response was not JSON") from None

    def _url(self, suffix: str, query: Mapping[str, str] | None) -> str:
        url = f"{self.api}/repos/{self.repo}/{suffix}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        return url

    def _safe_error(self, status: int, detail: str) -> str:
        text = self._scrub(detail).strip().splitlines()
        line = (text[0] if text else "GitHub request failed")[:300]
        return f"GitHub HTTP {status}: {line}"

    def _scrub(self, text: str) -> str:
        if self._token:
            text = text.replace(self._token, "[redacted]")
        return text

    def _next_link(self, header: str | None) -> str | None:
        if not header:
            return None
        for part in header.split(","):
            piece = part.strip()
            if 'rel="next"' not in piece:
                continue
            start = piece.find("<")
            end = piece.find(">")
            if start != -1 and end != -1:
                return piece[start + 1 : end]
        return None
