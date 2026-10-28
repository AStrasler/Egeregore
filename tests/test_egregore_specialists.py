#!/usr/bin/env python3
"""Tests for read-only specialist discovery from the pinned source catalog."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp"))

from egregore_mcp import specialists  # noqa: E402

get_specialists = specialists.get_specialists


class SpecialistCatalogTests(unittest.TestCase):
    def test_list_uses_immutable_sources_without_prompt_contents(self) -> None:
        response = get_specialists()
        self.assertTrue(response["ok"], response)
        result = response["result"]
        self.assertEqual(result["count"], 6)
        self.assertEqual(result["capability"], "catalog-only")
        self.assertEqual(result["fork_repository"], "AStrasler/agents")
        self.assertEqual(
            result["fork_commit"], "33e26871c429a6ec75f4706dd08ece75347368cb"
        )
        for entry in result["specialists"]:
            self.assertEqual(
                entry["source_url"],
                f"https://github.com/{entry['source_repository']}/blob/"
                f"{entry['source_commit']}/{entry['path']}",
            )
            self.assertNotIn("prompt", entry)
            self.assertNotIn("instructions", entry)
            self.assertIn(entry["kind"], ("agent", "skill"))
        fork_entries = [
            entry for entry in result["specialists"]
            if entry["source_repository"] == "AStrasler/agents"
        ]
        self.assertEqual(len(fork_entries), 5)

    def test_opt_in_ponytail_skill_is_pinned_and_role_scoped(self) -> None:
        selected = get_specialists(specialist_id="ponytail")["result"]["specialists"]
        self.assertEqual(len(selected), 1)
        entry = selected[0]
        self.assertEqual(entry["role"], "code-implementer")
        self.assertEqual(entry["kind"], "skill")
        self.assertEqual(entry["source_repository"], "DietrichGebert/ponytail")
        self.assertEqual(entry["source_commit"], "09c434ab73ca43761c5d8b0c5d341926c2fba710")
        self.assertEqual(entry["path"], "skills/ponytail/SKILL.md")
        self.assertEqual(
            get_specialists(role="code-implementer")["result"]["specialists"],
            selected,
        )

    def test_filter_by_role_kind_or_id(self) -> None:
        reviewer = get_specialists(role="code-reviewer")["result"]["specialists"]
        self.assertEqual(len(reviewer), 2)
        skills = get_specialists(kind="skill")["result"]["specialists"]
        self.assertEqual(len(skills), 4)
        selected = get_specialists(specialist_id="python-testing-patterns")["result"]["specialists"]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["role"], "test-writer")
        empty = get_specialists(specialist_id="nonexistent")
        self.assertEqual(empty["result"]["count"], 0)

    def test_invalid_selector_is_rejected(self) -> None:
        for args in (
            {"role": "../admin"},
            {"kind": "procedure"},
            {"specialist_id": "oops/../../etc"},
            {"kind": "Agent"},
        ):
            result = get_specialists(**args)
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "invalid_input")

    def test_unreviewed_external_source_is_refused(self) -> None:
        raw = json.loads(specialists._CATALOG_FILE.read_text(encoding="utf-8"))
        for change in (
            {"source_repository": "untrusted/prompts"},
            {"source_commit": "a" * 40},
            {"path": "../secrets"},
        ):
            bad = json.loads(json.dumps(raw))
            bad["entries"][-1].update(change)
            with tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "catalog.json"
                path.write_text(json.dumps(bad), encoding="utf-8")
                with patch.object(specialists, "_CATALOG_FILE", path):
                    response = get_specialists()
            self.assertFalse(response["ok"], change)
            self.assertEqual(response["error"]["code"], "unconfigured")


if __name__ == "__main__":
    unittest.main()
