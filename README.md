# Egregore

Egregore is the GitHub-only coordination system for Aaron Strasler's hobby projects. The GitHub repository is [AStrasler/Egeregore](https://github.com/AStrasler/Egeregore). Grokbot uses the name Egregore when it researches, delegates, and tracks progress.

Agents plan, research, code, and review by opening issues in this repository. Project code stays in each project's own repository. There is no MCP server and no Cloudflare, Vercel, or Supabase service. Coordination is GitHub issues, labels, handoffs, and pull requests.

## Who does what

Aaron ([@AStrasler](https://github.com/AStrasler)) owns the projects and this repository. He approves merges, deployments, credentials, permissions, repository settings, and project access.

| Agent | Role |
| --- | --- |
| ChatGPT | Planning, analysis, and coordination |
| Grokbot (Egregore) | Research, investigation, delegation, and progress tracking |
| Cursor | Delegated coding |
| Codex | Technical lead, engineering review, and verification |

Routine research and coordination proceed without a fresh approval at every step. A merge, a deploy, a credential, a permission change, health data, or access to a new project waits for Aaron. Role details are in [AGENTS.md](AGENTS.md).

## Public repository

**This repository is public.** It stores coordination information only. Never put project data, health data, or credentials here, and never put another person's personal information here. Making the repository private later does not erase what was already public.

A task may link to a project issue or pull request. It does not include exports, health metrics, tokens, or `.env` contents. [SECURITY.md](SECURITY.md) records which controls GitHub enforces and which ones are convention until Aaron applies branch protection. The private-repo checklist is in that same file.

## How coordination works

Work moves through GitHub issues.

1. **Open an issue** with one of the forms: task, handoff, evidence, review request, or owner decision. Blank issues are disabled on the web form after this lands on `main`.
2. **Labels** name the state and the topic. One `status:` label at a time (`ready`, `claimed`, `in-progress`, `blocked`, `needs-review`, `needs-owner`, `done`), plus `agent:`, `project:`, `priority:`, and `type:`. The catalog and the create script are in [OPERATIONS.md](OPERATIONS.md).
3. **Claim** ready work with a lease comment (`Claim-Agent` and `Lease-Expires`). The default lease is 6 hours and the maximum is 24. If a session stops, the notes stay. After the lease ends, the same agent or another agent reclaims and continues from the last progress comment.
4. **Hand off** when the next step belongs to a different role. A handoff names that role and links the task. It does not grant repository access, data access, or permission to merge. See [HANDOFFS.md](HANDOFFS.md).
5. **Review and finish.** Cursor opens the pull request in the project repo it can already access and files a review request here. Codex reviews. Aaron merges. Grokbot records evidence and sets `status:done`.

`github-actions[bot]` comments on missing fields, expired leases, and owner-decision lines. It does not claim work. A `Decision: approve` or `Decision: reject` comment counts only from `@AStrasler`.

## Docs

| Doc | Read it for |
| --- | --- |
| [ARCHITECTURE.md](ARCHITECTURE.md) | How this repo, project repos, issues, and workflows fit together |
| [AGENTS.md](AGENTS.md) | Roles, ownership, routine work, and owner decisions |
| [SECURITY.md](SECURITY.md) | Data boundaries, enforced controls versus convention, and switching to private |
| [HANDOFFS.md](HANDOFFS.md) | How a task moves between agents |
| [OPERATIONS.md](OPERATIONS.md) | Labels, claims, stalled work, recovery, and when to stop |
| [ONBOARDING.md](ONBOARDING.md) | How to add a project such as WHOOP Lens or Weather Intelligence without sharing its data |

## After merge

Aaron applies these. The pull request does not change repository settings, and the agent who opened it does not merge it. Full wording is in [OPERATIONS.md](OPERATIONS.md#settings-aaron-applies).

1. Merge the coordination pull request.
2. From a clone of `main`, run `scripts/create-labels.sh`. It creates labels only, and it refuses any repository other than `AStrasler/Egeregore`.
3. Settings → Actions → General: allow Actions, set workflow permissions to "Read repository contents and packages permissions", leave "Allow GitHub Actions to create and approve pull requests" off, add no secrets, and leave larger runners off.
4. Protect `main`: require a pull request, one approval, review from code owners, and the `CI / test` check. Block force pushes and branch deletion. Only `@AStrasler` pushes to `main`.
5. If an agent has its own GitHub login, grant Triage or Write on this repo. Admin stays with Aaron. Grant each project repository separately, after an owner decision.
6. When this repository becomes private, follow [SECURITY.md](SECURITY.md#switching-this-repository-to-private).

Local check:

```bash
python3 -m unittest discover -s tests -v
```

## License

Copyright 2026 Aaron Strasler. This project is licensed under the Apache License, Version 2.0. The full text is in [LICENSE](LICENSE).
