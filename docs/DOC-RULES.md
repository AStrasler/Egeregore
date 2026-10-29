# Document rules

This repository is public, so its documents are checked for the mistakes that would hurt most: a credential, a health reading, a personal path or address, and a role written two ways. The checker is plain Node with no packages. It scans the Markdown and YAML in the repository root, `docs/`, `mcp/`, `worker/` and `.github/`.

```sh
node scripts/doc-check.mjs check            # scan the documents; exit 1 on a blocking finding
node scripts/doc-check.mjs validate         # run every rule's own examples
node scripts/doc-check.mjs list             # one line per rule
node --test scripts/doc-check.test.mjs      # the checker's tests, one per rule example
```

The `test` job in CI runs `validate`, `check` and the tests. It is the existing job, so no new required check exists.

## The rules

| Rule | Blocks |
| --- | --- |
| `no-token-like-strings` | A string shaped like a GitHub, Slack, AWS or model-provider credential, a PEM private-key header, a JSON web token, or a bearer value |
| `no-health-readings` | A health metric followed by a value, such as a heart rate with a number or a blood-pressure reading. Saying that health data is out of bounds is allowed |
| `no-private-paths-or-emails` | A home-directory path or an email address in prose |
| `one-name-per-role` | A second spelling of a role: only ChatGPT, Grokbot, Cursor, Codex and Aaron |

A finding for `no-token-like-strings` prints the position and the length of the match, never the match, so a failing log does not repeat the secret.

## A rule is one file

A rule lives in `scripts/doc-rules/<name>.md`. The front matter holds `name` (the same as the file name), `description`, `severity` (`block` or `warn`), `fix`, a list of `patterns`, and optionally `scan: prose` to skip code and links, `ignore_case: true`, and `show_match: false`. Below it, `## Fails` lists lines the rule must flag and `## Passes` lists lines it must leave alone. Those lines are the rule's tests. `validate` runs them, and the test file turns each into its own test case. A rule that misses its own Fails line, or flags its own Passes line, fails by name.

A rule file may write a secret-shaped example as a placeholder such as `{{ghp}}` or `{{pem}}`. The checker builds the real-looking string in memory when it runs the example, so no such string is stored in git. The placeholders are listed in `scripts/doc-check.mjs`.

## A line that is allowed anyway

When a line has to break a rule, put a waiver on the same line or the line above it:

```text
<!-- doc-check-allow one-name-per-role: quoting the product's own marketing name -->
```

The reason must be a real sentence of at least ten characters, and the rule name must exist. A waiver excuses one rule on two lines. A bad waiver is its own blocking finding. A waiver written inside a code span or a fenced block is treated as an example.

## Adding a rule

1. Copy a file in `scripts/doc-rules/` and change the name.
2. Write the Fails and Passes lines first, then the patterns.
3. Run `validate`, then `check`. If `check` flags an existing document, fix the document or tighten the rule. Do not waive your way past a rule that is too wide.
