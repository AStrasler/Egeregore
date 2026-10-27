# Egregore

GitHub-only coordination for Aaron Strasler's hobby projects. The GitHub repository is [AStrasler/Egeregore](https://github.com/AStrasler/Egeregore). The system is called Egregore. Grokbot uses that name when it is researching, delegating, and tracking progress.

**This repository is public.** It stores coordination information only. Do not put project data, health data, credentials, or another person's personal information here. Making the repository private later does not erase what was already public.

Four agents do the work. Aaron ([@AStrasler](https://github.com/AStrasler)) owns the projects and approves merges, deployments, credentials, permissions, and project access. Routine research and coordination do not wait for a new approval at every step.

| Agent | Role |
| --- | --- |
| ChatGPT | Planning, analysis, and coordination |
| Grokbot (Egregore) | Research, investigation, delegation, and progress tracking |
| Cursor | Delegated coding |
| Codex | Technical lead, engineering review, and verification |

There is no MCP server and no Cloudflare, Vercel, or Supabase service in this system. Agents coordinate with GitHub issues, pull requests, and the docs below.

| Doc | Read it for |
| --- | --- |
| [ARCHITECTURE.md](ARCHITECTURE.md) | How the repos, issues, and workflows fit together |
| [AGENTS.md](AGENTS.md) | Roles, ownership, and what needs Aaron |
| [SECURITY.md](SECURITY.md) | Data boundaries, enforced controls, and the private-repo switch |
| [HANDOFFS.md](HANDOFFS.md) | How a task moves between agents |
| [OPERATIONS.md](OPERATIONS.md) | Labels, claims, stalls, recovery, and when to stop |
| [ONBOARDING.md](ONBOARDING.md) | How to add a project such as WHOOP Lens or Weather Intelligence |

Local check:

```bash
python3 -m unittest discover -s tests -v
```
