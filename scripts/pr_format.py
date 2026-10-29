#!/usr/bin/env python3
"""PR description check: required sections, read-only, standard library only.

Every non-draft pull request from a person must have a `## What` section and a
`## Why` section with real text under them. When the diff touches any file that
is not Markdown, it also needs a `## Verification` section. `## Risk` and
`## Links` are allowed and never required.

    python3 scripts/pr_format.py --event "$GITHUB_EVENT_PATH" --base SHA --head SHA

The check reads the event payload and runs `git diff --name-only`. It calls no
network API, posts no comment, and needs only `contents: read`. A failure is an
exit code of 1 plus a plain-language job summary. The summary never repeats the
pull request text, so a description cannot smuggle text into the log.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

ALWAYS_REQUIRED = ("What", "Why")
VERIFICATION = "Verification"
OPTIONAL = ("Risk", "Links")
MARKDOWN_SUFFIXES = (".md", ".markdown")

_HEADING = re.compile(r"^ {0,3}(#{1,2})[ \t]+(.*?)[ \t]*#*[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)
_WORD = re.compile(r"[^\W_]")


def _strip_comments(line: str, in_comment: bool) -> tuple[str, bool]:
    """Remove HTML comment text from one line. Returns the rest and whether a comment is still open."""
    kept: list[str] = []
    pos = 0
    while pos < len(line):
        if in_comment:
            end = line.find("-->", pos)
            if end < 0:
                return "".join(kept), True
            pos = end + 3
            in_comment = False
        else:
            start = line.find("<!--", pos)
            if start < 0:
                kept.append(line[pos:])
                break
            kept.append(line[pos:start])
            pos = start + 4
            in_comment = True
    return "".join(kept), in_comment


def parse_sections(body: str) -> dict[str, str]:
    """Map each level-2 heading (lower case) to the text under it.

    A section runs to the next level-1 or level-2 heading. Deeper headings stay
    inside it. Headings inside a fenced code block are text, not headings.
    Text inside an HTML comment is hidden from the reader, so it can neither
    start, end nor fill a section. Comments inside a code fence are literal
    text. When a heading appears twice, the texts are joined.
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None
    fence: tuple[str, int] | None = None
    in_comment = False
    for raw in (body or "").splitlines():
        line = raw
        if fence is None:
            line, in_comment = _strip_comments(raw, in_comment)
        marker = _FENCE.match(line)
        if marker:
            run = marker.group(1)
            if fence is None:
                fence = (run[0], len(run))
            elif run[0] == fence[0] and len(run) >= fence[1] and not line.strip().strip(run[0]):
                fence = None
            if current is not None:
                sections[current].append(line)
            continue
        heading = None if fence else _HEADING.match(line)
        if heading:
            if len(heading.group(1)) == 2:
                current = heading.group(2).strip().lower()
                sections.setdefault(current, [])
            else:
                current = None
            continue
        if current is not None:
            sections[current].append(line)
    return {name: "\n".join(lines) for name, lines in sections.items()}


def has_content(text: str) -> bool:
    """True when the text holds at least one letter or digit outside comments."""
    return bool(_WORD.search(_COMMENT.sub("", text or "")))


def is_markdown(path: str) -> bool:
    return path.lower().endswith(MARKDOWN_SUFFIXES)


def touches_non_markdown(changed: Iterable[str] | None) -> bool:
    """True when any changed path is not Markdown. An unknown diff counts as yes."""
    if changed is None:
        return True
    return any(not is_markdown(path) for path in changed)


def is_bot(author_type: str, author_login: str) -> bool:
    return author_type == "Bot" or author_login.lower().endswith("[bot]")


@dataclass
class Result:
    skipped: str | None = None
    problems: list[str] = field(default_factory=list)
    verification_required: bool = False
    diff_known: bool = True

    @property
    def ok(self) -> bool:
        return not self.problems


def check(
    body: str | None,
    changed: Iterable[str] | None,
    *,
    draft: bool = False,
    author_type: str = "User",
    author_login: str = "",
) -> Result:
    """Judge one pull request. `changed` is None when the diff could not be read."""
    if draft:
        return Result(skipped="the pull request is a draft")
    if is_bot(author_type, author_login):
        return Result(skipped="the author is a bot")

    files = None if changed is None else list(changed)
    result = Result(diff_known=files is not None)
    result.verification_required = touches_non_markdown(files)
    sections = parse_sections(body or "")

    required = list(ALWAYS_REQUIRED)
    if result.verification_required:
        required.append(VERIFICATION)
    for name in required:
        text = sections.get(name.lower())
        if text is None:
            result.problems.append(f"`## {name}` is missing.{_why_needed(name, result)}")
        elif not has_content(text):
            result.problems.append(
                f"`## {name}` is empty. A heading, a comment or a placeholder alone does not count."
            )
    return result


def _why_needed(name: str, result: Result) -> str:
    if name != VERIFICATION:
        return ""
    if result.diff_known:
        return " It is required because the diff touches files that are not Markdown."
    return " It is required because the changed files could not be listed, so the check assumes code changed."


def render(result: Result) -> str:
    if result.skipped:
        return f"## PR format check\n\nSkipped: {result.skipped}.\n"
    lines = ["## PR format check", ""]
    if result.ok:
        lines.append("Passed. The required sections are present and not empty.")
        if result.verification_required:
            lines.append("`## Verification` was required because the diff touches files that are not Markdown.")
        lines.append("")
        return "\n".join(lines)
    lines.append("Failed. Edit the pull request description. The check reruns when you save it.")
    lines.append("")
    lines.extend(f"- {problem}" for problem in result.problems)
    lines.extend(
        [
            "",
            "Shape:",
            "",
            "```markdown",
            "## What",
            "The change, most important first.",
            "",
            "## Why",
            "The reason, which the diff cannot show.",
            "",
            "## Verification",
            "How it was checked. Required when any file that is not Markdown changes.",
            "```",
            "",
            "`## Risk` and `## Links` are optional.",
            "",
        ]
    )
    return "\n".join(lines)


def changed_files(base: str, head: str, cwd: str | None = None) -> list[str] | None:
    """Paths changed on `head` since it left `base` (merge base). None on any failure."""
    if not base or not head:
        return None
    try:
        out = subprocess.run(
            ["git", "diff", "--name-only", "--no-renames", "-z", f"{base}...{head}"],
            cwd=cwd,
            capture_output=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return [p for p in out.stdout.decode("utf-8", "surrogateescape").split("\0") if p]


def read_event(path: str) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    pr = data.get("pull_request")
    if not isinstance(pr, dict):
        raise ValueError("the event has no pull_request object")
    return pr


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--event", required=True, help="path to the pull_request event JSON")
    parser.add_argument("--base", default="", help="base commit SHA")
    parser.add_argument("--head", default="", help="head commit SHA")
    args = parser.parse_args(argv)

    try:
        pr = read_event(args.event)
    except (OSError, ValueError) as exc:
        print(f"::error title=PR format check::cannot read the event payload: {type(exc).__name__}")
        return 2

    user = pr.get("user") if isinstance(pr.get("user"), dict) else {}
    body = pr.get("body")
    draft = bool(pr.get("draft"))
    author_type = str(user.get("type") or "")
    author_login = str(user.get("login") or "")
    skipping = draft or is_bot(author_type, author_login)
    changed = None if skipping else changed_files(args.base, args.head)

    result = check(
        body if isinstance(body, str) else "",
        changed,
        draft=draft,
        author_type=author_type,
        author_login=author_login,
    )
    text = render(result)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")
    for problem in result.problems:
        print(f"::error title=PR format check::{problem.replace('`', '')}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
