"""Read-only Egregore specialist catalog pointers.

Specialists are prompts stored in the user's wshobson/agents fork; this file
does not fetch, execute, copy, or install those prompts. Clients load them
using their own sandbox and permissions after choosing a catalog entry.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import quote

_CATALOG_FILE = Path(__file__).resolve().parents[1] / "specialists.json"
_SLUG = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_ALLOWED_KINDS = {"agent", "skill"}


def _failure(code: str, message: str) -> dict[str, object]:
    return {
        "ok": False,
        "result": None,
        "error": {"code": code, "message": message, "details": {}},
    }


def get_specialists(
    role: str = "", kind: str = "", specialist_id: str = ""
) -> dict[str, object]:
    """List vetted metadata and immutable source links, without running a specialist."""
    for field, value in (("role", role), ("kind", kind), ("specialist_id", specialist_id)):
        if not isinstance(value, str) or (value and not _SLUG.fullmatch(value)):
            return _failure("invalid_input", f"{field} must be an empty value or a lowercase slug.")
    if kind and kind not in _ALLOWED_KINDS:
        return _failure("invalid_input", "kind must be agent or skill.")

    try:
        catalog = json.loads(_CATALOG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _failure("unconfigured", "The pinned specialist catalog is unavailable.")

    fork = catalog.get("fork_repository")
    commit = catalog.get("fork_commit")
    entries = catalog.get("entries")
    if (
        not isinstance(fork, str)
        or fork != "AStrasler/agents"
        or not isinstance(commit, str)
        or not _SHA.fullmatch(commit)
        or not isinstance(entries, list)
    ):
        return _failure("unconfigured", "The pinned specialist catalog is invalid.")

    selected: list[dict[str, str]] = []
    for item in entries:
        if not isinstance(item, dict):
            return _failure("unconfigured", "The specialist catalog has an invalid entry.")
        if not all(isinstance(item.get(k), str) for k in ("id", "kind", "role", "plugin", "path")):
            return _failure("unconfigured", "The specialist catalog has a missing entry field.")
        entry_id = item["id"]
        entry_kind = item["kind"]
        entry_role = item["role"]
        plugin = item["plugin"]
        path = item["path"]
        source_repo = item.get("source_repository", fork)
        source_commit = item.get("source_commit", commit)
        if (
            not _SLUG.fullmatch(entry_id)
            or not _SLUG.fullmatch(entry_role)
            or not _SLUG.fullmatch(plugin)
            or entry_kind not in _ALLOWED_KINDS
            or not isinstance(source_repo, str)
            or not isinstance(source_commit, str)
            or not _SHA.fullmatch(source_commit)
            or any(segment in ("", ".", "..") for segment in path.split("/"))
            or "\\\\" in path
            or ":" in path
        ):
            return _failure("unconfigured", "The specialist catalog contains an unsafe entry.")

        # External prompts are intentionally opt-in and source-pinned.
        # Adding a repository to the catalog is a reviewed code change.
        if source_repo == fork:
            segments = path.split("/")
            allowed_path = (
                len(segments) >= 4
                and segments[0] == "plugins"
                and segments[1] == plugin
                and (
                    (entry_kind == "agent" and len(segments) == 4
                     and segments[2] == "agents" and segments[3].endswith(".md"))
                    or (entry_kind == "skill" and len(segments) == 5
                        and segments[2] == "skills" and segments[4] == "SKILL.md")
                )
            )
            if source_commit != commit or not allowed_path:
                return _failure("unconfigured", "The fork specialist path or commit is invalid.")
        elif source_repo == "DietrichGebert/ponytail":
            if (
                entry_id != "ponytail" or plugin != "ponytail"
                or entry_kind != "skill" or entry_role != "code-implementer"
                or path != "skills/ponytail/SKILL.md"
                or source_commit != "09c434ab73ca43761c5d8b0c5d341926c2fba710"
            ):
                return _failure("unconfigured", "The Ponytail source is not the reviewed pinned skill.")
        else:
            return _failure("unconfigured", "The catalog contains an unapproved source repository.")

        if role and entry_role != role:
            continue
        if kind and entry_kind != kind:
            continue
        if specialist_id and entry_id != specialist_id:
            continue
        selected.append(
            {
                "id": entry_id,
                "kind": entry_kind,
                "role": entry_role,
                "plugin": plugin,
                "path": path,
                "source_repository": source_repo,
                "source_commit": source_commit,
                "source_url": f"https://github.com/{source_repo}/blob/{source_commit}/{quote(path, safe='/')}",
            }
        )

    return {
        "ok": True,
        "result": {
            "specialists": selected,
            "count": len(selected),
            "fork_repository": fork,
            "fork_commit": commit,
            "fork_pr": catalog.get("fork_pr"),
            "upstream_repository": catalog.get("upstream_repository"),
            "upstream_commit": catalog.get("upstream_commit"),
            "capability": "catalog-only",
            "note": (
                "These are opt-in reference links, not callable agents. A Cursor/Codex "
                "client must load the chosen prompt with its own permissions. "
                "No cross-agent communication or new access is granted."
            ),
        },
        "error": None,
    }
