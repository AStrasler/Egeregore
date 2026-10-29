# Everyday coordination

**This repository is public.** It stores coordination information only. Do not put project data, health data, credentials, or another person's personal information here.

This is the day-to-day procedure. Labels, lease text, and the settings reference stay in [OPERATIONS.md](../OPERATIONS.md). Roles stay in [AGENTS.md](../AGENTS.md). Data rules stay in [SECURITY.md](../SECURITY.md).

Phase 2 exercised claims, leases, hygiene, and a `Decision:` line on issue #2. Issue #2 was closed on 2026-10-27T18:10:49Z with `status:done`. Phase 3 is issue #7 and pull request #8. Cursor claimed #7, and the pull request merged on 2026-10-27T17:55:54Z as `558506d`. Codex's receipt on that pull request named commit `07d5bb45f19a3acebf62c63995a59c28dd9620c6`, which was the head at review time, with no findings and a 👍. `CI / test` was green. Issue #7 was closed with the merge and, as of 2026-10-27T18:12Z, carries `status:done`. It has no owner-decision issue. [AGENTS.md](../AGENTS.md) still requires that issue before a merge to a default branch. Those issues are evidence. They are not a second copy of this guide.

## 1. Intake lifecycle

Use the forms already in `.github/ISSUE_TEMPLATE/`. Blank issues are disabled. The form's `labels:` list is what GitHub applies. On `main`, Project, Role, and Priority in the body do not become labels by themselves. The person who opens the issue adds `project:`, `priority:`, and `agent:` to match the body. The claimer adds any of those three that are missing. The companion hardening pull request teaches hygiene to copy those three fields onto labels. Until that pull request is on `main`, the copy is manual.

| Incoming work | Form | Labels at open | Who does it |
| --- | --- | --- | --- |
| Work a role can start | Task | `type:task`, `status:ready` | Role named in the body |
| Next step is a different role | Handoff | `type:handoff`, `status:ready` | The To role, after it claims |
| Milestone or completion record | Evidence | `type:evidence` | The role that did the work |
| A note that a pull request exists | Review | `type:review`, `status:needs-review` | Optional. See reviews below |
| Merge, deploy, credential, permission, setting, or new access | Owner decision | `type:decision`, `status:needs-owner`, `agent:aaron`, assignee `@AStrasler` | Aaron |

Classify before opening:

- Approval needed is `yes` only when work cannot start until Aaron acts. Open an owner decision, not a task. Hygiene adds `status:needs-owner` to a task whose Approval needed line starts with `yes`, and it removes `status:ready`.
- A coding task uses Approval needed `no` while the work is still the agent's to do. When the next step is a merge to a default branch, open the owner-decision issue in Reviews. The task is not that issue.
- Search open issues for the same project and outcome first. Section 4 is the duplicate rule.

Then:

1. Claim with the lease comment in [OPERATIONS.md](../OPERATIONS.md), including `Agent:` and `Claim-Agent`. Replace `status:ready` with `status:claimed`, set `agent:` to the role, and remove `lease:expired`. There is no `lease:active` label. Switch to `status:in-progress` when work starts.
2. Do the work in the repository that role can already open. One branch and one pull request for the task.
3. Link the pull request from a Progress comment on the task.
4. Review, then stop or finish, as below.

### Reviews

Codex reviews pull requests through `chatgpt-codex-connector[bot]`. Cursor implements the claimed task in the repository it can already push to, on one branch, and opens one pull request. The review request is a pull request comment whose body is the single line `@codex review`. Opening the pull request or marking a draft ready can also start a review. That is what happened on pull request #8. The bot reacts with 👀 while a review runs, comments when it has findings, and reacts with 👍 when the review finishes with no findings.

A pass requires both the 👍 and a review receipt whose commit is the pull request's current head SHA. On pull request #8 the receipt named `07d5bb45f19a3acebf62c63995a59c28dd9620c6`. The visible table shows the short SHA. The comment source also carries `commit_sha` and `has_unresolved_findings`. Compare that full SHA to `git rev-parse HEAD` on the pull request. A receipt for an older SHA is not a pass for a newer head. The GitHub approving-review list can stay empty. A 👍 does not merge the pull request.

The bot does not review issues. It does not treat a mention in a pull request description as a review request. Issue #2 and the first description of pull request #9 both received a reply that asks for a Codex cloud environment, and #9 received no review summary from that description. There is no Codex cloud environment for this repo. Do not mention the bot on an issue. Do not put the mention in a pull request description. After one environment reply, stop.

Skip a review-request issue for a pull request in this repo. The form can record a link. An issue reply is not the review. Leave `status:needs-review` only while a pull-request review is still running, then move the task when the bot finishes.

After a 👍 whose summary names the current head and reports no unresolved findings, stop and open one owner-decision issue. Search open and closed `[Decision]` issues first, and fill the Supersedes field (`none` when nothing is replaced). Decision type is `merge`. The Request links the task and the pull request. The form sets `status:needs-owner`, `agent:aaron`, and the assignee. Set the task to `status:needs-owner` and `agent:aaron`, release the task lease, and stop. [AGENTS.md](../AGENTS.md) requires that issue before a merge to a default branch. The task label keeps lease watch from moving the task back to `status:ready`. It does not replace the decision issue. Aaron merges. Filing the issue does not grant the merge.

When Codex comments findings, Cursor pushes fixes to the same pull request and comments `@codex review` once. A later push is not in the trigger list from pull request #8. Section 4 caps how many times to ask.

### Completion

Set `status:done` and release the lease when all of these are true:

- The outcome is met, or Aaron rejected it.
- An evidence comment or an evidence issue links the result.
- For code, the pull request link is present, Codex has passed that head, and Aaron has merged. An open pull request is not done.
- For a docs or research task with no merge, the evidence link is enough once the outcome is met.

Grokbot or Aaron closes the issue after the `status:done` label is on. Closing without that label leaves the board unclear.

Set the task to `status:needs-owner` before releasing it when an owner-decision issue is open for it. Lease watch turns a released `status:claimed` or `status:in-progress` issue back to `status:ready`. It leaves `status:needs-owner` in place.

## 2. Stalled work, expired leases, and blocked handoffs

Nothing in this repo polls on a timer except lease watch. Grokbot checks the board when a session is actually running.

| What you see | What it means | What to do |
| --- | --- | --- |
| Live lease on `status:claimed` or `status:in-progress` | Not stalled | Leave it. The holder renews or releases |
| Those two statuses, lease missing, expired, released, over 24 hours, or with no known agent | Reclaimable | Read the last Progress comment. Reclaim, or leave `status:ready`. Do not open a second pull request |
| `lease:expired` beside `status:ready` | The backstop already ran | Same as a reclaim. Remove `lease:expired` when claiming |
| `status:blocked` | Waiting on something other than Aaron | One comment must name the unblocker. If it does not, ask once |
| `status:needs-owner` | Waiting on Aaron | Stop. The owner-decision issue is the ask |
| `status:needs-review` after the lease is gone | Not relabeled by lease watch on `main` | Read the pull request and follow the review rules. Issue #2 sat in this shape after an issue mention, and was closed on 2026-10-27T18:10:49Z with `status:done` |

Lease watch is `.github/workflows/lease-watch.yml`. It runs daily at 12:17 UTC and on `workflow_dispatch`. It reads open issues, skips pull requests, and relabels only `status:claimed` and `status:in-progress`. It posts one overlap warning per live lease and one expiry comment per marker. It does not claim, delete branches, or close pull requests. GitHub may delay or skip the cron. Phase 2 ran it with `workflow_dispatch`, including Actions runs 40548511242, 40548792034, and 40549228229. The scheduled cron was not part of that test. Reclaim from the comments without waiting.

Every lease-watch run prints one summary line: issues scanned, active, expired, and conflicts warned. That line has been on `main` since pull request #8 merged. A run that only sees active leases still prints the line. The companion hardening pull request adds a run class (`active`, `expired`, `conflict`, `no-op`, or `stall`) and one stall flag for a `status:needs-review` issue, a handoff, or a `status:needs-owner` issue with no newer comment for 3 days. The flag is one marker comment, plus `stall:flagged` when that label exists. It does not repeat for the same idle stretch, and it does not claim the issue. Until that pull request is on `main`, lease watch does not flag those idle issues.

Lease watch scans first and applies second. The scan reads every open issue and its thread and writes nothing. It decides each write from that one read. Before an issue's writes, the apply pass reads the issue and its thread again. If the labels, state, update time, comment count, or any comment id changed since the scan, it drops that issue's plan, prints `issue N: changed since the scan, plan dropped`, and counts it under `plans stale`. The next run plans again. A dropped plan is not an error and does not fail the run.

The summary line now also ends with `handoffs expired`, `handoffs held`, and `plans stale`. A run with only a handoff expiry has the class `handoff`, below `stall` and above `active`.

Handoff lifecycle, from the same 14-day rule the egregore handoff tooling uses:

- Age alone never marks anything done. Lease watch never sets `status:done`.
- A handoff with no newer comment for 14 days expires when its intent is `fyi`, or when nobody ever picked it up (no comment except this workflow's own notes). Expiry posts one `egregore-handoff-expiry` comment per idle stretch. By default that is the whole change and the issue stays open. Set `EGREGORE_HANDOFF_EXPIRY=close` in the workflow to also close it as not planned.
- A handoff someone picked up (a claim or any comment) with intent `action` or `feedback` and no `## Evidence` comment stays open for a person, however old. Lease watch counts it under `handoffs held` and writes nothing for it.
- A handoff with an evidence comment is left alone. A person closes it.
- A live lease is never expired.

Dispatch lease watch when a claimed or in-progress label is still up after the lease ended and the next reader would take the wrong task. Reclaiming does not need the relabel. Do not dispatch on a timer.

A blocked handoff stays blocked until the comment's unblocker happens. If the missing piece is repository access, a credential, or a Codex cloud environment, open one owner-decision issue and set the task to `status:needs-owner`. Do not leave it on `status:needs-review`.

Unresolved Codex findings are the bot's review comments on the pull request whose summary still has `has_unresolved_findings` for the current head. A 👍 on an older commit does not clear a newer head. The summary names the commit it reviewed.

## 3. What runs without Aaron's laptop

These run on GitHub's servers. After the trigger in the table, they finish with no laptop awake.

| Runner | When it runs |
| --- | --- |
| `CI` | Pull request activity, and pushes to `main`. The job name is `test`. The ruleset requires that check |
| Issue hygiene | An issue is opened or edited. A separate job runs when a comment starts with `Decision:` |
| Lease watch cron | Daily at 12:17 UTC, subject to GitHub's schedule delay. The cron itself was not observed in Phase 2 |
| Codex pull-request review | The Codex GitHub app, when a pull request is opened, a draft is marked ready, or a pull request comment is exactly `@codex review`. Pull request #8 showed the open trigger. The app does not review issue comments, and a mention in the pull request description does not start a review |

Cursor cloud agents and Grokbot run in the cloud once a session has been started. This repo does not start them. ChatGPT runs when someone is in a ChatGPT session. This repo does not start that either.

A person, or an agent that was prompted, still has to open tasks, claim them, code, comment `@codex review` after a fix, notice stuck `status:needs-review` and `status:blocked` issues, and dispatch lease watch early. Nothing in this repo starts a Cursor cloud agent. An owner-configured Cursor automation would be that trigger, and it is not set up. Aaron still merges, deploys, changes settings, and grants access. He can do that from any device.

Creating a Codex cloud environment is his action on his Codex account. Pull-request review does not need that environment. Issue mentions do.

## 4. Duplicates, noise, review loops, and known limitations

### Duplicates

Before opening an issue, search this repo's open issues for the same project and the same outcome. If one exists, comment on it. Use the existing `duplicate` label when closing a second copy. Before opening a pull request, look for an open pull request on the same task and push to that branch. On `main`, hygiene does not compare titles. The companion hardening pull request makes hygiene warn when a new task title closely matches an open task, in the existing hygiene comment, once per wording.

One owner-decision issue per request. Before opening one, search open `type:decision` issues for the same pull request or the same access ask. A second decision issue for that request is a duplicate. Set the task to `status:needs-owner` when you open the decision issue. The task label does not replace it.

### Noise

- One Progress comment when the state changes. Commit-by-commit comments are noise.
- Do not mention `@codex` on an issue or in a pull request description. Pull request #9's description did, and the bot answered with the cloud-environment reply. The owner-decision form assigns `@AStrasler`. That assignment is the notification. Do not also mention him on the pull request.
- The owner-decision form assigns `@AStrasler`. Other forms leave assignees empty. Do not add assignees.
- Hygiene, lease, and decision notes are keyed so the workflow updates or skips. Do not reply to `github-actions[bot]`, and do not treat its comment as a claim.
- Do not dispatch lease watch because the last run was quiet.

### Review loops

Stop when the review receipt names the current head SHA, Codex has reacted 👍, and the summary reports no unresolved findings.

Otherwise allow three completed Codex reviews on that pull request. A round is one completed review of a new head. Commenting `@codex review` again on the same commit is not a new round.

After a third completed review that still has findings, stop. Open one owner-decision issue linking the task and the pull request, set the task to `status:needs-owner`, and name the remaining findings. Aaron decides whether to continue, narrow the task, or close it. Do not post a fourth `@codex review`.

### Known limitations

Check this list before opening a defect task or an owner decision about how the system behaves. If the item is listed, use the workaround and stop.

1. ChatGPT, Grokbot, Cursor, and the Codex role post as `@AStrasler`. GitHub cannot tell those posts from Aaron. Issue #2 recorded a `Decision: approve` written through his connected account. The workflow stored a note and changed no labels. A comment is not his approval.
2. The cloud-environment reply is not a review. Issue #2 and pull request #9 both received it, and #9 received no review summary. Pull request #8 received a 👍 without that reply. Do not file the missing environment again, and do not mention the bot a second time to force the review.
3. Lease watch on `main` does not relabel `status:needs-review`, `status:blocked`, or a stale `agent:` label. Move the label by hand. Issue #2 was the leftover and is now closed with `status:done`.
4. Issue comments are not a lock. Two claims can land before either is visible. The earlier valid lease holds. Simultaneous claims were not part of the Phase 2 live run.
5. The daily cron was not observed in Phase 2. `workflow_dispatch` was. Cron can be late. Reclaim from comments.
6. Pull request #8 is merged. Lease watch prints a one-line summary on every run, including an all-active run. A needs-owner hygiene note no longer inserts a stray blank line where a problem list would be. Do not file those again.
7. Hygiene looks for common credential shapes. It does not detect health data, and it does not echo a secret.
8. A `Decision:` comment never changes labels. Hygiene adds `status:needs-owner` for a decision issue, an Approval needed line that starts with `yes`, or a credential-like string. It does not remove `status:needs-owner`.
9. The active ruleset requires check context `test` and zero approving reviews. It does not require the branch to be up to date with `main`. Updating the branch for its own sake adds review rounds.
10. `require_extra_approval_for_unattributed_changes` is on. GitHub's ruleset docs say that extra approval applies when Copilot opens a pull request that is not attributed to a person, and that it has no effect when the required approval count is zero. This ruleset requires zero. Pull request #8 was mergeable with no approving review. Do not escalate the flag.
11. Web forms enforce fields in the web UI. An issue opened through the API can omit them. Hygiene comments and does not close the issue.
12. The data-boundary checkbox is a backstop. A writer can still paste a secret. This repo stays public until Aaron changes visibility, and history stays public even after that.

## 5. Identity

Every coordination comment starts with one attribution line, except an owner-decision comment:

```text
Agent: Cursor
```

The allowed values are `Cursor`, `Codex`, `Grokbot`, `ChatGPT`, and `Aaron`. Lease comments also keep `Claim-Agent`, which the workflow reads. The `Agent` line is the role. The workflow does not check it.

An owner-decision comment is the exception. Its first line stays `Decision: approve` or `Decision: reject`. The decision job runs only when the body starts with `Decision:`, and `decision_verdict()` reads that first line. Put the attribution on the next line. Agents do not write this comment.

```text
Decision: approve
Agent: Aaron
```

GitHub shows `@AStrasler` as the author of those comments. `github-actions[bot]` and `chatgpt-codex-connector[bot]` post as themselves. An `Agent` line, a `Claim-Agent` line, and a comment authored as `@AStrasler` are not owner authorization. Aaron authorizes a merge, a setting change, or a label change by doing that action himself.

A separate GitHub login or GitHub App for a role is an owner decision. None is in place.

## 6. Autonomy

All four roles currently post as `@AStrasler`. `github-actions[bot]` and `chatgpt-codex-connector[bot]` post as themselves. A comment, a `Decision:` line, a 👍, or a session that can push because it is signed in as `@AStrasler` is not proof that Aaron approved. He verifies an owner action by merging, changing the setting, or moving the status label in a session he is driving.

| Action | Who may do it |
| --- | --- |
| Read this repo. Open and comment on coordination issues. Search public sources | The agent doing the task |
| Claim, renew, release, or reclaim a lease. Post Progress or Evidence. Change status labels on the issue it holds | The agent in that role |
| Research or open a pull request in a project repo Aaron has already granted | The agent that already has that access |
| Ask Codex to review by opening a pull request or commenting `@codex review` on it | Cursor, or the role that opened the pull request |
| Ask Aaron to merge: open one owner-decision issue that links the task and the pull request, and set the task to `status:needs-owner` | The agent that holds the task |
| Run lease watch with `workflow_dispatch` | An agent that can start Actions. This does not claim the issue |
| Merge to a default branch, in this repo or a project repo | Aaron |
| Deploy, release, or change production configuration | Aaron |
| Create, read, copy, or rotate a credential. Add an Actions secret | Aaron |
| Change collaborators, roles, rulesets, branch protection, Actions permissions, GitHub Apps, or visibility | Aaron |
| Grant access to a project repo, or open a private repo the agent cannot already see | Aaron |
| Run `scripts/create-labels.sh` | Aaron, from a clone of `main`. An agent runs it only after Aaron has verified that approval himself |
| Copy health data, another member's personal data, or data from one project into another | Nobody through this repo. Stop and ask Aaron if a task cannot be finished without it |

Filing an owner decision does not grant it. Approval of one project does not grant another. Agents do not write `Decision: approve` or `Decision: reject`.

## 7. Failure handling

- Hygiene posts a comment. It does not close the issue or block it. Edit the issue to update that comment.
- A `Decision:` comment records a note and does not change labels. A recorded approve is not a merge.
- If `CI / test` fails, the `Protect main` ruleset blocks the merge. Fix the branch. Do not force-push `main`. The ruleset also blocks deleting the default branch and has no bypass actors. Required approving reviews stay at 0.
- If lease watch does not relabel an expired claim, reclaim from the comments. The summary line is the audit of the last run. A missing run means the cron has not fired or Actions did not start. Dispatch once, then stop.
- If the Codex reply asks for a cloud environment, that attempt did not review the head. Do not send a second mention.
- If a label update warns that the label is missing, stop and ask Aaron to run `scripts/create-labels.sh`. Do not invent a label. `lease:active` is not in the catalog. The catalog lease label is `lease:expired`.
- On `main`, closing a task does not by itself set `status:done`. Set that label before closing. The companion hardening pull request makes hygiene add `status:done` and remove the other `status:` labels when a task issue is closed. `EGREGORE_CLOSED_STATUS=flag` leaves the labels and records the gap in the hygiene comment instead. That switch is not on `main` yet.
- A handoff that names a repository the receiver cannot open waits on an owner-decision issue. Do not copy data into this repo to get around the missing grant.

## 8. WHOOP Lens and Weather Intelligence

These intakes are optional and coordination-only. The labels `project:whoop-lens` and `project:weather-intelligence` are already on this repo. Opening an issue with one of them does not connect the project, grant a clone, or add a collaborator.

File the task here with that `project:` label. Put a normal GitHub link in Context when a public issue or pull request exists. Do the work in the project repo only when that agent can already open it. This coordination issue does not authorize opening, cloning, or changing WHOOP-Lens or Weather Intelligence. A missing grant stops the task. It does not get solved by pasting data here.

Leave health data, WHOOP tokens, sleep or heart-rate exports, screenshots of a member's data, API keys, location history, and saved places out of this public repo. [ONBOARDING.md](../ONBOARDING.md) is the worked example. [SECURITY.md](../SECURITY.md) is the boundary.

## 9. Optional next steps

Ranked by practical value for regular use of this repo. Costs are Aaron's time and GitHub's own product, not a new service in this repository. None of these are required to keep using the loop above.

| Rank | Change | Value | Cost |
| --- | --- | --- | --- |
| 1 | Give writing agents their own GitHub logins, and stop posting as `@AStrasler` | A comment from his login can mean him. This is the gap Phase 2 measured | Aaron creates each login, maps it in an owner decision, and grants Triage or Write here. Project repos stay a separate grant. Cursor and Grokbot have to actually use those logins. No repo code |
| 2 | After a second writer exists, require one approving review from an account that did not author the pull request | A real second pair of eyes, enforced by GitHub | One ruleset edit, only after rank 1. Turning it on while he is the only author deadlocks his own pull requests. Code-owner review stays off until a second code owner exists |
| 3 | Cursor automation that starts `status:ready` work for `agent:cursor` | Ready coding tasks move without a fresh prompt | Not implemented. Needs an owner-configured trigger. This repo's workflows cannot start it. Duplicate and spend risk. Do it after rank 1 |
| 4 | A Codex cloud environment for issue-thread review | Issue mentions could be answered | Pull-request review already works without it. Setup is on Aaron's Codex account, and those runs are billed. Low value |
| 5 | Make this repo private | Coordination titles leave the public web | His checklist in SECURITY.md. History that was public stays disclosed. Private Actions minutes come from the GitHub Pro quota cited there. The workflow does not get better |

Pull request #8 is already merged, without an owner-decision issue. Later merges still need that issue. The next optional work is a separate login for writing agents.

Skip required up-to-date branches, a new MCP server, and any always-on process. The practical gap is a separate login, and Aaron merging.
