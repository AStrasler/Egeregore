---
name: no-health-readings
description: A health measurement with a value is in a public document
severity: block
scan: all
ignore_case: true
fix: Remove the reading. Say that a health metric exists if the task needs it, and keep the value in the project repo that is allowed to hold it.
patterns:
  - '\b\d{2,3}\s?bpm\b'
  - '\b(?:resting heart rate|heart rate|heart-rate|rhr|hrv|spo2|blood pressure|blood glucose|glucose)\s*(?:of|is|was|at|:|=)?\s*\d{2,3}(?:[./]\d{1,3})?\b'
  - '\b(?:recovery|sleep|readiness)\s+(?:score|performance|efficiency)\s*(?:of|is|was|at|:|=)?\s*\d{1,3}\s?%?'
  - '\bstrain\s*(?:score)?\s*[:=]\s*\d'
  - '\b\d{2,3}/\d{2,3}\s?mmhg\b'
  - '\b\d{1,2}(?:\.\d)?\s?(?:hours|hrs)\s+of\s+sleep\b'
---

This repository stores coordination only, and health data never goes into it. The words are allowed, because the documents have to say that health data is out of bounds. A value is not. The rule matches a metric name followed by a number or a unit, so "no sleep or heart-rate exports" passes and "resting heart rate 52" does not.

## Fails

- Resting heart rate: 52 on Tuesday.
- HRV was 61 after the long run.
- The tracker showed 148 bpm at the end.
- Recovery score of 83% this morning.
- Sleep performance: 71 last night.
- Strain: 14 yesterday.
- Blood pressure 118/76 mmHg at rest.
- She got 6.5 hours of sleep.

## Passes

- Do not put health data in this repository.
- Leave sleep or heart-rate exports out of every issue.
- The recovery section lists how to reclaim a stalled lease.
- Lease recovery takes about 6 hours.
- Step 3 runs after 20 minutes of review.
