# Third-party notices

This repository is licensed under the Apache License, Version 2.0 (see [LICENSE](LICENSE)). Parts of its formats and documentation were adapted from the work below, which is licensed under the MIT License. Those parts keep the notice that follows.

## Egregore (egregore-labs/egregore)

- Source repository: https://github.com/egregore-labs/egregore
- Source commit: `d570d103df64518ad56ea864c334aae0fc0c787c` (`d570d10`)
- License: MIT, from the `LICENSE` file at that commit.
- Copyright holder line: `Copyright (c) 2026 Egregore Labs`

### What was adapted

The adapted material is written in this repository's own words and fitted to its GitHub-issue workflow. No source file was copied whole. The notice is included because the structure and wording of some passages follow the sources.

| Piece | Where it is used here | Source files at `d570d10` |
|---|---|---|
| Handoff fields: claim, ask, intent (`action`, `feedback`, `fyi`), decisions, open threads, next steps | [.github/ISSUE_TEMPLATE/handoff.yml](.github/ISSUE_TEMPLATE/handoff.yml), [HANDOFFS.md](HANDOFFS.md) | [`.claude/skills/handoff/SKILL.md`](https://github.com/egregore-labs/egregore/blob/d570d10/.claude/skills/handoff/SKILL.md), [`skills/preview-design/samples/handoff.json`](https://github.com/egregore-labs/egregore/blob/d570d10/skills/preview-design/samples/handoff.json) |
| Decision records: supersession pointer and duplicate check | [.github/ISSUE_TEMPLATE/owner-decision.yml](.github/ISSUE_TEMPLATE/owner-decision.yml), [AGENTS.md](AGENTS.md), [docs/RUNBOOK.md](docs/RUNBOOK.md) | [`egregore_runtime/knowledge.py`](https://github.com/egregore-labs/egregore/blob/d570d10/egregore_runtime/knowledge.py), [`.claude/skills/reflect/SKILL.md`](https://github.com/egregore-labs/egregore/blob/d570d10/.claude/skills/reflect/SKILL.md) |
| Verbatim-evidence rules for specialist output | [docs/SPECIALISTS.md](docs/SPECIALISTS.md) | [`.claude/skills/deep-reflect/ROLES.md`](https://github.com/egregore-labs/egregore/blob/d570d10/.claude/skills/deep-reflect/ROLES.md) |
| Canary secret-leak tests: fake credentials fed through error and log paths, with a manifest where each path is covered or exempt with a dated reason | [tests/test_secret_hygiene.py](tests/test_secret_hygiene.py), [worker/test/secret-hygiene.test.ts](worker/test/secret-hygiene.test.ts), [tests/secret-hygiene.manifest.json](tests/secret-hygiene.manifest.json) | [`bin/tests/test-secret-hygiene.sh`](https://github.com/egregore-labs/egregore/blob/d570d10/bin/tests/test-secret-hygiene.sh), [`bin/tests/secret-hygiene.manifest`](https://github.com/egregore-labs/egregore/blob/d570d10/bin/tests/secret-hygiene.manifest) |
| Document rule checker: one rule per file, examples that are run as tests, and a deliberately broken rule that must fail by name | [scripts/doc-check.mjs](scripts/doc-check.mjs), [scripts/doc-check.test.mjs](scripts/doc-check.test.mjs), [scripts/doc-rules/](scripts/doc-rules/), [docs/DOC-RULES.md](docs/DOC-RULES.md) | [`bin/prose-check.mjs`](https://github.com/egregore-labs/egregore/blob/d570d10/bin/prose-check.mjs), [`bin/prose-rules/`](https://github.com/egregore-labs/egregore/tree/d570d10/bin/prose-rules), [`bin/tests/test-prose-check.sh`](https://github.com/egregore-labs/egregore/blob/d570d10/bin/tests/test-prose-check.sh) |
| Handoff lifecycle rules: age alone never marks a handoff done; a 14-day expiry for FYI handoffs and for ones nobody picked up; picked-up action and feedback handoffs without evidence stay open for a person | [scripts/coordination.py](scripts/coordination.py) (`handoff_plan`), [HANDOFFS.md](HANDOFFS.md), [docs/RUNBOOK.md](docs/RUNBOOK.md), [OPERATIONS.md](OPERATIONS.md) | [`bin/handoff-lifecycle.sh`](https://github.com/egregore-labs/egregore/blob/d570d10/bin/handoff-lifecycle.sh), [`egregore_runtime/lifecycle.py`](https://github.com/egregore-labs/egregore/blob/d570d10/egregore_runtime/lifecycle.py) |
| Scan, then apply: a scan that writes nothing, and an apply that re-reads and drops a plan when the issue changed since the scan | [scripts/coordination.py](scripts/coordination.py) (`scan_issue`, `plan_is_stale`, `cmd_check_leases`), [mcp/egregore_mcp/service.py](mcp/egregore_mcp/service.py) and [worker/src/service.ts](worker/src/service.ts) (`release_task` done gate) | [`bin/handoff-lifecycle.sh`](https://github.com/egregore-labs/egregore/blob/d570d10/bin/handoff-lifecycle.sh), [`egregore_runtime/lifecycle.py`](https://github.com/egregore-labs/egregore/blob/d570d10/egregore_runtime/lifecycle.py) |

Later changes that adapt more of this source will add their files to this table.

### License text

```text
MIT License

Copyright (c) 2026 Egregore Labs

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
