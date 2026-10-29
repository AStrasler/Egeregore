"""GitHub App authentication for one installation on one repository.

The server signs a short-lived RS256 JWT with the app private key and exchanges
it at POST /app/installations/{id}/access_tokens. The installation token is
cached until five minutes before it expires, then refreshed. The token request
asks only for issues:write, pull_requests:read, and metadata:read, and only for
EGREGORE_REPO. A token whose repository list is anything else, or whose
permissions are broader, refuses to start.

PyJWT signs that JWT. The Python standard library has no RSA signature
implementation. cryptography is the RS256 backend PyJWT imports for that
algorithm. Both packages are free: PyJWT is MIT, cryptography is Apache-2.0
and BSD.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Mapping

import jwt

from egregore_mcp.github_api import GitHubError
from egregore_mcp.redaction import redact_secrets

# Do not add permissions. issues:write covers claims, comments, and labels.
# pull_requests stays read-only. metadata:read is the GitHub minimum.
REQUESTED_PERMISSIONS: dict[str, str] = {
    "issues": "write",
    "pull_requests": "read",
    "metadata": "read",
}
_LEVELS = {"read": 1, "write": 2, "admin": 3}
_REFRESH_SKEW = timedelta(minutes=5)
_JWT_PAST_SKEW = 60
_JWT_FUTURE = 8 * 60
_APP_ENV_KEYS = (
    "EGREGORE_APP_ID",
    "EGREGORE_APP_INSTALLATION_ID",
    "EGREGORE_APP_PRIVATE_KEY",
    "EGREGORE_APP_PRIVATE_KEY_PATH",
)


class GitHubAppError(Exception):
    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class GitHubAppConfigError(GitHubAppError):
    """The app environment is incomplete or the private key cannot be read."""


class GitHubAppScopeError(GitHubAppError):
    """The installation token can see another repository or a broader permission."""


@dataclass(frozen=True)
class _CachedToken:
    value: str
    expires_at: datetime
    minted_at: datetime


def app_credentials_present() -> bool:
    return any(os.environ.get(key, "").strip() for key in _APP_ENV_KEYS)


def normalize_pem(text: str) -> str:
    cleaned = text.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {'"', "'"}:
        cleaned = cleaned[1:-1].strip()
    cleaned = cleaned.replace("\\n", "\n")
    if "-----BEGIN " not in cleaned or "PRIVATE KEY-----" not in cleaned:
        raise GitHubAppConfigError("The GitHub App private key must be a PEM private key.")
    if not cleaned.endswith("\n"):
        cleaned += "\n"
    return cleaned


def load_private_key_from_env() -> str:
    inline = os.environ.get("EGREGORE_APP_PRIVATE_KEY", "")
    path = os.environ.get("EGREGORE_APP_PRIVATE_KEY_PATH", "").strip()
    if inline.strip():
        return normalize_pem(inline)
    if path:
        try:
            data = Path(path).expanduser().read_text(encoding="utf-8")
        except OSError:
            raise GitHubAppConfigError("Could not read EGREGORE_APP_PRIVATE_KEY_PATH.") from None
        return normalize_pem(data)
    raise GitHubAppConfigError("Set EGREGORE_APP_PRIVATE_KEY or EGREGORE_APP_PRIVATE_KEY_PATH.")


class GitHubAppAuth:
    """Mint and cache the installation token for EGREGORE_REPO."""

    def __init__(
        self,
        *,
        api: str,
        app_id: str,
        private_key: str,
        repo: str,
        installation_id: str | None = None,
        now: Callable[[], datetime] | None = None,
        skew: timedelta = _REFRESH_SKEW,
    ) -> None:
        owner, _sep, name = repo.partition("/")
        if not owner or not name or "/" in name:
            raise GitHubAppConfigError("EGREGORE_REPO must be owner/name.")
        if not app_id.isdigit():
            raise GitHubAppConfigError("EGREGORE_APP_ID must be the numeric GitHub App id.")
        if installation_id is not None and not installation_id.isdigit():
            raise GitHubAppConfigError("EGREGORE_APP_INSTALLATION_ID must be a numeric installation id.")
        self.api = api.rstrip("/")
        self.app_id = app_id
        self.repo = repo
        self._owner = owner
        self._repo_name = name
        self._installation_id = installation_id
        self._private_key = private_key
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._skew = skew
        self._cached: _CachedToken | None = None
        self._secrets: list[str] = []
        self._remember(private_key)
        # The HTTP exchange stays inside the lock so two callers cannot each
        # mint and then keep a different token.
        self._lock = threading.Lock()

    @classmethod
    def from_env(
        cls,
        *,
        api: str,
        repo: str,
        now: Callable[[], datetime] | None = None,
    ) -> GitHubAppAuth:
        app_id = os.environ.get("EGREGORE_APP_ID", "").strip()
        if not app_id.isdigit():
            raise GitHubAppConfigError("EGREGORE_APP_ID must be the numeric GitHub App id.")
        installation = os.environ.get("EGREGORE_APP_INSTALLATION_ID", "").strip()
        if installation and not installation.isdigit():
            raise GitHubAppConfigError("EGREGORE_APP_INSTALLATION_ID must be a numeric installation id.")
        return cls(
            api=api,
            app_id=app_id,
            private_key=load_private_key_from_env(),
            repo=repo,
            installation_id=installation or None,
            now=now,
        )

    def authenticate(self) -> None:
        """Mint the first token. A bad repository list or permission set raises."""
        with self._lock:
            self._cached = self._mint(self._aware_now())

    def token(self) -> str:
        """Return a cached installation token, refreshing within the skew window."""
        try:
            return self._token_for_call()
        except GitHubAppScopeError as exc:
            raise SystemExit(str(exc)) from None
        except GitHubAppError as exc:
            raise GitHubError(exc.status, str(exc)) from None

    def _token_for_call(self) -> str:
        with self._lock:
            now = self._aware_now()
            cached = self._cached
            if cached is not None and not _due(cached, now, self._skew):
                return cached.value
            try:
                minted = self._mint(now)
            except GitHubAppScopeError:
                raise
            except GitHubAppError:
                if cached is not None and now < cached.expires_at:
                    return cached.value
                raise
            self._cached = minted
            return minted.value

    def _mint(self, now: datetime) -> _CachedToken:
        installation_id = self._installation_id_for(now)
        payload, _link = self._http(
            "POST",
            f"{self.api}/app/installations/{installation_id}/access_tokens",
            bearer=self._app_jwt(now),
            body={
                "repositories": [self._repo_name],
                "permissions": dict(REQUESTED_PERMISSIONS),
            },
        )
        if not isinstance(payload, dict):
            raise GitHubAppError("GitHub installation token response was not an object.", status=502)
        raw_token = payload.get("token")
        if not isinstance(raw_token, str) or not raw_token:
            raise GitHubAppError("GitHub installation token response had no token.", status=502)
        self._remember(raw_token)
        expires = _parse_time(payload.get("expires_at"))
        if expires is None:
            raise GitHubAppError("GitHub installation token response had no expires_at.", status=502)
        if expires <= now:
            raise GitHubAppError("GitHub returned an installation token that is already expired.", status=502)
        self._refuse_broad_token(payload, raw_token)
        return _CachedToken(value=raw_token, expires_at=expires, minted_at=now)

    def _installation_id_for(self, now: datetime) -> str:
        if self._installation_id:
            return self._installation_id
        owner = urllib.parse.quote(self._owner, safe="")
        name = urllib.parse.quote(self._repo_name, safe="")
        payload, _link = self._http(
            "GET",
            f"{self.api}/repos/{owner}/{name}/installation",
            bearer=self._app_jwt(now),
        )
        if not isinstance(payload, dict):
            raise GitHubAppError("GitHub did not return an installation id for this repository.", status=502)
        found = payload.get("id")
        if isinstance(found, bool) or not isinstance(found, int) or found < 1:
            raise GitHubAppError("GitHub did not return an installation id for this repository.", status=502)
        self._installation_id = str(found)
        return self._installation_id

    def _app_jwt(self, now: datetime) -> str:
        issued = int(now.timestamp()) - _JWT_PAST_SKEW
        # Eight minutes ahead is under GitHub's ten-minute exp cap. iat is one
        # minute behind so a small clock skew still validates.
        payload = {"iat": issued, "exp": int(now.timestamp()) + _JWT_FUTURE, "iss": self.app_id}
        try:
            encoded = jwt.encode(payload, self._private_key, algorithm="RS256")
        except Exception:
            raise GitHubAppConfigError("EGREGORE_APP_PRIVATE_KEY is not a usable RSA private key.") from None
        if isinstance(encoded, bytes):
            encoded = encoded.decode("ascii")
        self._remember(encoded)
        return encoded

    def _refuse_broad_token(self, payload: Mapping[str, object], raw_token: str) -> None:
        check_permissions(payload.get("permissions"))
        selection = str(payload.get("repository_selection") or "")
        if selection == "all":
            raise GitHubAppScopeError(
                "Refusing to start: installation token repository_selection is all, "
                f"which is broader than {self.repo}."
            )
        if "repositories" in payload:
            names = full_names(payload.get("repositories"))
        else:
            names = self._list_installation_repos(raw_token)
        check_repo_names(names, self.repo)

    def _list_installation_repos(self, raw_token: str) -> list[str]:
        names: list[str] = []
        url: str | None = f"{self.api}/installation/repositories?per_page=100"
        pages = 0
        while url is not None:
            pages += 1
            if pages > 10:
                raise GitHubAppScopeError("Refusing to start: installation repository list did not end.")
            payload, url = self._http("GET", url, bearer=raw_token)
            if not isinstance(payload, dict):
                raise GitHubAppScopeError("Refusing to start: installation repository list was malformed.")
            batch = full_names(payload.get("repositories"))
            names.extend(batch)
            total = payload.get("total_count")
            if url is None and isinstance(total, int) and not isinstance(total, bool) and total > len(names):
                raise GitHubAppScopeError(
                    f"Refusing to start: installation token reports {total} repositories, "
                    f"which is broader than {self.repo}."
                )
        return names

    def _http(
        self,
        method: str,
        url: str,
        *,
        bearer: str,
        body: Mapping[str, object] | None = None,
    ) -> tuple[object, str | None]:
        self._remember(bearer)
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {bearer}")
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
            raise GitHubAppError(self._safe_error(err.code, detail), status=err.code) from None
        except urllib.error.URLError as err:
            reason = self._scrub(str(err.reason))
            raise GitHubAppError(f"GitHub connection failed: {reason}", status=0) from None
        if not raw:
            raise GitHubAppError("GitHub HTTP 502: empty authentication response", status=502)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            raise GitHubAppError("GitHub HTTP 502: authentication response was not JSON", status=502) from None
        return payload, _next_link(link)

    def _safe_error(self, status: int, detail: str) -> str:
        text = self._scrub(detail).strip().splitlines()
        line = (text[0] if text else "GitHub request failed")[:300]
        return f"GitHub HTTP {status}: {line}"

    def _remember(self, secret: str) -> None:
        pieces = [secret]
        if "PRIVATE KEY" in secret:
            pieces.extend(line.strip() for line in secret.splitlines() if line.strip())
        for piece in pieces:
            if len(piece) < 8 or piece in self._secrets:
                continue
            self._secrets.append(piece)

    def _scrub(self, text: str) -> str:
        for secret in sorted(self._secrets, key=len, reverse=True):
            text = text.replace(secret, "[redacted]")
        return redact_secrets(text)

    def _aware_now(self) -> datetime:
        current = self._now()
        if current.tzinfo is None:
            raise GitHubAppConfigError("Authentication clock must be timezone-aware.")
        return current.astimezone(timezone.utc)


def check_permissions(granted: object) -> None:
    if not isinstance(granted, dict):
        raise GitHubAppScopeError("Refusing to start: installation token permissions were missing.")
    problems: list[str] = []
    for key, value in granted.items():
        if value is None or value == "":
            continue
        name = str(key)
        level_name = str(value)
        if name not in REQUESTED_PERMISSIONS:
            problems.append(f"{name}:{level_name}")
            continue
        level = _LEVELS.get(level_name)
        ceiling = _LEVELS[REQUESTED_PERMISSIONS[name]]
        if level is None or level > ceiling:
            problems.append(f"{name}:{level_name}")
    for name, needed in REQUESTED_PERMISSIONS.items():
        actual = granted.get(name)
        if str(actual) == needed:
            continue
        label = f"{name}:{actual if actual is not None and actual != '' else 'missing'}"
        if label not in problems:
            problems.append(label)
    if problems:
        raise GitHubAppScopeError(
            redact_secrets(
                "Refusing to start: installation token permissions must be "
                "issues:write, pull_requests:read, metadata:read. "
                f"Got {', '.join(problems)}."
            )
        )


def full_names(repositories: object) -> list[str]:
    if not isinstance(repositories, list):
        raise GitHubAppScopeError("Refusing to start: installation token repository list was malformed.")
    names: list[str] = []
    for item in repositories:
        if not isinstance(item, dict):
            raise GitHubAppScopeError("Refusing to start: installation token repository list was malformed.")
        full = item.get("full_name")
        if not isinstance(full, str) or "/" not in full:
            raise GitHubAppScopeError("Refusing to start: installation token repository list was malformed.")
        names.append(full)
    return names


def check_repo_names(names: list[str], expected: str) -> None:
    if len(names) == 1 and names[0].casefold() == expected.casefold():
        return
    shown = ", ".join(names) if names else "(empty)"
    raise GitHubAppScopeError(
        redact_secrets(f"Refusing to start: installation token repository list must be only {expected}. Got {shown}.")
    )


def _due(cached: _CachedToken, now: datetime, skew: timedelta) -> bool:
    if now >= cached.expires_at:
        return True
    # A token that expires inside the skew window is used until it actually
    # expires. Refreshing it immediately would mint on every call.
    refresh_at = cached.expires_at - skew
    if refresh_at <= cached.minted_at:
        return False
    return now >= refresh_at


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _next_link(header: str | None) -> str | None:
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
