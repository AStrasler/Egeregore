# Handoffs

A handoff names the role that owns the next step. It does not grant repository access, data access, credentials, or permission to merge or deploy.

## When to open one

Open a handoff issue when a different role should do the next step. Keep going on the same issue when the same agent is continuing.

Typical path:

1. ChatGPT opens a task with the outcome, the links, and whether Aaron's approval is required.
2. Grokbot investigates if the task needs research, then either does that research or opens a handoff to the role that should act.
3. Cursor claims a coding handoff, works in the project repo it can already access, and opens one pull request. It links that pull request from the task.
4. Codex's GitHub app reviews the pull request. On a pass, open an owner-decision issue that links the task and the pull request, and set the task to `status:needs-owner`. On findings, Cursor updates the same pull request.
5. Aaron merges. Grokbot marks the task `status:done` after the merge, or after the outcome is met when no merge was required.

A pass from Codex means the engineering looks right. Aaron still merges.

## How to send one

Use the Handoff form. The receiver claims it with the lease comment in [OPERATIONS.md](OPERATIONS.md) before changing code or posting a review conclusion.

The Access field starts with: "This handoff does not grant repository access, data access, credentials, or permission to merge or deploy." Keep that sentence. Add the repo URL where the work happens, and say whether Aaron has already granted the receiving role access there. If access is missing, stop and open an owner-decision issue. The handoff waits.

## What travels with the handoff

- The outcome and the related issue link.
- The claim, the ask, and the intent (see [Handoff fields](#handoff-fields)).
- What is already done, with links a reader can open.
- The single next action.
- Branch or pull request URLs, when those exist.

Leave these out of the handoff:

- Exports, datasets, health metrics, screenshots of personal data, or credentials.
- Permission to see a repository the receiver cannot already open.
- Permission to copy anything out of the source project into a different project.

## Handoff fields

The Handoff form carries a short summary on top and the detail below it, so a receiver can decide in a few seconds whether the handoff is theirs.

| Field | Required | What goes in it |
|---|---|---|
| Claim | yes | One sentence on where things stand now. Facts only, no hopes. |
| Ask | yes | One sentence on what the receiver should do. |
| Intent | yes | `action`, `feedback`, or `fyi`. See below. |
| Done so far | yes | Facts and links the receiver can verify. |
| Decisions | no | Choices already made that the receiver should not reopen. Write each as the decision, the reason, and what follows from it. |
| Open threads | no | Questions or risks nobody has settled yet, each with a link to where it lives. |
| Next action | yes | The single next step. |
| Next steps | no | What follows the next action, in order. |

Intent says what the receiver owes back. It does not change who claims: the receiver of any handoff claims it first with the lease comment in [OPERATIONS.md](OPERATIONS.md), as [AGENTS.md](AGENTS.md) requires.

- `action`: the receiver does the next step, such as changing code.
- `feedback`: the receiver answers or reviews, and replies on the issue.
- `fyi`: no work is expected. Reading it is the whole job.

Pick the intent that is true. A handoff that needs an answer is `feedback`, not `fyi`.

Claim, Ask, and Intent are new. Issue hygiene still checks only the earlier fields, so a handoff opened without them is not flagged. Fill them in anyway.

None of these fields carries data or access. Decisions and Open threads hold coordination facts, such as why a pull request was split, not exports, metrics, or credentials. The Access field and the data boundary checkbox stay exactly as they are.

## Cross-project notes

An agent may point at a pattern in another project ("the scheduler in Weather Intelligence is a useful reference for a WHOOP Lens reminder") and link to it. The agent who implements the reminder works from the WHOOP Lens repo's own code and access. They do not copy Weather Intelligence data into WHOOP Lens, or the reverse.

If the next step needs a second project's private access, that is an owner decision for that project. The handoff waits.

## Reviews

Codex reviews the pull request. Opening it, marking a draft ready, or a pull request comment whose body is `@codex review` is what starts the review. A pass means the review receipt names that pull request's current head SHA and reports no findings. Aaron still merges. The merge still needs an owner-decision issue.

Mentioning `@codex` on an issue does not start a review. The same cloud-environment reply showed up on pull request #9, which also got no review summary. The review-request form can record a pull request. It does not summon Codex. The loop and the stop after three reviews are in [docs/RUNBOOK.md](docs/RUNBOOK.md).
