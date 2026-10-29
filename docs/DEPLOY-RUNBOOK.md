# Deploy runbook: put the Egregore Worker live

Status: a procedure. Reading or merging this document deploys nothing, sets no secret, and changes no Cloudflare setting. The commands below run later, from Egregore's own box, in the order given, and each gate waits for Aaron's own words.

The design is in [cloudflare-hosting.md](cloudflare-hosting.md). The Worker's behavior is in [../worker/README.md](../worker/README.md). This file is only the release procedure.

## Rules that sit above every step

1. **Workers Free only. Never bill.** No billing, plan, subscription, payment, custom domain, route, Durable Object, KV, R2, Queue, or Logpush call, ever. If something needs a paid feature, stop and ask Aaron. Exceeding a Free limit makes requests fail. It does not charge.
2. **Nothing runs on Aaron's device.** Egregore runs every command from its own box. The tokens and the Cloudflare credential stay on the box. Aaron's part is to say yes at the gates, and to carry each token to its client (step 5).
3. **Aaron owns every release.** Steps 0 to 2 only prepare. Step 3 promotes a version and opens a public URL, so it needs Aaron's own words in chat. A comment, reaction, or label from his GitHub account is not proof. The smoke test (step 4) writes to a test issue, so the same message has to name that too.
4. **No secret in chat, in a tool result, in the repository, or in a log.** Tool output reaches a transcript. Never run `env`, `printenv`, `set`, `cat` on a secret file, `echo "$TOKEN"`, or a command with `-x`. Pass a secret by environment variable or standard input, never as an argument, because arguments show in `ps`.
5. **Stop at the first surprise.** A failed check, an unexpected deployment, a second account, or a prompt this document does not describe is a stop. Report it. Do not work around it.
6. **Use only the Egregore credential.** The box holds other Cloudflare credentials for other projects. This runbook uses `EGREGORE_CLOUDFLARE_API_TOKEN` and nothing else.

## What is being released

| Item | Value |
| --- | --- |
| Cloudflare account | The Egregore account, used only for Egregore. Its ID goes in `CLOUDFLARE_ACCOUNT_ID`. The ID is kept in Egregore's box notes and is not in this repository. |
| Worker | `egeregore` |
| Plan | Workers Free |
| Credential | The box secret `EGREGORE_CLOUDFLARE_API_TOKEN`, exported as `CLOUDFLARE_API_TOKEN` for each command |
| GitHub App | `egregore-coordinator`, ID `5503558`, installed on `AStrasler/Egeregore` only. Its private key is the box secret `EGREGORE_APP_PRIVATE_KEY` |
| Repository the Worker acts on | `AStrasler/Egeregore` (the default of `EGREGORE_REPO`, so it is not set) |

Worker settings, from `worker/src/index.ts`:

| Name | Kind | Set in this runbook | Notes |
| --- | --- | --- | --- |
| `EGREGORE_AGENT_TOKENS` | secret | yes, step 2 | The JSON array of hashed token records. Without it every tool answers `unauthenticated`. |
| `EGREGORE_APP_PRIVATE_KEY` | secret | yes, step 2 | PKCS#1 or PKCS#8 PEM. The Worker converts it at runtime. |
| `EGREGORE_APP_ID` | value | yes, step 2, as a secret | A number, not sensitive. It is set with the secrets because a plain value that is not in `wrangler.jsonc` can be dropped by the next upload, while a secret is kept. Without it every tool answers `unconfigured`. |
| `EGREGORE_APP_INSTALLATION_ID` | value | no | Optional. When unset the Worker looks the installation up, which costs one subrequest per cold start. |
| `EGREGORE_REPO` | value | no | Optional. Defaults to `AStrasler/Egeregore`. Every token's `repos` must contain it. |

`wrangler.jsonc` stays as it is: `workers_dev` false, `preview_urls` false, no route, no binding. The public URL in step 3 is a Cloudflare setting for the live Worker. It is not a repository change.

## Step 0. Prepare the box and read the current state

Nothing here changes anything. Use one shell, so the exports are gone when it ends.

```sh
set +x
cd "$(git rev-parse --show-toplevel)"           # a checkout of main at the commit being released
git rev-parse HEAD                              # record this SHA; it is the commit to release
node --version                                  # 22.6 or later
(cd worker && npm ci)                           # exact versions from the lockfile
wr() { (cd worker && npx wrangler "$@"); }      # run wrangler from worker/, where wrangler.jsonc is

export CLOUDFLARE_API_TOKEN="$EGREGORE_CLOUDFLARE_API_TOKEN"
export CLOUDFLARE_ACCOUNT_ID="<the Egregore account ID>"

# A small helper for the REST calls. The credential goes in on standard input,
# not in the argument list.
cf() {  # cf METHOD /path [json-body]
  printf 'header = "Authorization: Bearer %s"\n' "$CLOUDFLARE_API_TOKEN" |
    curl -sS --fail-with-body -K - -X "$1" -H 'Content-Type: application/json' \
      "https://api.cloudflare.com/client/v4$2" ${3:+--data "$3"}
}
```

Then read the state:

```sh
cf GET /accounts | jq '[.result[].id] | length'                        # expect 1: the token sees only this account (an authorization error is acceptable for a token that cannot list accounts)
cf GET "/accounts/$CLOUDFLARE_ACCOUNT_ID/workers/scripts/egeregore/subdomain" | jq .result
wr deployments list --name egeregore                          # what is live now
wr versions list --name egeregore --json                      # every uploaded version
cf GET "/accounts/$CLOUDFLARE_ACCOUNT_ID/workers/subdomain" | jq -r .result.subdomain   # the account's workers.dev name
```

Write down, outside the repository:

- `PREVIOUS_VERSION`: the version ID that is live now. This is the rollback target. It may be the starter "Hello world" Worker.
- `CODE_VERSION`: the newest uploaded version, from Workers Builds, for the commit in `git rev-parse HEAD`. Check its commit annotation with `wr versions view <id> --name egeregore`. If it is not the commit being released, upload one from the box, which makes a version and deploys nothing: `wr versions upload --tag "$(git rev-parse --short HEAD)"`.
- Whether `workers.dev` is `enabled: false` (expected) and whether the account has a `workers.dev` subdomain.

Stop if any of these is true:

- The token sees more than one account. Use a narrower token. The doc [cloudflare-hosting.md](cloudflare-hosting.md) says the token should be Workers Scripts edit and Account Settings read for this account only.
- The account has no `workers.dev` subdomain. Choosing a public name is Aaron's call. Ask. Do not create one.
- A command asks for a plan, a payment method, or an upgrade.

## Step 1. Generate the agent tokens

Confirm the plan with Aaron first. It decides who may do what. The default below follows the permission model in [cloudflare-hosting.md](cloudflare-hosting.md) (`read`, `write`, `review` never imply one another, and `request_review` makes Codex run):

| Agent | Permissions | Why |
| --- | --- | --- |
| `chatgpt` | `read` | Looks at the task board. Cannot change it. |
| `grokbot` | `read`, `write`, `review` | Claims and hands off work, and may ask for a review. |
| `cursor` | `read`, `write`, `review` | Opens pull requests, so it asks for the review. |
| `codex` | `read`, `write` | Works tasks. Its reviews come from the GitHub app, not this server. |

Every token is limited to `AStrasler/Egeregore` and all tools. To change the plan, pass `--plan plan.json` with `{"tokens": [{"agent": "cursor", "permissions": ["read"], "label": "..."}]}`. `aaron` is not an allowed agent name. Aaron's approvals do not travel through this server.

Make the tokens in a directory outside the repository that only the box user can open:

```sh
umask 077
SECRETS_DIR="$HOME/.egregore-secrets/$(date +%Y%m%d)-initial"      # new directory per generation
node --experimental-strip-types --no-warnings worker/scripts/provision-agent-tokens.mjs --out-dir "$SECRETS_DIR"
```

The commands in this runbook run from the repository root. The script:

- runs `generate-agent-token.mjs` once per agent;
- runs the Worker's own parser over the records and writes nothing if the Worker would reject them;
- writes `agent-tokens.private.json` (the raw tokens, mode 0600) and `agent-tokens.records.json` (hashes only, mode 0600) into a 0700 directory;
- refuses a directory inside a git work tree, refuses to overwrite, and prints no token.

The output names the agents and permissions and nothing else. If it prints anything token-shaped, treat that token as leaked and make a new set.

**About "root-only".** The box runs as an ordinary user with no root access, so the strongest protection available is owner-only: directory 0700, files 0600, outside every repository. That keeps the tokens from other users of the machine. It does not hide them from another agent that runs as the same box user and shares this disk. The Egregore GitHub App key already sits in the box environment with the same exposure. If the box ever runs as root, use a directory under `/root`.

## Step 2. Validate, then stage the secrets

This step makes a new Worker **version** that carries the secrets. It does not deploy it, so nothing is live and no public URL exists.

Validate with the Worker's own parser. It prints the agents and counts, never a token or a hash:

```sh
node --experimental-strip-types --no-warnings worker/scripts/check-agent-tokens.mjs < "$SECRETS_DIR/agent-tokens.records.json"
```

Check one raw token against the records, without printing it:

```sh
TOKEN="$(jq -r '.tokens[] | select(.agent=="cursor") | .token' "$SECRETS_DIR/agent-tokens.private.json")" \
  node --experimental-strip-types --no-warnings worker/scripts/check-agent-tokens.mjs --verify < "$SECRETS_DIR/agent-tokens.records.json" | tail -1
```

Expect `TOKEN matches a record for cursor.` The next step is only worth running if both pass.

Stage the three settings in **one** new version. `wrangler versions secret bulk` creates a version and does not deploy it. `wrangler secret put` and `wrangler secret bulk` create a version **and deploy it at once**, so do not use them:

```sh
export EGREGORE_APP_ID_VALUE=5503558
jq -n \
  --rawfile tokens "$SECRETS_DIR/agent-tokens.records.json" \
  '{
     EGREGORE_AGENT_TOKENS: ($tokens | fromjson | tojson),
     EGREGORE_APP_PRIVATE_KEY: env.EGREGORE_APP_PRIVATE_KEY,
     EGREGORE_APP_ID: env.EGREGORE_APP_ID_VALUE
   }' |
  wr versions secret bulk --name egeregore \
    --message "Stage secrets for the first release. Not deployed." \
    --tag "secrets-$(date +%Y%m%d)"
unset EGREGORE_APP_ID_VALUE
```

`jq` reads the key from the environment, so it is never an argument. Wrangler reads the JSON from standard input and prints only the names it set. The new version copies the code and settings of the newest uploaded version, so this is why step 0 checks that the newest version is the commit being released. A later `versions upload` from Workers Builds keeps the existing secrets.

Check that nothing went live:

```sh
wr deployments list --name egeregore                   # the live deployment is still PREVIOUS_VERSION
wr versions list --name egeregore                       # a new version with the secrets tag is the newest
wr versions secret list --name egeregore --latest-version   # names only: the three settings
cf GET "/accounts/$CLOUDFLARE_ACCOUNT_ID/workers/scripts/egeregore/subdomain" | jq .result   # still enabled: false
```

Record the ID of the new version as `RELEASE_VERSION`. If the live deployment changed, stop and report: something deployed without a gate.

## Gate. Aaron's explicit sign-off

Do not continue without a message from Aaron in chat that says, in his own words, to release. Ask for it with a message that lists exactly what it covers. For example:

> Ready to release the Egregore Worker, commit `<short SHA>`. This will: (1) make version `<RELEASE_VERSION>` the live version, replacing `<PREVIOUS_VERSION>`; (2) turn on `https://egeregore.<subdomain>.workers.dev`; (3) run the smoke test, which opens one throwaway issue in AStrasler/Egeregore, claims and releases it with each agent token, and closes it; (4) make no other change. Workers Free, nothing billed. Reply "release" to go ahead. To undo it, I disable the URL and re-promote `<PREVIOUS_VERSION>`.

Add "and the review check on pull #N" only if Aaron names a test pull request. Anything not listed in that message is not approved. A reply that says "ok" to something else, a GitHub comment, or a reaction is not a yes.

## Step 3. Promote and open the URL

Promote first, while no public URL exists, then open the URL. Record the time in America/Chicago.

```sh
# 1. Make the staged version live at 100%.
wr versions deploy "$RELEASE_VERSION@100%" --name egeregore --yes \
  --message "First release of the Egregore Worker, commit <short SHA>. Approved by Aaron."
wr deployments list --name egeregore            # RELEASE_VERSION at 100%

# 2. Turn on workers.dev for this Worker only. Preview URLs stay off.
cf POST "/accounts/$CLOUDFLARE_ACCOUNT_ID/workers/scripts/egeregore/subdomain" '{"enabled":true,"previews_enabled":false}' | jq .result
SUBDOMAIN="$(cf GET "/accounts/$CLOUDFLARE_ACCOUNT_ID/workers/subdomain" | jq -r .result.subdomain)"
MCP_URL="https://egeregore.$SUBDOMAIN.workers.dev/mcp"
```

Notes:

- `wrangler.jsonc` still says `workers_dev: false`. `versions upload` does not apply it, so the setting above stands. A future `wrangler deploy` would turn the URL off again. The build command stays `wrangler versions upload`, so this does not happen by accident.
- If either command fails or asks anything unexpected, go to step 6 (rollback) and report.

## Step 4. Smoke test

The test runs from the box against the live URL. It needs one throwaway issue.

**Make the test issue** with the Task form's fields, as Aaron approved in the gate: Project `coordination`, Role `any`, Priority `low`, Approval needed `no`, the data-boundary box checked, labels `type:task` and `status:ready`, title `[Task]: Deploy smoke test`, outcome "This is a smoke test of the Worker. Close it when the test ends." Link or paste nothing else. Note its number as `ISSUE`.

**Run it:**

```sh
node --experimental-strip-types --no-warnings worker/scripts/smoke-test.mjs \
  --url "$MCP_URL" \
  --tokens-file "$SECRETS_DIR/agent-tokens.private.json" \
  --issue "$ISSUE"
```

The script prints failures and skips, then `N passed, N failed, N skipped`. The exit code is 0 only when every check passes. It never prints a token. It refuses a token file that group or others can read, or one inside a git work tree.

What it checks, in order:

1. **Metadata is open.** `tools/list` works with no token and lists all 12 tools.
2. **Unauthenticated refusal.** Every tool, called with no header, a malformed header, and a wrong random token, answers `unauthenticated`. The refusal text is the same for all three, so it does not say which part failed.
3. **Per token, forbidden paths.** Every tool the token's permissions or tool list do not allow answers `forbidden`. A refused call makes no GitHub call. If a refused call succeeds, the script fails, undoes a claim it made, and tells you which comment to look for.
4. **Per token, allowed reads.** `ping` answers `pong`, and `get_tasks`, `get_task`, `get_task_history`, `get_owner_decisions` and `get_specialists` return `ok`. This also proves the GitHub App key and ID work, because the reads mint an installation token.
5. **Per token that may write.** One token at a time: `claim_task`, `post_progress`, `renew_lease`, `release_task` on `ISSUE`. The lease is released even if a step fails.
6. **Review, only if Aaron named a test pull request** (`--pull N`). One token that may review calls `request_review`, which posts a comment that makes Codex run. The Worker refuses a second request for the same head commit, so the other review tokens are skipped. Without `--pull` the live review call is not made. The refusal for tokens that lack `review` is still checked.

`handoff_task` is not run live, because it opens a new issue. A token that may not write is still checked against it, and the handoff logic is covered by the Worker's tests and by [phase5-live-test-plan.md](phase5-live-test-plan.md).

Read-only variant, which posts nothing: add `--no-write`.

**If it fails**, read the check names. The common causes:

| What failed | Likely cause |
| --- | --- |
| Every authenticated check says `unauthenticated` | The secret is not on the live version, the array was rejected (run `check-agent-tokens.mjs` again), or the file holds tokens from another generation. |
| A token's reads say `unconfigured` | `EGREGORE_APP_ID` or `EGREGORE_APP_PRIVATE_KEY` is missing from the live version. |
| A token's reads say `forbidden` | The record's `repos` does not contain `AStrasler/Egeregore`. |
| `claim_task` says `not_claimable` or `wrong_role` | The test issue is not `status:ready` with Role `any`. |
| A refused call succeeded | A record is wider than the file says. Fix the records, stage again, and re-promote. Look at the issue for a comment that should not be there. |
| A request fails outright | The URL is off, or a Cloudflare limit applies. Check `cf GET .../subdomain`. |

Any failure other than a bad test issue is a reason to run step 6, not to leave the Worker open and keep trying.

**Clean up.** Close the test issue as Egregore through the GitHub connector. The Worker has no close tool, and the claim, progress, and renewal comments stay on the closed issue. That is expected. They contain coordination text only. Confirm the issue ended in `status:ready` before it was closed, so no stale lease label is left.

Report to Aaron in chat: the commit, `RELEASE_VERSION`, `PREVIOUS_VERSION`, the URL, the pass/fail counts, and the time. The report contains no token.

## Step 5. Aaron hands each token to its client

A token has to get from the box to the client's settings without passing through chat, email, a gist, a pasted note, or a tool result. Each token is for one client. Never reuse one across clients.

| Client | Where the token goes | Notes |
| --- | --- | --- |
| Cursor, Codex, Grokbot, or any client whose MCP config is on the box | Egregore writes it into that client's config, with file mode 0600, without displaying it. The config's `Authorization` header value is `Bearer <token>`. | Egregore does this. Aaron is not involved. |
| ChatGPT's connector settings (a browser page in Aaron's account) | Aaron enters it in the connector's authentication field himself. | The token has to reach Aaron's browser. Egregore asks how Aaron wants to receive it. A window on the box desktop that Aaron can watch, which Egregore does not capture, is the intended route. If no such route exists, stop and ask. Do not paste it in chat. |

The server URL for every client is `MCP_URL`. If a client cannot send a custom `Authorization: Bearer` header (some hosted clients accept only OAuth), stop. That is the optional OAuth work (PR 6 in [cloudflare-hosting.md](cloudflare-hosting.md)), and it is a separate decision for Aaron.

After each client is set up, have it call `ping` and `get_tasks` once. Expected: `pong`, and the open task list. Do not ask a client to print its own configuration.

When every client works, keep the raw token file. It is the only copy of the tokens, because the Worker keeps only hashes. Delete it when the tokens are retired.

## Step 6. Rollback, revocation, and rotation

### Roll back (the Worker misbehaves, or a gate was wrong)

Close the door first, then restore the code:

```sh
cf POST "/accounts/$CLOUDFLARE_ACCOUNT_ID/workers/scripts/egeregore/subdomain" '{"enabled":false,"previews_enabled":false}' | jq .result
wr rollback "$PREVIOUS_VERSION" --name egeregore --message "Rollback of the first release. Requested by Aaron."
wr deployments list --name egeregore            # PREVIOUS_VERSION at 100%
```

Disabling `workers.dev` is the quick way to close the URL, and it stops all traffic. Re-promoting restores the old code. Both are safe to run twice. Tell Aaron what was rolled back and why. If the only cause was a failed smoke test of a bad test issue, fix the issue and re-run step 4. Do not roll back for it.

### Revoke one token

Remove its record, check the array, stage it, and promote. **Staging alone changes nothing. The old token works until the new version is promoted.**

```sh
jq 'map(select(.label != "cursor-20261029"))' "$SECRETS_DIR/agent-tokens.records.json" > "$SECRETS_DIR/agent-tokens.records.next.json"
node --experimental-strip-types --no-warnings worker/scripts/check-agent-tokens.mjs < "$SECRETS_DIR/agent-tokens.records.next.json"
```

Then stage as in step 2 (with the `.next.json` file) and promote as in step 3, with Aaron's sign-off for the promotion. Remove the raw token from the private file when done, and tell the client to stop using it. Labels are in `agent-tokens.private.json`.

### Rotate tokens without a gap

1. Provision a new set into a **new** directory: `provision-agent-tokens.mjs --out-dir "$HOME/.egregore-secrets/<date>-rotation" --plan plan.json`, where the plan lists only the agents being rotated.
2. Merge old and new records, so both work: `jq -s 'add' old/agent-tokens.records.json new/agent-tokens.records.json > merged.json`. Check it with `check-agent-tokens.mjs`. The limit is 32 records and 16 KiB.
3. Stage `merged.json` (step 2) and promote it (step 3, with Aaron's sign-off).
4. Switch each client to its new token (step 5). Run the smoke test with a tokens file that has the new tokens.
5. Revoke the old records with the steps above, stage, and promote with Aaron's sign-off. Delete the old directory's private file.

### Rotate the GitHub App key

Aaron makes a new key in the GitHub App settings. That is his step, in his account, and it is outside this runbook. The new key goes to the box secret `EGREGORE_APP_PRIVATE_KEY`. Then stage with the step 2 command, promote with sign-off, run the smoke test, and have Aaron delete the old key in GitHub.

### Rotate the Cloudflare credential

Aaron makes a replacement token in the Egregore Cloudflare account with only Workers Scripts edit and Account Settings read, and it replaces the box secret `EGREGORE_CLOUDFLARE_API_TOKEN`. Then he deletes the old one in Cloudflare. The token needs no billing, DNS, zone, KV, R2, or Pages permission, and this runbook never needs one.

## What this runbook never does

- Create, upgrade, or pay for anything. Touch billing or a plan.
- Use any Cloudflare credential but `EGREGORE_CLOUDFLARE_API_TOKEN`, or any other Cloudflare account, Worker, or zone.
- Add a route, custom domain, binding, Durable Object, KV namespace, queue, or R2 bucket.
- Turn on preview URLs, observability, or Logpush.
- Deploy, promote, or open the URL without Aaron's own words.
- Print, paste, commit, email, or post a token, the App key, or the Cloudflare credential.
- Give a token the agent name `aaron`, or give Aaron's approvals a path through this server.
- Merge a pull request, change a ruleset, or change a repository setting.
