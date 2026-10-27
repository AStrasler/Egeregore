# Handoffs

A handoff names the role that owns the next step. It does not grant repository access, data access, credentials, or permission to merge or deploy.

## When to open one

Open a handoff issue when a different role should do the next step. Keep going on the same issue when the same agent is continuing.

Typical path:

1. ChatGPT opens a task with the outcome, the links, and whether Aaron's approval is required.
2. Grokbot investigates if the task needs research, then either does that research or opens a handoff to the role that should act.
3. Cursor claims a coding handoff, works in the project repo it can already access, and opens a pull request. It then opens a review request that links the pull request.
4. Codex claims the review. On a pass, Codex posts a review comment and an evidence note. On problems, Codex sets the coding task back to `status:ready` or `status:in-progress` and names what to change.
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

The review-request form is the handoff to Codex. Codex comments on the review issue:

```text
## Review
Claim-Agent: codex
Result: pass
Notes: Checked the diff and the test run linked above.
Data-Boundary: coordination-only
```

Use `Result: changes` when the work should go back to Cursor. Codex releases the review lease when the comment is posted. Cursor re-claims the coding task if its lease has ended.
