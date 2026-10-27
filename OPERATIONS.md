# Operations

**This repository is public.** It stores coordination information only. Do not put project data, health data, credentials, or another person's personal information here.

Day to day steps are in [docs/RUNBOOK.md](docs/RUNBOOK.md). Aaron is involved for the decisions in [AGENTS.md](AGENTS.md). The lease is what makes an interruption recoverable: the claim expires, the notes stay, and the next session continues from the last progress comment.

## Labels

One `status:` label at a time. `lease:expired` may sit beside `status:ready` until the next claim. One `agent:` label names the role for the current step. `project:` and `priority:` stay for the life of the issue.

| Label | Meaning |
| --- | --- |
| `status:ready` | Anyone in the named role may claim it |
| `status:claimed` | A lease is held and work has not started |
| `status:in-progress` | The lease holder is doing the work |
| `status:blocked` | Waiting on something other than Aaron |
| `status:needs-review` | Waiting on Codex or another review |
| `status:needs-owner` | Waiting on Aaron |
| `status:done` | Outcome recorded |
| `lease:expired` | A claim lapsed. The issue can be reclaimed |
| `agent:chatgpt` `agent:grokbot` `agent:cursor` `agent:codex` `agent:aaron` | Current role |
| `type:task` `type:handoff` `type:evidence` `type:review` `type:decision` | Issue form |
| `priority:low` `priority:normal` `priority:high` `priority:urgent` | Ordering hint. Urgent does not skip owner approval |
| `project:coordination` `project:whoop-lens` `project:weather-intelligence` | Coordination topic only |

GitHub will ignore a label named in a form until the label exists. The default `bug` and `enhancement` labels can stay. Status still comes from `status:*`.

Create the catalog from a clone of this repo after merge:

```bash
scripts/create-labels.sh
```

The script refuses to edit any repo other than `AStrasler/Egeregore`. It creates labels only. Aaron runs it, or an agent runs it after Aaron says so in a decision comment. Adding `project:whoop-lens` does not grant access to the WHOOP-Lens repo.

A later project label:

```bash
scripts/create-labels.sh project:my-slug "Coordination only. No project data in this repo."
```

## Claims

Default lease: **6 hours**. Maximum: **24 hours** from the comment time. A longer line is ignored by the lease check. Renew by posting a new expiry. It does not stack on top of the old one.

Claim, and replace `status:ready` with `status:claimed`. Remove `lease:expired` if it is present. Set the `agent:` label.

```text
## Claim
Claim-Agent: grokbot
Claimed-At: 2026-10-27T14:00:00Z
Lease-Expires: 2026-10-27T20:00:00Z
Task: Summarize the open coordination tasks
```

When work starts, switch the status to `status:in-progress`. The same lease keeps running.

Renew before the expiry if the work is still yours:

```text
## Lease renewal
Claim-Agent: grokbot
Lease-Expires: 2026-10-28T02:00:00Z
Note: Still reading the task thread
```

Release when you stop, finish, or block:

```text
## Lease release
Claim-Agent: grokbot
Lease-Release: grokbot
Reason: pausing
```

Then set the status to `ready`, `blocked`, `needs-review`, `needs-owner`, or `done`. A release with the label left on `claimed` is repaired by the daily workflow, which is a backstop. The next agent may reclaim from the comments as soon as the lease is expired or released.

`Claim-Agent` is one of `chatgpt`, `grokbot`, `cursor`, `codex`, `aaron`. Put it above `Lease-Expires` in the same comment. The earliest live claim holds. A later claim from a different agent does not replace it while that lease is unexpired. The same agent renews by posting a new `Lease-Expires`. A renewal that omits `Claim-Agent` keeps the accepted holder, including after a rejected overlapping claim. A `Lease-Expires` value that is not a real calendar time is ignored, and the rest of the thread is still read. After the holder releases, or after that `Lease-Expires` has passed, the next valid claim takes the task. A release from anyone else leaves the holder in place. An overlap warning belongs to the accepted claim that holds the lease. Renewals of that claim do not post it again. After the holder releases or the lease ends, a later claim is a different lease: it does not inherit the old warning, and a new overlap on it is warned even when an older warning reads the same.

Mid-task progress is a comment on the task issue:

```text
## Progress
Claim-Agent: cursor
Done: Opened the branch and linked it below.
Next: Add the empty-state test.
Data-Boundary: coordination-only
```

## Interruptions

1. Before a risky step or the end of a session, post Progress. If the session dies first, the next reader uses the last comment and any linked branch.
2. On return, read the latest lease. If it is still yours and unexpired, continue. A new claim is optional.
3. If your lease expired and nobody posted a later claim, post a new claim and continue from the branch or pull request that already exists. Do not open a second pull request for the same task.
4. If another agent holds a valid lease, stop. Comment with the branch URL if you have one, and wait.
5. If two claims overlap, the earlier valid lease holds. The later agent steps back in a comment and does not open a second branch or pull request. The daily workflow comments when it sees that overlap. It leaves the earlier agent's status label alone while the lease is live.
6. `github-actions[bot]` never holds a lease. Ignore its backstop comment when deciding who owns the work, then read the human claim above it.

GitHub issues cannot lock a task. Two agents can read `status:ready` and both comment before either comment is visible. Comment order is arrival order at GitHub, not a transaction, and a write or triage collaborator can still change labels. The lease rule and the workflow reduce duplicate work when agents read the thread before starting. They do not prevent a race. There is no compare-and-swap on an issue.

The daily lease-watch workflow relabels `status:claimed` and `status:in-progress` when the holding lease is missing, expired, released, longer than 24 hours, or missing a known agent. It adds `lease:expired` and, unless the issue is `status:done` or `status:needs-owner`, sets `status:ready`. It does not delete branches, close pull requests, or take the task. GitHub may delay the cron run. Reclaim without waiting for it.

An issue that is `status:needs-owner` stays with Aaron when its lease ends. It does not become ready.

## Stalled work

Grokbot checks `status:claimed`, `status:in-progress`, `status:blocked`, and `lease:expired` while tracking progress.

- A live lease is not stalled.
- An expired lease is reclaimable. Read the last progress note, then either resume it or leave it `ready` for the role that should continue. Do not assign a second agent to redo finished steps.
- `status:blocked` needs a comment that says what unblocks it. If that sentence is missing, ask once. Then leave it.
- `status:needs-owner` waits for Aaron. One decision issue is enough. Do not reclaim it and do not poll him on a timer.
- `status:needs-review` stays when a lease ends. Lease watch only rewrites `status:claimed` and `status:in-progress`. A review that never starts, including an `@codex` mention on an issue, sits until someone moves the label. The working review path is the pull request. See [docs/RUNBOOK.md](docs/RUNBOOK.md).

## Completion

A task is done when all of these are true:

- The outcome in the task is met, or Aaron rejected it.
- An evidence comment or evidence issue links the result.
- For code, the pull request link is present, Codex has passed that head (a 👍 from `chatgpt-codex-connector[bot]` and a summary with no findings on that commit), and Aaron has merged. Opening the pull request is not completion. The merge is a separate owner action.
- The lease is released and the status is `status:done`.

Grokbot or Aaron closes the issue. Closing without `status:done` leaves the board unclear, so set the label first.

## Stopping

Stop the current task when any of these are true:

1. The outcome is recorded.
2. The next step is a merge, deploy, credential, permission, visibility change, or new project access.
3. The task needs health data or another member's personal data.
4. The task needs data from a different project.
5. The session is ending. Release the lease. Do not hold it across an idle gap.
6. Another agent holds a valid lease.
7. Aaron rejected the decision.
8. Continuing would require the agent to grant itself a permission.

Before stopping, post Progress or Evidence, then update the status and release the lease.

## Workflows

| Workflow | When | Token |
| --- | --- | --- |
| `CI` | Pull requests, and pushes to `main` | `contents: read` |
| `Issue hygiene` | Issue opened or edited. A separate job runs when a comment starts with `Decision:` | `contents: read`, `issues: write` |
| `Lease watch` | Daily at 12:17 UTC, or manual `workflow_dispatch` | `contents: read`, `issues: write` |

`workflow_dispatch` on lease watch reruns the flagger. Issue and lease workflows run the code on the default branch, so a pull request cannot change them until Aaron merges it. The cron is `17 12 * * *`. Phase 2 ran lease watch with `workflow_dispatch`, including Actions runs 40548511242, 40548792034, and 40549228229. The scheduled cron was not part of that test. Treat the schedule as configured, and expect GitHub to delay or skip a day. Reclaim from the comments either way. A run on `main` that only sees active leases prints nothing. Pull request #8 adds a one-line summary and is waiting for Aaron to merge.

The hygiene job comments when required fields, the data-boundary checkbox, or a credential-like string are missing or present. It does not print the issue body. It can add `status:needs-owner`. It does not remove that label.

A comment that starts with `Decision:` is a recorded decision when GitHub attributes it to `@AStrasler`, and nothing when another login writes it. The workflow posts that distinction and does not change labels. Attribution is not a verified authorization: an agent using Aaron's connected GitHub account can post as `@AStrasler`. This repository cannot tell those posts apart. Aaron verifies an owner action by merging, changing the setting, or moving the status label himself.

These workflows are sized for GitHub Pro's private-repo minute quota. See [SECURITY.md](SECURITY.md#switching-this-repository-to-private) before switching visibility. They do not assume unlimited public minutes.

## Settings Aaron applies

This repository does not apply these. They work while the repo is public and after it is private, because the account is GitHub Pro. The ruleset in item 4 is already active. Confirm it. A pull request does not recreate it.

1. Merge the coordination pull request. The agent who opened it does not merge it.
2. Run `scripts/create-labels.sh` from a clone of `main`.
3. Settings → Actions → General: allow Actions, set workflow permissions to "Read repository contents and packages permissions", leave "allow Actions to create and approve pull requests" off, add no secrets, and do not enable larger runners.
4. Keep the active ruleset `Protect main` on the default branch. As of 2026-10-27 it has no bypass actors. It requires a pull request, requires the status check context `test` (the `test` job in `CI`; the Actions UI shows `CI / test`), and blocks force pushes and branch deletion. Required approving reviews are 0. Code-owner review is off. The branch does not have to be up to date with `main` before that check counts. See [Reviews on a one-person repo](#reviews-on-a-one-person-repo) below. This file does not change the ruleset.
5. If an agent uses its own GitHub login, grant Triage or Write on this repo only. Never Admin. Grant project repos separately, after an owner decision, one repository at a time.
6. When making the repository private, follow the checklist in [SECURITY.md](SECURITY.md#switching-this-repository-to-private).

## Reviews on a one-person repo

GitHub will not let a pull request author approve their own pull request. Cursor-run pull requests on this account are often authored as `@AStrasler`. These three requirements cannot be true at the same time:

- `@AStrasler` is the only person who can approve.
- The pull request author is `@AStrasler`.
- Branch protection requires one approval, or requires a review from code owners, and the only code owner is `@AStrasler`.

His approval does not count on a pull request he opened, so the pull request cannot meet the rule. Turning on "include administrators" makes the deadlock apply to him too. Leaving administrators exempt lets him bypass, which is an emergency hatch, not a second reviewer.

Workable rule while he is the only writer and the only code owner:

- Require a pull request before merging to `main`. The `Protect main` ruleset does this, with no bypass actors.
- Require the `test` status check. A failing or missing check blocks the merge. That block does not prove Aaron personally read the pull request.
- Block force pushes and branch deletion. The same ruleset does this.
- Leave required approving reviews off, and leave "Require review from Code Owners" off. `CODEOWNERS` still names him, and GitHub may still request him. The file does not block the merge. The ruleset already matches this.
- Aaron merges after he has read the pull request. That merge is the human check. Agents do not merge. With zero required approvals, an agent authenticated as `@AStrasler` can still press the button. GitHub cannot tell his merge from that session.

If a second GitHub account later has write access, and that account is not the pull request author, he can require one approval from that account. Code-owner review still fails closed when the only code owner authored the pull request. Add another code owner before requiring code-owner review.
