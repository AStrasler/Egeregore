#!/usr/bin/env python3
"""The deploy runbook's scripts: provision-agent-tokens.mjs and smoke-test.mjs.

provision-agent-tokens.mjs makes the whole token set on the box. smoke-test.mjs
checks a deployed Worker. The full smoke logic runs against the real Worker
code in worker/test/smoke.test.ts. These tests cover the command lines: what
they refuse, what they write, and that no token reaches their output. Tokens
are made at run time, so no token-shaped text is stored in the repository.

Both scripts load TypeScript, which needs Node 22.6 or later. On an older Node
the tests are skipped and say so.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVISION = ROOT / "worker" / "scripts" / "provision-agent-tokens.mjs"
SMOKE = ROOT / "worker" / "scripts" / "smoke-test.mjs"
NODE = shutil.which("node")
TOKEN_RE = re.compile(r"egr_[A-Za-z0-9_-]{43}")


def node_version() -> tuple[int, int]:
    if NODE is None:
        return (0, 0)
    out = subprocess.run([NODE, "--version"], capture_output=True, text=True, check=True).stdout.strip()
    match = re.match(r"v(\d+)\.(\d+)", out)
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


def run(script: Path, *args: str, stdin: str = ""):
    env = {k: v for k, v in os.environ.items() if k != "NODE_OPTIONS"}
    return subprocess.run(
        [NODE, "--experimental-strip-types", "--no-warnings", str(script), *args],
        input=stdin, capture_output=True, text=True, env=env, timeout=120,
    )


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@unittest.skipIf(NODE is None or node_version() < (22, 6), "needs Node 22.6 or later to load TypeScript")
class ProvisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "secrets" / "first"

    def provision(self, *args: str):
        return run(PROVISION, "--out-dir", str(self.out), *args)

    def test_it_writes_two_private_files_and_prints_no_token(self) -> None:
        done = self.provision()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIsNone(TOKEN_RE.search(done.stdout + done.stderr))
        private = self.out / "agent-tokens.private.json"
        records = self.out / "agent-tokens.records.json"
        self.assertEqual(mode(self.out), 0o700)
        self.assertEqual(mode(private), 0o600)
        self.assertEqual(mode(records), 0o600)
        raw = json.loads(private.read_text())
        self.assertEqual(raw["version"], 1)
        self.assertEqual([item["agent"] for item in raw["tokens"]], ["chatgpt", "grokbot", "cursor", "codex"])
        self.assertTrue(all(TOKEN_RE.fullmatch(item["token"]) for item in raw["tokens"]))
        hashes = json.loads(records.read_text())
        self.assertEqual(len(hashes), 4)
        self.assertIsNone(TOKEN_RE.search(records.read_text()), "the records file must hold hashes only")
        self.assertNotIn("aaron", [item["agent"] for item in raw["tokens"]])

    def test_the_records_pass_the_workers_own_parser(self) -> None:
        self.assertEqual(self.provision().returncode, 0)
        check = ROOT / "worker" / "scripts" / "check-agent-tokens.mjs"
        done = run(check, str(self.out / "agent-tokens.records.json"))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("OK: 4 record(s)", done.stdout)

    def test_a_plan_file_sets_agents_and_permissions(self) -> None:
        plan = Path(self.tmp.name) / "plan.json"
        plan.write_text(json.dumps({"tokens": [{"agent": "codex", "permissions": ["read"], "label": "only codex"}]}))
        done = self.provision("--plan", str(plan))
        self.assertEqual(done.returncode, 0, done.stderr)
        raw = json.loads((self.out / "agent-tokens.private.json").read_text())
        self.assertEqual([(t["agent"], t["permissions"], t["label"]) for t in raw["tokens"]], [("codex", ["read"], "only codex")])

    def test_a_bad_plan_writes_nothing(self) -> None:
        plan = Path(self.tmp.name) / "plan.json"
        for entry in ({"agent": "aaron"}, {"agent": "cursor", "permissions": ["admin"]}, {"agent": "cursor", "repos": ["*"]}):
            plan.write_text(json.dumps([entry]))
            done = self.provision("--plan", str(plan))
            self.assertNotEqual(done.returncode, 0, entry)
            self.assertFalse((self.out / "agent-tokens.private.json").exists(), entry)

    def test_it_never_overwrites(self) -> None:
        self.assertEqual(self.provision().returncode, 0)
        before = (self.out / "agent-tokens.private.json").read_text()
        done = self.provision()
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("already exists", done.stderr)
        self.assertEqual((self.out / "agent-tokens.private.json").read_text(), before)

    def test_it_refuses_a_directory_inside_a_git_work_tree_or_a_loose_one(self) -> None:
        inside = ROOT / "tests" / "should-not-exist-secrets"
        done = run(PROVISION, "--out-dir", str(inside))
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("git work tree", done.stderr)
        self.assertFalse(inside.exists())
        loose = Path(self.tmp.name) / "loose"
        loose.mkdir(mode=0o755)
        loose.chmod(0o755)
        done = run(PROVISION, "--out-dir", str(loose))
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("0700", done.stderr)
        done = run(PROVISION, "--out-dir", "relative/dir")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("absolute", done.stderr)


class Stub(BaseHTTPRequestHandler):
    """A server that knows no tokens: it lists the tools and refuses every call."""

    TOOLS = ["ping", "get_tasks", "get_task", "get_task_history", "get_owner_decisions", "get_specialists",
             "claim_task", "renew_lease", "release_task", "post_progress", "handoff_task", "request_review"]
    seen_authorization: list[str] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("content-length", "0"))
        body = json.loads(self.rfile.read(length))
        Stub.seen_authorization.append(self.headers.get("authorization", ""))
        if body["method"] == "tools/list":
            result = {"tools": [{"name": name} for name in self.TOOLS]}
        else:
            envelope = {"ok": False, "result": None, "error": {"code": "unauthenticated", "message": "no"}}
            result = {"content": [{"type": "text", "text": json.dumps(envelope)}], "structuredContent": envelope}
        payload = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args) -> None:
        pass


@unittest.skipIf(NODE is None or node_version() < (22, 6), "needs Node 22.6 or later to load TypeScript")
class SmokeCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = HTTPServer(("127.0.0.1", 0), Stub)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        done = run(PROVISION, "--out-dir", str(Path(self.tmp.name) / "keys"))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.file = Path(self.tmp.name) / "keys" / "agent-tokens.private.json"
        self.tokens = [item["token"] for item in json.loads(self.file.read_text())["tokens"]]

    def smoke(self, *args: str, url: str | None = None):
        return run(SMOKE, "--url", url or f"http://127.0.0.1:{self.port}/mcp", "--tokens-file", str(self.file), "--issue", "5", *args)

    def test_a_server_that_rejects_every_token_fails_and_no_token_is_printed(self) -> None:
        done = self.smoke("--no-write")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("FAIL", done.stdout)
        self.assertIn("failed", done.stdout)
        for token in self.tokens:
            self.assertNotIn(token, done.stdout + done.stderr)
        self.assertTrue(any(value.startswith("Bearer ") for value in Stub.seen_authorization))

    def test_it_refuses_a_bad_url_a_loose_file_or_a_file_in_a_repository(self) -> None:
        for url in ("http://example.com/mcp", "https://example.com/other", "https://user:pw@example.com/mcp", "nonsense"):
            done = self.smoke(url=url)
            self.assertEqual(done.returncode, 2, url)
        self.file.chmod(0o644)
        done = self.smoke()
        self.assertEqual(done.returncode, 2)
        self.assertIn("group or others", done.stderr)
        self.file.chmod(0o600)
        copy = ROOT / "tests" / "smoke-should-not-exist.json"
        try:
            shutil.copy(self.file, copy)
            copy.chmod(0o600)
            done = run(SMOKE, "--url", f"http://127.0.0.1:{self.port}/mcp", "--tokens-file", str(copy), "--issue", "5")
            self.assertEqual(done.returncode, 2)
            self.assertIn("git work tree", done.stderr)
            for token in self.tokens:
                self.assertNotIn(token, done.stdout + done.stderr)
        finally:
            copy.unlink(missing_ok=True)

    def test_it_needs_a_url_a_file_and_an_issue(self) -> None:
        for args in ([], ["--url", "https://example.com/mcp"], ["--tokens-file", str(self.file), "--issue", "x", "--url", "https://example.com/mcp"]):
            self.assertEqual(run(SMOKE, *args).returncode, 2, args)


if __name__ == "__main__":
    unittest.main()
