---
name: no-token-like-strings
description: A string shaped like a credential is in a public document
severity: block
scan: all
show_match: false
fix: Delete it, rotate the credential if it was real, and say where it lives instead ("the GitHub App private key in the Worker secret store").
patterns:
  - '\bgh[pousr]_[A-Za-z0-9]{20,}'
  - '\bgithub_pat_[A-Za-z0-9_]{20,}'
  - '-----BEGIN [A-Z ]*PRIVATE KEY-----'
  - '\bAKIA[0-9A-Z]{16}\b'
  - '\bxox[baprs]-[A-Za-z0-9-]{10,}'
  - '\bsk-[A-Za-z0-9]{20,}'
  - '\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}'
  - '\bBearer [A-Za-z0-9._~+/=-]{20,}'
---

This repository is public. Anything in a document, a code block, or an issue form can be read by anyone and is kept in history after it is deleted. The rule matches the shape of a credential, not the word "token", so a sentence that talks about tokens is fine. A match prints no text: the log of a failed check must not repeat the secret.

Describe where a credential is kept, never its value. A placeholder such as `<token>` is fine.

## Fails

- Set the token to {{ghp}} before you run it.
- The installation token was {{ghs}} at the time.
- Use {{github-pat}} for the fine-grained login.
- Authorization: {{bearer}}
- The key starts with {{pem}} and runs for a few lines.
- Slack posted with {{slack}} in the header.
- The cloud key is {{aws}}.
- Paste the model key {{sk}} into the form.
- The session was {{jwt}} when it failed.

## Passes

- The GitHub App private key lives in the Worker secret store.
- Never paste a token into an issue.
- Send the header as Authorization: Bearer <token>.
- The prefix is ghp_ followed by random characters.
- A key written as sk-... is a model key.
