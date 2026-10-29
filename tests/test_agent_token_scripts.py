#!/usr/bin/env python3
"""The local token tools: make a token and its hash, and check a config.

worker/scripts/generate-agent-token.mjs makes the token and the record for the
Worker secret. worker/scripts/check-agent-tokens.mjs runs the Worker's own
parser over a config. Both run offline. These tests make tokens at run time, so
no token-shaped text is stored in the repository.

The checker loads TypeScript, which needs Node 22.6 or later. On an older Node
those tests are skipped and say so.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATE = ROOT / "worker" / "scripts" / "generate-agent-token.mjs"
CHECK = ROOT / "worker" / "scripts" / "check-agent-tokens.mjs"
NODE = shutil.which("node")


def node_version() -> tuple[int, int]:
    if NODE is None:
        return (0, 0)
    out = subprocess.run([NODE, "--version"], capture_output=True, text=True, check=True).stdout.strip()
    match = re.match(r"v(\d+)\.(\d+)", out)
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


def run(script: Path, *args: str, stdin: str = "", env: dict[str, str] | None = None, strip: bool = False, cwd: str | None = None):
    command = [NODE]
    if strip:
        command.append("--experimental-strip-types")
    command += [str(script), *args]
    merged = {**os.environ, **(env or {})}
    merged.pop("NODE_OPTIONS", None)
    return subprocess.run(command, input=stdin, capture_output=True, text=True, env=merged, cwd=cwd, timeout=60)


@unittest.skipIf(NODE is None, "node is not installed")
class GenerateTests(unittest.TestCase):
    def make(self, *args: str) -> dict:
        done = run(GENERATE, "--agent", "cursor", "--json", *args)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_the_record_holds_the_hash_of_the_token_and_not_the_token(self) -> None:
        made = self.make()
        token, record = made["token"], made["record"]
        self.assertRegex(token, r"^egr_[A-Za-z0-9_-]{43}$")
        self.assertEqual(record["sha256"], hashlib.sha256(token.encode()).hexdigest())
        self.assertNotIn(token, json.dumps(record))
        self.assertEqual(
            record,
            {"sha256": record["sha256"], "agent": "cursor", "permissions": ["read", "write", "review"], "repos": ["AStrasler/Egeregore"], "tools": ["*"]},
        )

    def test_every_token_is_new(self) -> None:
        self.assertNotEqual(self.make()["token"], self.make()["token"])

    def test_options_shape_the_record(self) -> None:
        made = self.make("--permissions", "read,review", "--repos", "AStrasler/Egeregore", "--tools", "get_tasks,request_review", "--label", "laptop")
        self.assertEqual(made["record"]["permissions"], ["read", "review"])
        self.assertEqual(made["record"]["tools"], ["get_tasks", "request_review"])
        self.assertEqual(made["record"]["label"], "laptop")

    def test_the_readable_output_shows_the_token_once_and_writes_no_file(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            done = run(GENERATE, "--agent", "codex", cwd=empty)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(os.listdir(empty), [])
        self.assertEqual(len(re.findall(r"egr_[A-Za-z0-9_-]{43}", done.stdout)), 1)
        self.assertEqual(done.stderr, "")

    def test_it_refuses_an_unknown_agent_permission_or_flag(self) -> None:
        for args in (["--agent", "aaron"], ["--agent", "nobody"], [], ["--agent", "cursor", "--permissions", "admin"], ["--agent", "cursor", "--bogus"]):
            done = run(GENERATE, *args)
            self.assertNotEqual(done.returncode, 0, args)
            self.assertNotRegex(done.stdout, r"egr_")

    def test_hash_only_matches_sha256(self) -> None:
        token = "x" * 40
        done = run(GENERATE, "--hash-only", stdin=token + "\n")
        self.assertEqual(done.stdout.strip(), hashlib.sha256(token.encode()).hexdigest())
        self.assertNotEqual(run(GENERATE, "--hash-only", stdin="short").returncode, 0)


@unittest.skipIf(NODE is None or node_version() < (22, 6), "needs Node 22.6 or later to load TypeScript")
class CheckTests(unittest.TestCase):
    def setUp(self) -> None:
        done = run(GENERATE, "--agent", "grokbot", "--json")
        made = json.loads(done.stdout)
        self.token, self.record = made["token"], made["record"]

    def check(self, config: object, *args: str, token: str | None = None):
        env = {"TOKEN": token} if token is not None else None
        return run(CHECK, *args, stdin=config if isinstance(config, str) else json.dumps(config), env=env, strip=True)

    def test_a_generated_record_passes_the_workers_own_parser(self) -> None:
        done = self.check([self.record])
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("grokbot", done.stdout)
        self.assertNotIn(self.record["sha256"], done.stdout)

    def test_verify_names_the_agent_for_the_right_token_and_nobody_for_a_wrong_one(self) -> None:
        good = self.check([self.record], "--verify", token=self.token)
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertIn("matches a record for grokbot", good.stdout)
        self.assertNotIn(self.token, good.stdout + good.stderr)
        bad = self.check([self.record], "--verify", token="egr_" + "A" * 43)
        self.assertEqual(bad.returncode, 1)
        self.assertIn("matches no record", bad.stdout)

    def test_a_bad_config_is_named_and_exits_nonzero_without_echoing_values(self) -> None:
        sentinel = "SENTINEL-must-not-be-echoed"
        for config in ([{**self.record, "permissions": ["admin", sentinel]}], [{**self.record, "agent": sentinel}], "{", [], [self.record, self.record]):
            done = self.check(config)
            self.assertEqual(done.returncode, 1, config)
            self.assertIn("unauthenticated", done.stderr)
            self.assertNotIn(sentinel, done.stdout + done.stderr)


if __name__ == "__main__":
    unittest.main()
