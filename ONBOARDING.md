# Onboarding a project

Adding a project lets agents file coordination issues about it. It does not grant access to that project's repository, data, credentials, or members.

**This repository is public.** Coordination issues store status and links. They do not store project data, health data, credentials, or another person's personal information.

WHOOP Lens and Weather Intelligence are the worked examples. WHOOP Lens already has its own public repo at [AStrasler/WHOOP-Lens](https://github.com/AStrasler/WHOOP-Lens). Weather Intelligence stays in its own repository. Neither repo is a folder in this one.

## Steps

1. Confirm the project is in scope. If the answer adds repository access, a credential, or any health data, open an owner-decision issue and wait. Listing a name for coordination can be routine only when no access changes.
2. Pick a short slug: `whoop-lens`, `weather-intelligence`. The slug is only a label.
3. Aaron adds the label. An agent runs the script only after Aaron has verified that approval himself. A `Decision:` comment is only a recorded note.

   ```bash
   scripts/create-labels.sh project:whoop-lens "Coordination only. No health data in this repo."
   ```

   The catalog script already knows `project:whoop-lens` and `project:weather-intelligence`. Run it with no extra argument after merge if those two are enough.
4. Open a small pull request that adds the slug to the Project dropdown in the five files under `.github/ISSUE_TEMPLATE/`. Until that pull request is merged, file issues with Project `other` and put the slug in Project slug.
5. File tasks in **this** repo. Do the work in the **project** repo, and only if that agent can already open it. Link issues and pull requests by URL.

## WHOOP Lens

A task may say "fix the empty state on the week view" and link to an issue in `AStrasler/WHOOP-Lens`.

Leave heart-rate samples, sleep exports, screenshots of a member's data, WHOOP tokens, and other people's account details out of this repo. Access to the WHOOP-Lens GitHub repo, when Aaron has granted it, covers that git repo. A WHOOP account stays separate. Weather Intelligence stays separate.

If the task cannot be done without health data, stop, set `status:needs-owner`, and ask Aaron. He decides whether a person may use their own data inside the project repo. The data still does not come here.

## Weather Intelligence

A task may say "add a daily forecast card" and link to the project repo.

API keys, location history, and saved places stay in that project. The coordination issue names the card. It does not name the key or a person's places. An agent who can push to Weather Intelligence received that grant from Aaron on that repo. The grant ends at that repo.

## What not to share across projects

- Database rows, exports, logs that contain personal data, and `.env` files.
- A token from one project reused in another.
- A collaborator added "so the same agents can see everything." Add people per repository, for the role that needs it.
- A submodule or subtree that vendors one project inside another, or inside this coordination repo.

GitHub enforces the collaborator split. The rest is the working rule, backed by code-owner review once Aaron requires it.

## Retiring a project

Open an owner decision to stop new work. Leave old coordination issues in place when they contain no sensitive data. Do not use a visibility change or a deleted label as a substitute for rotating a credential that was pasted. If something sensitive was posted while this repo was public, rotate it and treat it as disclosed.
