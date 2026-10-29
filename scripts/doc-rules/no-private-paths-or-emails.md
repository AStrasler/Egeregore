---
name: no-private-paths-or-emails
description: A personal file path or an email address is in a public document
severity: block
scan: prose
fix: Remove it. Name the repository, the role, or the GitHub login instead.
patterns:
  - '(?:/home|/Users)/[A-Za-z0-9._-]+/'
  - '\b[A-Za-z]:\\Users\\[A-Za-z0-9._ -]+'
  - '\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b'
---

Another person's personal information stays out of this repository. A home directory shows a username and a machine layout, and an email address is personal information. A GitHub login such as @AStrasler is the public name for a person here and is fine. Links and code spans are not scanned.

## Fails

- The export is at /home/example/exports/day.csv on that machine.
- Open /Users/example/Documents/notes for the draft.
- The file sits in C:\Users\example\Desktop on the laptop.
- Ask example.person@example.com for access.

## Passes

- Ask @AStrasler for access.
- The notes live in the project repository, not here.
- The mail address is kept in the person's own settings.
- Path `/home/example/exports/day.csv` appears only inside a code span.
