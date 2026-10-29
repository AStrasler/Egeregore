#!/usr/bin/env python3
"""Tests for scripts/pr_format.py and the workflow and template around it. No network."""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pr_format as pf  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "pr-format.yml"
TEMPLATE = ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md"

GOOD = "## What\nAdds the check.\n\n## Why\nDescriptions were inconsistent.\n"
GOOD_CODE = GOOD + "\n## Verification\nRan the unit tests.\n"
MD_ONLY = ["README.md", "docs/guide.md"]
CODE = ["scripts/x.py"]


def problems(body, changed, **kwargs) -> list[str]:
    return pf.check(body, changed, **kwargs).problems


class ParseSectionsTests(unittest.TestCase):
    def test_splits_level_two_headings_and_ignores_case_and_closing_hashes(self) -> None:
        sections = pf.parse_sections("## WHAT ##\none\n## Why\ntwo\n")
        self.assertEqual(sections, {"what": "one", "why": "two"})

    def test_deeper_headings_stay_inside_their_section(self) -> None:
        sections = pf.parse_sections("## What\n### Detail\ntext\n## Why\nreason\n")
        self.assertIn("### Detail", sections["what"])
        self.assertEqual(sections["why"], "reason")

    def test_a_level_one_heading_ends_a_section(self) -> None:
        sections = pf.parse_sections("## What\ntext\n# Other\nstray\n## Why\nreason\n")
        self.assertEqual(sections["what"], "text")
        self.assertNotIn("stray", sections["why"])

    def test_headings_in_fenced_code_do_not_count(self) -> None:
        body = "## What\nexample:\n```\n## Why\nnot a heading\n```\n"
        self.assertEqual(list(pf.parse_sections(body)), ["what"])
        tilde = "## What\n~~~~\n## Why\n~~~\nstill code\n~~~~\n"
        self.assertEqual(list(pf.parse_sections(tilde)), ["what"])

    def test_a_shorter_fence_does_not_close_a_longer_one(self) -> None:
        body = "## What\n````\n```\n## Why\n````\n## Risk\nlow\n"
        self.assertEqual(list(pf.parse_sections(body)), ["what", "risk"])

    def test_indented_code_and_text_that_only_looks_like_a_heading(self) -> None:
        body = "## What\n    ## Why\nnot indented code? yes it is\n#Why\n"
        self.assertEqual(list(pf.parse_sections(body)), ["what"])

    def test_headings_inside_html_comments_are_hidden(self) -> None:
        body = "<!--\n## What\nhidden\n## Why\nhidden\n-->\n"
        self.assertEqual(pf.parse_sections(body), {})
        wrapped = "<!--\n## What\nreal looking\n\n## Why\nreal looking\n\n## Verification\nran it\n-->"
        self.assertEqual(len(problems(wrapped, CODE)), 3, "a fully commented description is missing every section")

    def test_a_commented_example_heading_does_not_end_a_section(self) -> None:
        body = "## What\nfirst\n<!--\n## Why\nexample\n-->\nsecond\n## Why\nreason\n"
        sections = pf.parse_sections(body)
        self.assertEqual(sections["what"].split(), ["first", "second"])
        self.assertEqual(sections["why"], "reason")
        self.assertEqual(problems(body, MD_ONLY), [])

    def test_comment_edges_on_one_line(self) -> None:
        self.assertEqual(list(pf.parse_sections("## What <!-- x -->\ntext\n")), ["what"])
        self.assertEqual(list(pf.parse_sections("<!-- a --> ## What\ntext\n")), ["what"])
        self.assertEqual(pf.parse_sections("## What\n<!-- a -->kept<!-- b -->\n")["what"], "kept")
        self.assertEqual(pf.parse_sections("## What\n<!-- a\nb --> after\n")["what"].strip(), "after")
        self.assertEqual(list(pf.parse_sections("<!-- ``` -->\n## What\ntext\n")), ["what"], "a fence marker in a comment is hidden")
        self.assertEqual(list(pf.parse_sections("## What\n<!-- never closed\n## Why\ntext\n")), ["what"])

    def test_a_comment_in_a_code_fence_is_literal_text(self) -> None:
        body = "## What\n```\n<!--\n```\n## Why\nreason\n"
        self.assertEqual(list(pf.parse_sections(body)), ["what", "why"])

    def test_duplicate_headings_are_joined(self) -> None:
        sections = pf.parse_sections("## What\none\n## Why\nx\n## What\ntwo\n")
        self.assertEqual(sections["what"], "one\ntwo")

    def test_crlf_and_empty_input(self) -> None:
        self.assertEqual(pf.parse_sections("## What\r\ntext\r\n")["what"], "text")
        self.assertEqual(pf.parse_sections(""), {})
        self.assertEqual(pf.parse_sections(None), {})  # type: ignore[arg-type]


class HasContentTests(unittest.TestCase):
    def test_text_counts(self) -> None:
        for text in ("x", "docs only", "42", "日本語"):
            with self.subTest(text=text):
                self.assertTrue(pf.has_content(text))

    def test_blank_comment_and_symbol_only_text_do_not_count(self) -> None:
        for text in ("", "   \n\t", "<!-- placeholder -->", "<!--\nmulti\nline\n-->", "- \n* \n---", "<!-- never closed\nstill comment"):
            with self.subTest(text=text):
                self.assertFalse(pf.has_content(text))

    def test_text_next_to_a_comment_counts(self) -> None:
        self.assertTrue(pf.has_content("<!-- hint -->\nreal words"))
        self.assertTrue(pf.has_content("real words <!-- hint -->"))


class FilePathTests(unittest.TestCase):
    def test_markdown_detection(self) -> None:
        for path in ("a.md", "docs/B.MD", "x.markdown"):
            self.assertTrue(pf.is_markdown(path), path)
        for path in ("a.py", "a.md.bak", "LICENSE", ".github/workflows/ci.yml", "md", "a.mdx"):
            self.assertFalse(pf.is_markdown(path), path)

    def test_touches_non_markdown(self) -> None:
        self.assertFalse(pf.touches_non_markdown([]))
        self.assertFalse(pf.touches_non_markdown(MD_ONLY))
        self.assertTrue(pf.touches_non_markdown(MD_ONLY + CODE))
        self.assertTrue(pf.touches_non_markdown(None), "an unknown diff fails closed")


class CheckTests(unittest.TestCase):
    def test_what_and_why_pass_for_a_markdown_only_diff(self) -> None:
        result = pf.check(GOOD, MD_ONLY)
        self.assertTrue(result.ok, result.problems)
        self.assertFalse(result.verification_required)
        self.assertIsNone(result.skipped)

    def test_missing_sections_are_named(self) -> None:
        for body, missing in (
            ("", ["What", "Why"]),
            (None, ["What", "Why"]),
            ("## Why\nreason\n", ["What"]),
            ("## What\nchange\n", ["Why"]),
            ("just a sentence, no headings", ["What", "Why"]),
            ("## What changed\nx\n## Why not\ny\n", ["What", "Why"]),
            ("### What\nx\n### Why\ny\n", ["What", "Why"]),
        ):
            with self.subTest(body=body):
                found = problems(body, MD_ONLY)
                self.assertEqual(len(found), len(missing), found)
                for name, line in zip(missing, found):
                    self.assertIn(f"`## {name}` is missing", line)

    def test_empty_sections_fail_even_with_a_heading(self) -> None:
        body = "## What\n<!-- hint -->\n\n## Why\n\n"
        found = problems(body, MD_ONLY)
        self.assertEqual(len(found), 2)
        self.assertTrue(all("is empty" in line for line in found), found)

    def test_a_section_is_not_filled_by_the_next_sections_text(self) -> None:
        found = problems("## What\n## Why\nreason\n", MD_ONLY)
        self.assertEqual(len(found), 1)
        self.assertIn("`## What` is empty", found[0])

    def test_verification_is_required_when_a_non_markdown_file_changes(self) -> None:
        result = pf.check(GOOD, CODE)
        self.assertTrue(result.verification_required)
        self.assertEqual(len(result.problems), 1)
        self.assertIn("`## Verification` is missing", result.problems[0])
        self.assertIn("not Markdown", result.problems[0])
        self.assertTrue(pf.check(GOOD_CODE, CODE).ok)

    def test_empty_verification_fails(self) -> None:
        found = problems(GOOD + "\n## Verification\n<!-- how -->\n", CODE)
        self.assertEqual(len(found), 1)
        self.assertIn("`## Verification` is empty", found[0])

    def test_verification_is_not_required_for_markdown_only_diffs(self) -> None:
        self.assertEqual(problems(GOOD, MD_ONLY), [])
        self.assertEqual(problems(GOOD, []), [])
        self.assertEqual(problems(GOOD + "\n## Verification\n\n", MD_ONLY), [], "optional there, so an empty one is fine")

    def test_unknown_diff_requires_verification_and_says_why(self) -> None:
        result = pf.check(GOOD, None)
        self.assertFalse(result.diff_known)
        self.assertEqual(len(result.problems), 1)
        self.assertIn("could not be listed", result.problems[0])

    def test_risk_and_links_are_optional_and_may_be_empty(self) -> None:
        for extra in ("", "\n## Risk\n\n## Links\n\n", "\n## Risk\nlow\n\n## Links\n#12\n"):
            with self.subTest(extra=extra):
                self.assertEqual(problems(GOOD_CODE + extra, CODE), [])

    def test_all_problems_are_reported_at_once(self) -> None:
        self.assertEqual(len(problems("", CODE)), 3)

    def test_drafts_are_skipped_even_when_the_body_is_empty(self) -> None:
        result = pf.check("", CODE, draft=True)
        self.assertEqual(result.problems, [])
        self.assertIn("draft", result.skipped or "")

    def test_bot_authors_are_skipped(self) -> None:
        for kwargs in ({"author_type": "Bot"}, {"author_login": "dependabot[bot]"}, {"author_login": "Some-App[BOT]"}):
            with self.subTest(kwargs=kwargs):
                result = pf.check("", CODE, **kwargs)
                self.assertEqual(result.problems, [])
                self.assertIn("bot", result.skipped or "")

    def test_people_are_not_skipped(self) -> None:
        self.assertIsNone(pf.check("", CODE, author_type="User", author_login="AStrasler").skipped)
        self.assertFalse(pf.check("", CODE, author_login="robot").ok)


class RenderTests(unittest.TestCase):
    def test_summary_for_each_outcome(self) -> None:
        self.assertIn("Skipped: the pull request is a draft.", pf.render(pf.check("", CODE, draft=True)))
        passed = pf.render(pf.check(GOOD_CODE, CODE))
        self.assertIn("Passed.", passed)
        self.assertIn("`## Verification` was required", passed)
        failed = pf.render(pf.check("", CODE))
        self.assertIn("Failed.", failed)
        self.assertIn("- `## What` is missing.", failed)
        self.assertIn("optional", failed)

    def test_summary_never_repeats_the_description(self) -> None:
        marker = "UNIQUE-TEXT-FROM-THE-DESCRIPTION"
        body = f"## What\n\n## Why\n\n## Risk\n{marker}\n"
        self.assertNotIn(marker, pf.render(pf.check(body, CODE)))


def git(cwd: str, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


class ChangedFilesTests(unittest.TestCase):
    def test_lists_paths_since_the_merge_base_without_renames(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            git(tmp, "init", "-q", "-b", "main")
            Path(tmp, "keep.md").write_text("a\n", encoding="utf-8")
            Path(tmp, "old.md").write_text("old\n", encoding="utf-8")
            git(tmp, "add", ".")
            git(tmp, "commit", "-q", "-m", "base")
            base = git(tmp, "rev-parse", "HEAD")
            git(tmp, "checkout", "-q", "-b", "topic")
            git(tmp, "mv", "old.md", "new.py")
            Path(tmp, "weird name.txt").write_text("x\n", encoding="utf-8")
            git(tmp, "add", ".")
            git(tmp, "commit", "-q", "-m", "topic")
            head = git(tmp, "rev-parse", "HEAD")
            git(tmp, "checkout", "-q", "main")
            Path(tmp, "main-only.py").write_text("later\n", encoding="utf-8")
            git(tmp, "add", ".")
            git(tmp, "commit", "-q", "-m", "main moved on")
            moved = git(tmp, "rev-parse", "HEAD")
            expected = ["new.py", "old.md", "weird name.txt"]
            self.assertEqual(sorted(pf.changed_files(base, head, cwd=tmp) or []), expected)
            self.assertEqual(sorted(pf.changed_files(moved, head, cwd=tmp) or []), expected, "main moving on adds nothing")
            self.assertTrue(pf.touches_non_markdown(pf.changed_files(base, head, cwd=tmp)))

    def test_a_failure_gives_none_so_the_check_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            git(tmp, "init", "-q", "-b", "main")
            self.assertIsNone(pf.changed_files("deadbeef", "cafef00d", cwd=tmp))
            self.assertIsNone(pf.changed_files("", "x", cwd=tmp))
            self.assertIsNone(pf.changed_files("x", "", cwd=tmp))
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(pf.changed_files("a", "b", cwd=tmp), "not a repository")


class MainTests(unittest.TestCase):
    def run_main(self, event, *extra: str, summary: bool = True) -> tuple[int, str, str]:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "event.json")
            path.write_text(event if isinstance(event, str) else json.dumps(event), encoding="utf-8")
            summary_path = Path(tmp, "summary.md")
            old = os.environ.get("GITHUB_STEP_SUMMARY")
            if summary:
                os.environ["GITHUB_STEP_SUMMARY"] = str(summary_path)
            else:
                os.environ.pop("GITHUB_STEP_SUMMARY", None)
            out = io.StringIO()
            try:
                with redirect_stdout(out):
                    code = pf.main(["--event", str(path), *extra])
            finally:
                if old is None:
                    os.environ.pop("GITHUB_STEP_SUMMARY", None)
                else:
                    os.environ["GITHUB_STEP_SUMMARY"] = old
            written = summary_path.read_text(encoding="utf-8") if summary_path.exists() else ""
            return code, out.getvalue(), written

    @staticmethod
    def event(body, *, draft=False, kind="User", login="AStrasler"):
        return {"pull_request": {"body": body, "draft": draft, "user": {"type": kind, "login": login}}}

    def test_a_failing_description_exits_one_and_writes_the_summary_and_an_annotation(self) -> None:
        code, out, summary = self.run_main(self.event("no sections"))
        self.assertEqual(code, 1)
        self.assertIn("Failed.", summary)
        self.assertIn("::error title=PR format check::## What is missing.", out)
        self.assertNotIn("`", "".join(line for line in out.splitlines() if line.startswith("::error")))

    def test_without_a_readable_diff_the_check_assumes_code_changed(self) -> None:
        code, _, _ = self.run_main(self.event(GOOD))
        self.assertEqual(code, 1, "no base or head given, so the diff is unknown and Verification is needed")
        code, _, summary = self.run_main(self.event(GOOD_CODE))
        self.assertEqual(code, 0)
        self.assertIn("Passed.", summary)

    def test_draft_and_bot_events_exit_zero_without_reading_git(self) -> None:
        for event in (self.event("", draft=True), self.event("", kind="Bot", login="x[bot]")):
            with self.subTest(event=event):
                code, out, summary = self.run_main(event)
                self.assertEqual(code, 0)
                self.assertIn("Skipped", out)
                self.assertIn("Skipped", summary)

    def test_a_missing_body_is_an_empty_description(self) -> None:
        self.assertEqual(self.run_main(self.event(None))[0], 1)

    def test_prints_without_a_summary_file(self) -> None:
        code, out, summary = self.run_main(self.event(GOOD_CODE), summary=False)
        self.assertEqual((code, summary), (0, ""))
        self.assertIn("Passed.", out)

    def test_an_unreadable_event_is_an_error_not_a_pass(self) -> None:
        for event in ("not json", json.dumps({"no": "pull_request"}), json.dumps({"pull_request": 3})):
            with self.subTest(event=event):
                code, out, _ = self.run_main(event)
                self.assertEqual(code, 2)
                self.assertIn("::error", out)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(pf.main(["--event", "/nonexistent/event.json"]), 2)


class TemplateTests(unittest.TestCase):
    def test_an_unfilled_template_fails_and_a_filled_one_passes(self) -> None:
        template = TEMPLATE.read_text(encoding="utf-8")
        found = problems(template, CODE)
        self.assertEqual(len(found), 3, found)
        self.assertTrue(all("is empty" in line for line in found), found)
        names = list(pf.parse_sections(template))
        for name in ("what", "why", "verification", "risk", "links"):
            self.assertIn(name, names)

        filled = template
        for name in ("What", "Why", "Verification"):
            filled = re.sub(rf"(## {name}\n\n)", rf"\1Real text for {name.lower()}.\n", filled, count=1)
        self.assertEqual(problems(filled, CODE), [])

    def test_the_old_heading_is_gone(self) -> None:
        self.assertNotIn("## What changed", TEMPLATE.read_text(encoding="utf-8"))


class WorkflowTests(unittest.TestCase):
    text = WORKFLOW.read_text(encoding="utf-8")

    def test_runs_on_pull_requests_with_the_description_events_and_nothing_else(self) -> None:
        self.assertIn("pull_request:", self.text)
        self.assertIn("types: [opened, edited, reopened, synchronize, ready_for_review]", self.text)
        self.assertNotIn("pull_request_target", self.text)
        self.assertNotIn("workflow_run", self.text)

    def test_is_read_only_and_posts_nothing(self) -> None:
        self.assertEqual(re.findall(r"(?m)^permissions:[ \t]*\n((?:[ \t]+\S.*\n)+)", self.text), ["  contents: read\n"])
        self.assertNotIn("write", re.sub(r"(?m)^#.*$", "", self.text))
        for forbidden in ("github-script", "createComment", "gh pr comment", "gh api", "secrets."):
            self.assertNotIn(forbidden, self.text)

    def test_skips_drafts_and_bots_at_the_job(self) -> None:
        self.assertIn("github.event.pull_request.draft == false", self.text)
        self.assertIn("github.event.pull_request.user.type != 'Bot'", self.text)

    def test_every_action_is_pinned_to_a_full_commit(self) -> None:
        uses = re.findall(r"uses:\s*(\S+)", self.text)
        self.assertEqual(uses, ["actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"])
        for use in uses:
            self.assertRegex(use, r"@[0-9a-f]{40}$")

    def test_the_description_never_reaches_a_shell(self) -> None:
        # Only SHAs go through env. The body and title are read from the event file.
        self.assertNotIn("pull_request.body", self.text)
        self.assertNotIn("pull_request.title", self.text)
        self.assertEqual(sorted(re.findall(r"\$\{\{\s*([^}]+?)\s*\}\}", self.text)),
                         ["github.event.pull_request.base.sha", "github.event.pull_request.head.sha", "github.event.pull_request.number"])

    def test_the_check_job_name_is_stable(self) -> None:
        # The job name is what a ruleset would list as a required check.
        self.assertRegex(self.text, r"(?m)^  pr-format:\n    name: pr-format\n")

    def test_runs_the_checker_with_the_base_and_head(self) -> None:
        self.assertIn('python3 scripts/pr_format.py --event "$GITHUB_EVENT_PATH" --base "$BASE_SHA" --head "$HEAD_SHA"', self.text)
        self.assertIn("fetch-depth: 0", self.text)
        self.assertIn("persist-credentials: false", self.text)

    def test_is_not_made_required_by_anything_in_the_repo(self) -> None:
        operations = (ROOT / "OPERATIONS.md").read_text(encoding="utf-8")
        self.assertIn("not a required check", operations)


if __name__ == "__main__":
    unittest.main()
