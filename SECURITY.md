# Security

**This repository is public.** It stores coordination information only. Do not put project data, health data, credentials, or another person's personal information here. Making the repository private later does not erase what was already public.

Aaron applies every repository setting himself. This system does not change visibility, collaborators, roles, rulesets, branch protection, tokens, or app installations.

## Boundaries

- **Per-project isolation.** Each hobby project keeps its code and data in its own repository. This repo stores task status, links, handoffs, and decisions.
- **Health data.** No agent requests, copies, or stores a member's health information here or in another project's issue. Coordinating a WHOOP Lens task does not authorize access to anyone's WHOOP data. If a task cannot be finished without that data, stop and ask Aaron.
- **Credentials.** Agents use their own GitHub login. They do not share tokens, copy a credential into an issue, a workflow, or another project, or create a token for this system. This repo has no Actions secrets.
- **Production.** No workflow in this repo deploys. An agent does not deploy or release from a project repo.
- **Merges.** The agent who opens a pull request does not merge it. Aaron merges.
- **Permissions.** An agent does not add collaborators, raise its own role, edit rulesets, or install apps. GitHub allows those changes for an admin. Aaron is the admin.
- **Cross-project work.** Agents may mention another project by name and link. They do not copy its data. A handoff does not grant access.

## What GitHub enforces

These hold because of GitHub's product behavior or because of a file GitHub reads. They do not depend on this repository staying public.

| Control | How it is enforced |
| --- | --- |
| Workflow token scope | The `permissions:` key in each workflow is enforced for `GITHUB_TOKEN`. `CI` is `contents: read`. The other two are `contents: read` and `issues: write`. |
| No Actions secrets in this design | No workflow reads `secrets.*`. Nothing in this pull request creates a secret. GitHub does not invent one. |
| Web issue forms | After merge, `.github/ISSUE_TEMPLATE/config.yml` disables blank issues. The web UI then requires the form fields and the data-boundary checkbox. |
| Owner decision comment | The decision job runs only for a comment whose body starts with `Decision:`. It posts a recorded-decision note when the login is `AStrasler`, and a warning otherwise. It does not change labels. The login is attribution, not a verified authorization. |
| Separate repositories | A collaborator on this repo is not a collaborator on WHOOP-Lens or Weather Intelligence. GitHub enforces that split. This repo cannot widen it. |
| Agents cannot promote themselves | Changing collaborators, roles, rulesets, and visibility requires admin. Agent logins are not given admin. |

The CI test fails a pull request that adds `pull_request_target`, `workflow_run`, a broader token permission, or a larger runner. That failure **blocks the merge only after Aaron requires the `CI / test` check**. Until then it is a visible signal.

## What is convention until Aaron changes settings

| Control | Until the setting exists |
| --- | --- |
| CODEOWNERS | GitHub requests `@AStrasler`. Requiring that review blocks a pull request he authored, including many Cursor runs, because an author cannot approve their own pull request and he is the only code owner. Leave that requirement off. See OPERATIONS.md. On a private personal repo, code-owner rules are part of GitHub Pro. |
| No direct pushes to `main` | Agents avoid `main`. A push restriction limited to `@AStrasler` stops other users. An agent using his token can still push. |
| Required CI | Aaron can require the `CI / test` check. That blocks a failing pull request. It does not prove he personally reviewed it. |
| Leases | Any agent may reclaim after `Lease-Expires`. The daily workflow relabels expired claims when Actions is enabled. GitHub cron can be delayed. Agents do not wait for it. |
| One status label | Agents replace the previous status label when they change state. GitHub allows several labels at once. |
| Data boundary | The checkbox and the hygiene comment are backstops. A person with write access can still paste a secret or a health export. The hygiene job looks for common credential shapes and does not echo them. It does not detect health data. |
| Handoffs and approvals | A handoff, a Codex review, and a `Decision:` comment grant nothing. A comment from `@AStrasler` is a recorded decision. Verified authorization is Aaron performing the merge, setting change, or label change himself. GitHub cannot separate that from an agent on his account. |
| `github-actions[bot]` | The bot does not claim tasks. Agents do not treat its comments as a lease. |

## Switching this repository to private

Aaron plans to make this repository private later. He is on GitHub Pro. Nothing here depends on public visibility: workflows use standard GitHub-hosted runners, no public-only code scanning, no Pages, and no unauthenticated API. Check the following when the visibility changes.

1. Read the history and open issues first. Anything that was public should be treated as already disclosed. The rule for this repo is that history contains coordination only. If a credential was committed or pasted, rotate it. Private visibility does not un-publish it.
2. Change visibility under Settings → General. No workflow does that.
3. Confirm Actions is still enabled. Scheduled workflows stop when Actions is disabled.
4. Minutes move to the account quota. Public standard runners do not draw it down. Private standard runners do. GitHub Pro currently includes 3,000 Actions minutes per month; confirm the number on the billing page, because the quota is the account's and not this repo's to assume. These jobs use `ubuntu-latest` only, time out at five minutes, store no artifacts, and run a daily lease check plus a short job on issue open, issue edit, and comments that start with `Decision:`. They do not use larger runners, which are billed even on public repositories. If the quota runs out, lease labels stop updating until it resets. Agents keep following the lease comments.
5. Re-invite agent logins that need to participate. Logged-out read access ends. Access here still does not add them to a project repo.
6. Confirm branch protection, code-owner review, and the required CI check survived the change. On a private personal repository those controls come with GitHub Pro.
7. Confirm there are still no Actions secrets and no GitHub App installed for this system.
8. Leave GitHub Pages off. Private Pages is not part of this design.
9. Do not add project repositories as submodules.
10. Keep the same content rule after the switch. A private coordination repo still does not store health data, project data, or credentials.

Code scanning and secret scanning are not configured. Private-repository code scanning is a paid Advanced Security feature, so this system does not depend on it. The issue hygiene credential check is the in-repo backstop, and it remains a backstop.
