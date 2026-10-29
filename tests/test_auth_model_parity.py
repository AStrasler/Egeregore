#!/usr/bin/env python3
"""The Worker's bearer-token layer and the Python server share rules. This file
holds the Python half.

Two things are shared. worker/test/parity/repo-names.json lists repository
names both sides must accept or refuse the same way, so `../..` cannot pass on
one side. worker/test/parity/auth-model.json is the permission model: which
permission each tool needs, and which agents a token may act as. The Worker's
own test pins src/auth.ts to the same file. Here it is checked against what
the Python server actually does.

The Python server has no token layer. It is one agent over stdio, with the
agent named by EGREGORE_AGENT and the GitHub credential held by the process.
Nothing here adds one.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "mcp"))

import coordination as coord  # noqa: E402
from egregore_mcp import github_app, server  # noqa: E402

PARITY = ROOT / "worker" / "test" / "parity"
VECTORS = json.loads((PARITY / "repo-names.json").read_text(encoding="utf-8"))
MODEL = json.loads((PARITY / "auth-model.json").read_text(encoding="utf-8"))
FIXTURES = json.loads((PARITY / "fixtures.json").read_text(encoding="utf-8"))


class RepoNameTests(unittest.TestCase):
    def test_every_valid_row_is_accepted(self) -> None:
        for row in VECTORS["valid"]:
            self.assertEqual(coord.safe_repo(row), row, repr(row))

    def test_every_invalid_row_is_refused(self) -> None:
        for row in VECTORS["invalid"]:
            self.assertIsNone(coord.safe_repo(row), repr(row))

    def test_non_strings_are_refused(self) -> None:
        for value in (None, 5, ["a", "b"], b"a/b"):
            self.assertIsNone(coord.safe_repo(value))

    def test_github_repository_with_traversal_is_refused(self) -> None:
        for bad in ("../..", "owner/..", "owner//name", "owner/name/"):
            with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": bad}):
                with self.assertRaises(SystemExit, msg=bad):
                    coord.repo_name()
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "AStrasler/Egeregore"}):
            self.assertEqual(coord.repo_name(), "AStrasler/Egeregore")

    def test_server_flags_a_traversal_in_egregore_repo(self) -> None:
        from egregore_mcp import service as service_module

        env = {"EGREGORE_AGENT": "cursor", "EGREGORE_GITHUB_TOKEN": "placeholder", "EGREGORE_REPO": "../.."}
        with mock.patch.dict(os.environ, env, clear=True):
            built = service_module.service_from_env()
        self.assertIn("EGREGORE_REPO", built.config_problems)

    def test_github_app_refuses_dot_segments(self) -> None:
        for bad in ("../..", "owner/..", "./name", "owner/."):
            with self.assertRaises(github_app.GitHubAppConfigError, msg=bad):
                github_app.GitHubAppAuth(api="https://api.github.test", app_id="1", private_key="x", repo=bad)


class PermissionModelTests(unittest.TestCase):
    def test_every_python_tool_has_a_permission_and_ping_is_the_extra(self) -> None:
        self.assertEqual(set(MODEL["tool_permissions"]) - {"ping"}, set(server.TOOLS))
        self.assertEqual(MODEL["tool_permissions"]["ping"], "read")

    def test_permissions_follow_the_python_servers_read_only_marks(self) -> None:
        marked = {tool["name"]: tool["readOnlyHint"] for tool in FIXTURES["tools"]}
        self.assertEqual(set(marked), set(server.TOOLS))
        for name, read_only in marked.items():
            needed = MODEL["tool_permissions"][name]
            self.assertEqual(needed == "read", read_only, name)
            self.assertIn(needed, MODEL["permissions"])

    def test_request_review_is_its_own_permission(self) -> None:
        reviewers = [name for name, needed in MODEL["tool_permissions"].items() if needed == "review"]
        self.assertEqual(reviewers, ["request_review"])

    def test_token_agents_are_callers_the_python_server_accepts_and_exclude_the_owner(self) -> None:
        self.assertTrue(set(MODEL["token_agents"]) <= set(coord.ALLOWED_AGENTS))
        self.assertNotIn("aaron", MODEL["token_agents"])

    def test_the_python_server_refuses_a_caller_that_is_not_an_allowed_agent(self) -> None:
        from egregore_mcp import service as service_module

        env = {"EGREGORE_AGENT": "nobody", "EGREGORE_GITHUB_TOKEN": "placeholder"}
        with mock.patch.dict(os.environ, env, clear=True):
            built = service_module.service_from_env()
        self.assertIn("EGREGORE_AGENT", built.config_problems)


if __name__ == "__main__":
    unittest.main()
