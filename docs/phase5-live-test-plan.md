# Phase 5 live integration test plan

These tests run against the real repository [AStrasler/Egeregore](https://github.com/AStrasler/Egeregore) after Aaron has created the private GitHub App and installed it only on that repository. This pull request does not run them. The implementation environment has no app id, no installation id, and no private key. Do not copy a key or a token out of another session to force a run. Do not commit a key. `.gitignore` ignores `*.pem`.

Run the five tests below in order. Stop at the first failure. Later tests assume the bot can write issues in this repository and that the server stayed up, which is the check that the installation token's repository list was only `EGREGORE_REPO`.

Unit coverage for token expiry, refresh, scope refusal, and the personal-token fallback is `tests/test_github_app_auth.py`. Those tests use a local fake token endpoint. They do not need the live app. Do not install the app on a second repository to re-check that refusal. A wider list, `repository_selection: all`, or a broader permission set makes the process refuse to start, and the unit tests already cover that.

The app posts as its bot, for example `egregore[bot]`. Aaron chooses the slug. The `Agent` line is still the role. It is not Aaron's approval, and the bot is not a separate login per role.

## Preconditions

Aaron does these. An agent does not.

- Create the private GitHub App and install it only on `AStrasler/Egeregore`.
- Grant only Issues read and write, Pull requests read-only, and Metadata read-only. Do not grant Contents, Administration, Members, Workflows, or any permission that can merge or change rulesets.
- Put `EGREGORE_APP_ID` and either `EGREGORE_APP_PRIVATE_KEY` or `EGREGORE_APP_PRIVATE_KEY_PATH` in the local environment of the test process. `EGREGORE_APP_INSTALLATION_ID` is optional. Leave the private key outside this repo.
- Set `EGREGORE_REPO=AStrasler/Egeregore` and `EGREGORE_AGENT` to the role under test.
- Do not set `EGREGORE_GITHUB_TOKEN` for these tests. App mode must be the identity under test. A partial app configuration must refuse to start rather than fall back to a personal token.
- Open the throwaway issues with the GitHub forms so the data-boundary checkbox is checked. Title them so they are obvious, for example `[Task]: Phase 5 live 1`. Close them in cleanup. The server has no merge tool and no close tool.

If the process exits at startup, read the message. A repository other than `AStrasler/Egeregore`, `repository_selection: all`, or permissions other than `issues:write`, `pull_requests:read`, and `metadata:read` are a failed install, not a failed test to retry. Fix the installation before continuing. Do not grant a broader permission to get past the check.

## 1. Real GitHub writes as the bot

Needs the live app: yes.

Steps:

1. Start `python3 -m egregore_mcp` with `EGREGORE_AGENT=cursor` and the app variables. It must stay up. Record that startup succeeded. That means the token's repository list was only `AStrasler/Egeregore` and the permissions matched the ceiling.
2. As Aaron, open one `[Task]` issue: Project `coordination`, Role `any`, Priority `normal`, Approval needed `no`, data boundary checked, and an outcome that says the issue is a Phase 5 write check. Add `type:task` and `status:ready`.
3. From the server, call `claim_task` with `lease_hours` 1, then `post_progress`, then `renew_lease` with `lease_hours` 1.
4. Read the new comments on the issue in the GitHub UI or with a personal `gh` login, not by printing the installation token.

Expected:

- The process did not exit at startup.
- `claim_task` returns `ok: true` and `held: true`.
- The claim comment, the progress comment, and the renewal comment exist. Each starts with `Agent: Cursor` except that the claim and renewal also contain `Claim-Agent: cursor` and `Lease-Expires`.
- GitHub shows the comment author as the app bot. `user.login` ends with `[bot]`, `user.type` is `Bot`, and the login is not `AStrasler`.
- Labels include `status:claimed` or `status:in-progress` and `agent:cursor`. The renewal keeps the original claim comment id as the lease id.
- No comment body contains the private key or an installation token.

Cleanup:

- `release_task` to `ready` with a short reason, from the same cursor process.
- Aaron closes the issue. Do not delete the app or revoke the key.

## 2. Simultaneous claims

Needs the live app: yes.

Issue comments are not a lock. `assess_lease` in `scripts/coordination.py` keeps the earliest live claim. A later claim from another role does not take an unexpired lease. Comment order is GitHub's `created_at` order.

Steps:

1. As Aaron, open one `[Task]` issue with Role `any`, Approval needed `no`, `type:task`, and `status:ready`.
2. Start two processes against that issue, both using the app credentials. One has `EGREGORE_AGENT=cursor`. The other has `EGREGORE_AGENT=codex`.
3. Call `claim_task` on both at the same time, `lease_hours` 1.
4. After both calls return, call `get_task` from either process.

Expected:

- Both claim comments are on the issue. Each has its own `Claim-Agent` and `Lease-Expires`.
- `get_task` reports the holder as the claim with the earlier `created_at`. The later claim does not become the lease. That is earliest-wins. It does not matter which HTTP response returned first.
- If the second caller saw the first comment before posting, its result is `lease_conflict`, a conflict comment with the `egregore-lease-conflict` marker names the winner's claim comment id, and the labels stay with the winner.
- If the two reads overlapped, both results may say `held: true`, and the later label write can name the later agent. The lease holder is still the earliest claim. Labels are not the lock. The agent that does not match the earliest claim stops and does not start a second copy of the work. Record which of the two shapes happened. Either shape passes when the earliest `created_at` is the holder. A result where the later claim is the holder fails.

Cleanup:

- The earliest holder calls `release_task` to `ready`. If labels name the other agent, Aaron corrects the labels in the UI after the release comment is present. Do not edit `created_at`.
- Aaron closes the issue.

## 3. Lease expiry and recovery

Needs the live app: yes.

The shortest lease the server will post is one hour (`lease_hours` from 1 to 24). This test waits until that `Lease-Expires` timestamp. Do not edit the comment to forge an earlier expiry. The unit tests move the clock. This one does not.

Steps:

1. As Aaron, open one `[Task]` issue, Role `any`, Approval needed `no`, `type:task`, `status:ready`.
2. With `EGREGORE_AGENT=cursor`, `claim_task` with `lease_hours` 1. Record the `Lease-Expires` value.
3. Before that timestamp, `post_progress` once so the issue has a note to recover from.
4. Wait until the timestamp is in the past. Do not renew.
5. From the cursor process, call `post_progress` and `renew_lease`.
6. From a second process with `EGREGORE_AGENT=codex`, call `claim_task` with `lease_hours` 1.
7. From codex, call `post_progress` quoting the earlier note as the point to continue from.

Expected:

- Before expiry, the cursor progress comment is stored and the lease stays cursor's.
- After expiry, cursor's `post_progress` and `renew_lease` return `lease_expired` and add no comment.
- Codex's `claim_task` returns `held: true`. `get_task` shows `Claim-Agent: codex` as the active lease. The expired cursor claim remains in the history and is not the holder.
- Codex can post progress. The new work continues from the earlier note instead of starting a second copy.
- Labels can show `status:claimed` and `agent:codex`. `lease:expired` may still be present until the new claim removes it. The lease comment, not the label, is the holder.

Cleanup:

- Codex `release_task` to `ready`.
- Aaron closes the issue.

## 4. Agent handoffs

Needs the live app: yes.

A handoff names the next role. It does not grant repository access, data access, credentials, or permission to merge or deploy.

Steps:

1. As Aaron, open one `[Task]` issue, Project `coordination`, Role `any`, Approval needed `no`, `type:task`, `status:ready`. The outcome says this is a Phase 5 handoff check and no pull request is required.
2. With `EGREGORE_AGENT=cursor`, `claim_task`, then `handoff_task` to `codex` with a non-empty `done_so_far` and `next_action`.
3. Read the new issue.
4. With `EGREGORE_AGENT=cursor`, try `claim_task` on the handoff issue.
5. With `EGREGORE_AGENT=codex`, `claim_task` on the handoff issue, then `post_progress`.

Expected:

- The new issue title starts with `[Handoff]:`. Its Access field starts with "This handoff does not grant repository access, data access, credentials, or permission to merge or deploy." The work stays in `https://github.com/AStrasler/Egeregore`. The body does not grant merge or access to another repository.
- The source issue has a handoff comment with `Lease-Release: cursor`, and its status label is `status:blocked`. `get_task` on the source does not show an active cursor lease.
- Cursor's claim on the handoff returns `wrong_role` and posts no claim comment.
- Codex's claim returns `held: true`. The claim comment author is the app bot, and `Claim-Agent: codex`.
- Nothing in the handoff is a merge, a ruleset change, or a permission change.

Cleanup:

- Codex `release_task` on the handoff to `ready`.
- Aaron closes the handoff and the source issue. Leave both closed. Do not reopen the source to keep working.

## 5. Owner-approval boundaries

Needs the live app: yes.

The server does not merge, deploy, change settings, or record `Decision: approve` or `Decision: reject`. An owner gate is `status:needs-owner` or an `Approval needed` line that starts with `yes`.

Steps:

1. Confirm the running server's tool list is `get_tasks`, `get_task`, `claim_task`, `renew_lease`, `release_task`, `post_progress`, `handoff_task`, `request_review`, `get_owner_decisions`, `get_task_history`. No tool name contains `merge`, `deploy`, `permission`, or `settings`.
2. As Aaron, open one `[Task]` issue with Approval needed `yes`, `type:task`, and `status:ready`. From the cursor process, `claim_task`.
3. As Aaron, open one owner-decision issue with the decision form. From the cursor process, `claim_task`.
4. On a separate ready task that cursor already holds from a normal claim, call `post_progress` with `done` set to a line `Decision: approve`.
5. Do not call the GitHub merge API, do not edit a ruleset, and do not change the app's permissions. Aaron can read the app's permission screen and confirm it is still Issues write, Pull requests read, and Metadata read. That screen is the permission check. A merge request is not.

Expected:

- Startup already proved the token was limited to this repository and to those permissions. The process is still that process.
- The owner-gated task returns `not_claimable`. The comment count does not gain a `Claim-Agent` line. Labels stay as Aaron set them.
- The owner-decision issue returns `not_claimable`. It is not a task the bot can claim.
- The progress call returns `forbidden` and posts nothing. The server does not write `Decision: approve`.
- `get_owner_decisions` can list the decision issue. `authorization` is `recorded` or `none`, never `verified`.
- There is no successful merge, no ruleset edit, and no permission edit from this process. `request_review` is not part of this test. The app token's pull-request permission is read-only, and a failure there is not a reason to grant write.

Cleanup:

- If a normal task was claimed for the `Decision:` probe, `release_task` to `ready` before closing it.
- Aaron closes every Phase 5 issue opened for this plan.
- Leave the app installed only on `AStrasler/Egeregore`. Do not rotate the key as part of the test, and do not commit it.

## What this plan does not do

- It does not merge a pull request, deploy, change a ruleset, or change app permissions.
- It does not post `@codex review`. Mentioning that on an issue does not start a review, and the app token is not the tool to widen until a review comment succeeds.
- It does not install the app on any other repository.
- It does not treat a `Decision:` comment, a bot login, or an `Agent` line as Aaron's approval. Aaron merges by merging.
