#!/usr/bin/env python3
"""Tests for read-only specialist discovery from the pinned fork catalog."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp"))

from egregore_mcp.specialists import get_specialists  # noqa: E402


class SpecialistCatalogTests(unittest.TestCase):
    def test_list_points_to_fork_commit_without_prompt_contents(self) -> None:
        response = get_specialists()
        self.assertTrue(response["ok"], response)
        result = response["result"]
        self.assertEqual(result["count"], 5)
        self.assertEqual(result["capability"], "catalog-only")
        self.assertEqual(result["fork_repository"], "AStrasler/agents")
        self.assertEqual(
            result["fork_commit"], "33e26871c429a6ec75f4706dd08ece75347368cb"
        )
        for specialist in result["specialists"]:
            self.assertTrue(
                specialist["source_url"].startswith(
                    "https://github.com/AStrasler/agents/blob/"
                    "33e26871c429a6ec75f4706dd08ece75347368cb/"
                )
            )
            self.assertNotIn("prompt", specialist)
            self.assertNotIn("instructions", specialist)
            self.assertIn(specialist["kind"], ("agent", "skill"))

    def test_filter_by_role_kind_or_id(self) -> None:
        reviewer = get_specialists(role="code-reviewer")["result"]["specialists"]
        self.assertEqual(len(reviewer), 2)
        skills = get_specialists(kind="skill")["result"]["specialists"]
        self.assertEqual(len(skills), 3)
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


if __name__ == "__main__":
    unittest.main()
