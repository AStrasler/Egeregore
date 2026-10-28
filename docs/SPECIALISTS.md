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

The fork contains the wider library. This first integration exposes **five curated
entries** (two agents and three skills) across code review, tests, and documentation.
It does not copy, install, or change upstream Markdown files.

## Discovering a specialist

Call the Egregore MCP read-only tool `get_specialists`. Optional filters:
`role` (`code-reviewer`, `test-writer`, `docs-writer`),
`kind` (`agent` or `skill`), or `specialist_id` (the catalog id).

The result includes the fork repository, immutable fork commit, role, kind, path,
and a source URL for each matching entry. It returns no prompt body, no secrets,
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

**Owner boundary:** The catalog grants no credentials, health-data access,
project access, merge authority, or deployment authority. It does not change
any GitHub App permissions or the existing owner-approval workflow.
