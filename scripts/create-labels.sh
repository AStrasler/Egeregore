#!/usr/bin/env bash
# Create or update Egregore coordination labels.
#
# This changes labels only. It does not change visibility, collaborators,
# roles, rulesets, branch protection, Actions secrets, or app installations.
# Aaron runs it from a clone of AStrasler/Egeregore after the coordination
# pull request is merged. An agent runs it only after Aaron says to.
#
# Usage:
#   scripts/create-labels.sh [--dry-run]
#   scripts/create-labels.sh [--dry-run] project:my-slug "Coordination only. No project data in this repo."
set -euo pipefail

DRY=0
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY=1
  shift
fi

if [[ $# -ne 0 && $# -ne 2 ]]; then
  echo "usage: scripts/create-labels.sh [--dry-run] [project:slug \"description\"]" >&2
  exit 2
fi

EXTRA_NAME=""
EXTRA_DESC=""
if [[ $# -eq 2 ]]; then
  EXTRA_NAME="$1"
  EXTRA_DESC="$2"
  if [[ ! "$EXTRA_NAME" =~ ^project:[a-z0-9]+(-[a-z0-9]+)*$ ]]; then
    echo "extra label must look like project:my-slug" >&2
    exit 2
  fi
  if [[ -z "$EXTRA_DESC" || ${#EXTRA_DESC} -gt 100 ]]; then
    echo "description must be 1-100 characters" >&2
    exit 2
  fi
fi

label() {
  local name="$1"
  local color="$2"
  local desc="$3"
  printf '%s\n' "$name"
  if [[ "$DRY" -eq 1 ]]; then
    return 0
  fi
  gh label create "$name" --repo "$REPO" --color "$color" --description "$desc" --force >/dev/null
}

if [[ "$DRY" -eq 0 ]]; then
  expected="AStrasler/Egeregore"
  REPO="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"
  if [[ "$REPO" != "$expected" ]]; then
    echo "Refusing to edit labels on ${REPO}. Expected ${expected}." >&2
    exit 2
  fi
fi

label "status:ready" "1A7F37" "Available to claim"
label "status:claimed" "9A6700" "Lease held; work has not started"
label "status:in-progress" "0969DA" "Lease held and work is underway"
label "status:blocked" "CF222E" "Waiting on something other than Aaron"
label "status:needs-review" "8250DF" "Waiting for Codex or another review"
label "status:needs-owner" "BC4C00" "Waiting for Aaron"
label "status:done" "57606A" "Finished and recorded"

label "lease:expired" "BD561D" "Lease lapsed while the issue looked active"

label "agent:chatgpt" "0550AE" "Planning, analysis, and coordination"
label "agent:grokbot" "0550AE" "Research, investigation, delegation, tracking"
label "agent:cursor" "0550AE" "Delegated coding"
label "agent:codex" "0550AE" "Technical lead, review, and verification"
label "agent:aaron" "0550AE" "Owner decisions and approvals"

label "type:task" "0075CA" "Task to be claimed"
label "type:handoff" "0075CA" "Handoff of the next step"
label "type:evidence" "0075CA" "Progress or completion evidence"
label "type:review" "0075CA" "Request for Codex review"
label "type:decision" "0075CA" "Decision only Aaron can make"

label "priority:low" "C5DEF5" "Low"
label "priority:normal" "FBCA04" "Normal"
label "priority:high" "D93F0B" "High"
label "priority:urgent" "B60205" "Urgent. Does not bypass owner approval."

label "project:coordination" "0E8A16" "Coordination for this repository"
label "project:whoop-lens" "0E8A16" "Coordination only. No health data in this repo."
label "project:weather-intelligence" "0E8A16" "Coordination only. No project data in this repo."

if [[ -n "$EXTRA_NAME" ]]; then
  label "$EXTRA_NAME" "0E8A16" "$EXTRA_DESC"
fi
