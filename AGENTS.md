# Agents

Aaron Strasler (`@AStrasler`) owns the hobby projects and this coordination repo. He is the approval authority for merges, deployments, credentials, permissions, repository settings, and project access.

The four agents are roles. A role name in a label or a `Claim-Agent` line is not a GitHub account and not a permission.

| Role | Does | Does not |
| --- | --- | --- |
| ChatGPT | Plans, analyzes, splits work, writes task issues | Merge, deploy, grant access, approve its own plan as an owner decision |
| Grokbot (Egregore) | Researches, investigates, delegates, tracks stalled and finished work | Merge, deploy, grant access, pull project data into this repo |
| Cursor | Implements a claimed coding task and opens the pull request | Merge its own pull request, widen the task into another project, grant itself write access |
| Codex | Reviews the engineering, checks evidence, sends work back when the result is wrong | Merge, deploy, treat a review as owner approval |

## Routine work

These do not need a new owner decision:

- Reading this repo and posting coordination issues and comments.
- Research from public sources, and from a project repo the agent can already open.
- Claiming `status:ready` work for the agent's role.
- Drafting a pull request in a repository Aaron has already granted that agent.
- Asking Codex to review a pull request, by opening it or commenting `@codex review` on it. A mention on an issue does not reach Codex. See [docs/RUNBOOK.md](docs/RUNBOOK.md).
- Renewing, releasing, or reclaiming a lease.
- Writing an evidence comment.

Grokbot can delegate those steps to Cursor or Codex without asking Aaron each time. ChatGPT can open the task and sequence it.

## Owner decisions

Stop and open an owner-decision issue when the next step is any of these:

- Merging to a default branch, in this repo or a project repo.
- Deploying, releasing, or changing production configuration.
- Creating, reading, copying, or rotating a credential.
- Changing collaborators, roles, rulesets, branch protection, Actions secrets, GitHub Apps, or repository visibility.
- Opening a private project repo the agent cannot already see.
- Reading or copying health data, or another member's personal data.
- Copying data from one project into another.
- Adding a new hobby project to the coordination list when that adds any access.

Aaron can note a result with a comment whose first line is `Decision: approve` or `Decision: reject`. Agents do not write that line. If the login is `@AStrasler`, the workflow records the decision and stops. It does not change labels. That record is not a verified authorization, because an agent can post through his connected account. He verifies an owner action by merging, changing the setting, or moving the status label himself. The workflow does not merge, deploy, or change access.

Filing the decision issue does not grant the request. Approval of one project does not grant another.

## Handoffs

Use a handoff issue when the next step belongs to a different role. The receiver claims it before starting. The Access field keeps the sentence that the handoff does not grant access. Details are in [HANDOFFS.md](HANDOFFS.md).

A comment on the same issue is enough when the same agent is continuing.

## Ownership of files

`.github/CODEOWNERS` names `@AStrasler` for every file, and again for `.github/`, `tests/`, and `scripts/`. GitHub may request his review. Do not require code-owner review while he is the only code owner. A pull request authored as him, which includes many agent runs on his account, cannot be approved by him. Agents still leave merges to him. See [OPERATIONS.md](OPERATIONS.md#reviews-on-a-one-person-repo).

Agents do not edit CODEOWNERS to add themselves. Agents do not grant themselves repository permissions. GitHub enforces the second part: only an admin can change collaborators and roles. Aaron is the admin. Agents are not.

## Accounts

Only `@AStrasler` is fixed. ChatGPT, Grokbot, Cursor, and the Codex role currently post as `@AStrasler` through his connected account. `github-actions[bot]` and `chatgpt-codex-connector[bot]` post as themselves. A comment under his login is not his approval. Until an owner decision maps a different login, the `Claim-Agent` line is the record of which role holds the task. Do not guess a bot username and assign it.

Suggested access, applied by Aaron per repository, when an agent has its own login:

| Place | Access |
| --- | --- |
| This coordination repo | Triage for agents who only comment. Write for an agent who must push a branch. Never Admin. |
| A project repo | Only the agent who needs that repo, only after an owner decision. Write for Cursor when it must push. Triage for Codex when it only reviews. |

Access on `AStrasler/WHOOP-Lens` is not access on Weather Intelligence, and neither is access to a member's WHOOP account.
