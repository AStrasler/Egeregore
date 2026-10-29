---
name: one-name-per-role
description: A role is spelled a second way
severity: block
scan: prose
fix: Use the recorded spelling every time. The roles are ChatGPT, Grokbot, Cursor, Codex and Aaron.
patterns:
  - '\bGrok[ -][Bb]ot\b'
  - '\bGrokBot\b'
  - '\bGROKBOT\b'
  - '\bChat[ -]GPT\b'
  - '\bChat[Gg]pt\b'
  - '\bCHATGPT\b'
  - '\bChatgpt\b'
  - '\bCodeX\b'
  - '\bCURSOR agent\b'
---

An agent role is a name that appears in the Agent line of a comment, in a label such as `agent:grokbot`, and in the prose around them. A second spelling reads as a second role, and a reader or a search then misses half the history. The recorded spellings are ChatGPT, Grokbot, Cursor, Codex and Aaron. Code spans and fenced blocks are not scanned, because labels and identifiers there are lowercase on purpose.

## Fails

- Grok Bot investigates the task first.
- Grok-bot opens the handoff.
- GrokBot posts the progress comment.
- Chat GPT reviews the plan.
- Chatgpt reviews the plan.
- CHATGPT reviews the plan.

## Passes

- Grokbot investigates the task first.
- ChatGPT reviews the plan.
- The label `agent:grokbot` marks the holder.
- Codex answers the review request, and Cursor writes the change.
- The word "grok" alone is not a role.
