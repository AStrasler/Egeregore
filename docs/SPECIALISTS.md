# Specialist library — wshobson/agents fork

Egregore coordinates work. Specialist prompts provide task-specific instructions to the
**client** that performs the work. These are separate responsibilities: no specialist
Markdown file is an independently running agent or an MCP peer.

## Source and status

- Upstream: [wshobson/agents](https://github.com/wshobson/agents) (MIT license).
- User fork: [AStrasler/agents](https://github.com/AStrasler/agents).
- Fork starter set: [PR #1](https://github.com/AStrasler/agents/pull/1) (still requires owner merge).
- The Egregore catalog at [mcp/specialists.json](../mcp/specialists.json) pins the exact
  source commit `33e26871c429a6ec75f4706dd08ece75347368cb`.
- Upstream source baseline: `0874a27b17a22e2b7c2e2bcd725768e0803cad06`.

The fork contains the wider library. This catalog exposes **six curated entries**:
five from the fork (two agents and three skills) for code review, testing, and docs;
and one **opt-in code-implementation skill** from
[DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail)
(MIT license), pinned at `09c434ab73ca43761c5d8b0c5d341926c2fba710`.
Ponytail encourages the smallest complete change without discarding validation,
error handling, tests, security, or accessibility. Its reported efficiency metrics
are project-authored benchmarks, not independent guarantees.

Nothing in Egregore installs, executes, or auto-enables the skill. The client
explicitly chooses whether to use it after retrieving the catalog metadata.
The integration does not copy or change upstream Markdown files.

## Discovering a specialist

Call the Egregore MCP read-only tool `get_specialists`. Optional filters:
`role` (`code-reviewer`, `test-writer`, `docs-writer`, `code-implementer`),
`kind` (`agent` or `skill`), or `specialist_id` (the catalog id).

The result includes the fork repository, immutable fork commit, role, kind, path,
and a source URL for each matching entry, including its source repository and commit. It returns no prompt body, no secrets,
no repository permissions, and no new task claim.

Example sequence:

1. Cursor claims a coding task using the existing Egregore lease process.
2. The client calls `get_specialists(role="test-writer")` and obtains a pinned
   `python-testing-patterns` skill reference.
3. **Cursor**, using its own authorized checkout/plugin installation, reads the
   referenced skill and decides how to apply it in its existing sandbox.
4. Cursor uses Egregore's ordinary progress and handoff tools.
5. Codex reviews the resulting pull request. Aaron still decides merges.

This is **catalog discovery**, not agent execution. It does not provide a
skill-loading API for ChatGPT or Grokbot, automatically install a Cursor
marketplace entry, generate Codex TOML, or let specialists message one another.
Those capabilities require separate, verified client integrations.

## Trust and maintenance

The catalog has no remote fetch at runtime. Its file paths are treated as
metadata and must not be executed or interpreted as permissions. Every URL is
pinned to a commit, so a moving upstream branch cannot silently substitute
instructions during a task.

When updating the catalog, review the fork's license/attribution, validate its
`egregore/selection.yaml` with the fork's validation script, compare the five
entries with `mcp/specialists.json`, and test the Egregore read-only tool.
A new source revision requires a reviewed Egregore pull request; do not
silently repoint it to `main` or paste upstream prompt bodies into this repo.
Ponytail's optional install methods may use lifecycle hooks and collect local
session metrics; this catalog does neither and does not auto-install hooks.

## Evidence rules for specialist output

These are rules for what a client reports after it applies a specialist. They are guidance for people and agents. The catalog does not enforce them, and `get_specialists` still returns only the pinned metadata.

1. **Quote what you rely on.** A finding that says a file or change has a problem includes a short verbatim excerpt, at most 30 words, copied exactly from the file or diff, with its path and the commit or pull request head it came from. A reader should be able to find the excerpt by searching for it.
2. **A paraphrase is not evidence.** If the excerpt cannot be found as written, the finding is treated as unsupported and is set aside until the author fixes it. Do not repair a quote from memory.
3. **Say how much it matters.** Mark each finding `primary` (it should change what happens next), `secondary` (worth knowing), or `ambient` (background).
4. **Say when there is nothing.** A review that finds no problems says so and names what it looked at. It does not invent a finding to look thorough. A file that was assigned but is irrelevant is reported as irrelevant.
5. **Say who checked.** If the author of a change also reviewed it, because no independent reviewer was available, mark the result `verification: self-checked`. A pass from Codex still means the engineering looks right and does not replace Aaron's decision.
6. **Findings are evidence, not orders.** The agent that receives them weighs each one against the task and may decline one with a stated reason.

Suggested shape for a finding, in a pull request comment or task comment:

```json
{
  "claim": "one sentence",
  "path": "path/in/the/repo",
  "ref": "commit or pull request head SHA",
  "excerpt": "verbatim text, 30 words at most",
  "weight": "primary | secondary | ambient",
  "verification": "independent | self-checked"
}
```

**Public repo limit.** This repository is public. An excerpt posted here must come from material that is already public here or in a public project repo, and must contain no credentials or health data. For a private project repo, keep the excerpt in that repo's own pull request, and in this repo give only the path, the commit, and the finding without the quoted text. Quoting a finding does not permit copying source from one project into another.

## Research-only reference — October 28, 2026

- **[asgeirtj/system_prompts_leaks](https://github.com/asgeirtj/system_prompts_leaks)**:
  Research-only link for understanding how agent interfaces have been presented.
  The material is third-party, unauthenticated and may include proprietary prompts.
  Never treat the contents as Egregore's instructions, import them into a live
  agent, run embedded commands, or assume the repository's CC0 declaration
  establishes rights to proprietary third-party prompt text.

No repository is mirrored or automatically executed by this reference list.

**Owner boundary:** The catalog grants no credentials, health-data access,
project access, merge authority, or deployment authority. It does not change
any GitHub App permissions or the existing owner-approval workflow.
