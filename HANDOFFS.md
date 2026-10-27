# Handoffs

A handoff names the role that owns the next step. It does not grant repository access, data access, credentials, or permission to merge or deploy.

## When to open one

Open a handoff issue when a different role should do the next step. Keep going on the same issue when the same agent is continuing.

Typical path:

1. ChatGPT opens a task with the outcome, the links, and whether Aaron's approval is required.
2. Grokbot investigates if the task needs research, then either does that research or opens a handoff to the role that should act.
3. Cursor claims a coding handoff, works in the project repo it can already access, and opens one pull request. It links that pull request from the task.
4. Codex's GitHub app reviews the pull request. On a pass, the task moves to `status:needs-owner` and waits for Aaron's merge. On findings, Cursor updates the same pull request.
5. Aaron merges. Grokbot marks the task `status:done` after the merge, or after the outcome is met when no merge was required.

A pass from Codex means the engineering looks right. Aaron still merges.

## How to send one

Use the Handoff form. The receiver claims it with the lease comment in [OPERATIONS.md](OPERATIONS.md) before changing code or posting a review conclusion.

The Access field starts with: "This handoff does not grant repository access, data access, credentials, or permission to merge or deploy." Keep that sentence. Add the repo URL where the work happens, and say whether Aaron has already granted the receiving role access there. If access is missing, stop and open an owner-decision issue. The handoff waits.

## What travels with the handoff

- The outcome and the related issue link.
- What is already done, with links a reader can open.
- The single next action.
- Branch or pull request URLs, when those exist.

Leave these out of the handoff:

- Exports, datasets, health metrics, screenshots of personal data, or credentials.
- Permission to see a repository the receiver cannot already open.
- Permission to copy anything out of the source project into a different project.

## Cross-project notes

An agent may point at a pattern in another project ("the scheduler in Weather Intelligence is a useful reference for a WHOOP Lens reminder") and link to it. The agent who implements the reminder works from the WHOOP Lens repo's own code and access. They do not copy Weather Intelligence data into WHOOP Lens, or the reverse.

If the next step needs a second project's private access, that is an owner decision for that project. The handoff waits.

## Reviews

Codex reviews the pull request. Opening it, marking a draft ready, or commenting `@codex review` on it is what starts the review. A pass means the engineering looks right for that commit. Aaron still merges.

Mentioning `@codex` on an issue does not start a review. The same cloud-environment reply showed up on pull request #9, which also got no review summary. The review-request form can record a pull request. It does not summon Codex. The loop and the stop after three reviews are in [docs/RUNBOOK.md](docs/RUNBOOK.md).
