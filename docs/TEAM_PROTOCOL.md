# TEAM_PROTOCOL.md

Team: Anushka (Lead / Integrator / Full-stack), Jaiveer (Python / AI-ML), Soum (Python / AI-ML), Urooz (Testing / Demo / Pitch)
Hackathon: 20 hours, offline. Goal: WIN. Time and Claude tokens are limited, so avoid rework.

This file is the law. Every teammate's Claude Code must read it at the start of every session.
Ownership of each area is finalized in `docs/MASTER_PLAN.md` and `docs/OWNERSHIP.md`.

---

## 1. Golden rules

1. **Stay in your lane.** Only edit files you own (see OWNERSHIP.md). If you need a change elsewhere, write a handoff (section 5). Do not edit it yourself.
2. **Never hallucinate.** If you are unsure about an API, library version, function signature, contract field or requirement, STOP and check the docs or the code. If it is still unclear, ask (section 7). Never invent endpoints, fields, env vars or package names.
3. **Read before you write.** Before starting any task, read: `docs/PRD.md`, `docs/ARCHITECTURE.md`, `docs/TECH_STACK.md`, `docs/DESIGN.md`, `docs/MASTER_PLAN.md`, your task file, and `docs/contracts/`.
4. **Contracts are frozen.** The API contract and shared types in `docs/contracts/` are changed only by Anushka.
5. **Small, working increments.** Commit often, push often, and never leave your branch broken for hours.
6. **Demo first.** A working end-to-end demo beats any unfinished feature. Do not add scope.
7. **Do not touch what is gated** (section 4) without Anushka's approval.

---

## 2. File naming and ownership (to minimize merge conflicts)

- Files that belong to one person start with their name: `<owner>_<purpose>.<ext>`
  Examples: `jaiveer_recommender_service.py`, `soum_embedding_utils.py`, `urooz_e2e_checkout.test.ts`, `anushka_dashboard_page.tsx`
- Each person works inside their own folder when possible: `backend/<owner>/`, `ml/<owner>/`, `tests/<owner>/`, `frontend/...` (as assigned in the master plan).
- Shared files (config, types, layout, package manifests, contracts, routers) are owned by Anushka only.
- Never reformat, rename, move or auto-fix files you do not own. Turn off "format on save" for other people's files and do not run repo-wide formatters or linters with `--fix`.
- Never run repo-wide find-and-replace.

---

## 3. Git workflow

- `main` is protected in practice. Only Anushka merges to `main`.
- Branch naming: `<owner>/<short-task>` (example: `jaiveer/recsys-endpoint`).
- Workflow: pull `main` -> branch -> work -> commit small -> push -> open PR to `main` -> tell Anushka in the group chat.
- Commit messages: `<owner>: <what changed>`.
- Before opening a PR: rebase or pull `main`, run the app or tests for your area, and confirm nothing outside your files changed (`git diff --stat`).
- Never force-push. Never rewrite shared history.
- Never commit secrets. `.env` is git-ignored and `.env.example` is maintained by Anushka.

---

## 4. Gated changes (STOP, ask Anushka, and let her know before touching)

Claude must pause and ask the user to get Anushka's approval before doing any of these:

- Editing `docs/` (PRD, architecture, tech stack, design, master plan, contracts, ownership)
- Adding, removing or upgrading dependencies (`package.json`, `requirements.txt`, lockfiles)
- Changing folder structure or moving files
- Changing the API contract, request/response shapes or shared types
- Changing the DB schema, data models or seed data format
- Auth, environment variables, secrets, API keys or deployment config
- Build config, CI/CD, `next.config`, tsconfig, Dockerfiles
- Global styles, design tokens, theme, layout shell or shared UI components
- The main routing structure or app entry points
- Anything that would affect another teammate's files
- Any decision that changes scope, features or the demo flow
- Deleting files or running destructive commands (`rm -rf`, `git reset --hard`, `git push --force`)

When gated, message Anushka with: what you want to change, why, which files, and what could break.

---

## 5. Handoff protocol (when your work needs a change in someone else's area)

1. Do NOT edit their files.
2. Create `handoffs/<from>_to_<to>_<short-topic>.md` using the template below.
3. Tell the recipient (and Anushka if it touches a contract or gated item).
4. Continue with other work, or use a mock or stub until the handoff is done.
5. The recipient marks it `DONE` and notes it in their `updates/<name>_update.md`.

Template:

```
# Handoff: <from> -> <to>
Date/Time:
Status: OPEN | IN PROGRESS | DONE
Topic:
What I need changed / added:
Exact files or endpoints involved:
Expected input / output (with example):
Why it is needed (which feature depends on it):
Deadline (hour of hackathon):
What I will use as a stub until then:
Gated? (yes/no, if yes Anushka approved: yes/no)
```

---

## 6. update.md protocol (mandatory)

- Each person keeps `updates/<name>_update.md`.
- Ask your Claude Code to update it **after every completed task or meaningful change**.
- Format for each entry:

```
## [Hour X] <task name>
- What was done:
- Files created/changed:
- Endpoints / functions / components exposed (with signatures):
- How to run / test it:
- Known issues / TODO:
- Anything other teammates must know or do:
```

- Anushka uses these files to integrate. If it is not in your update file, it does not exist for integration.

---

## 7. Doubt protocol (never guess)

Order of escalation:
1. Check the docs in `docs/` and `docs/contracts/`.
2. Check the code and the teammate's `updates/*_update.md`.
3. Ask the relevant teammate directly (they own that area).
4. Ask Anushka (decisions, scope, contracts, conflicts).

If Claude is not sure about something, it must say "I'm not sure" and ask the user. It must not fabricate an answer. Verify external library usage against the installed version or official docs before using it.

---

## 8. Token and time discipline

- Give Claude only the files it needs. Do not paste the whole repo.
- Start each new chat by having Claude read this file, its task file, and the relevant docs. Do not re-explain the project.
- Write small, specific prompts. Ask for one task at a time.
- Do not ask Claude to re-plan things already in the master plan.
- Prefer editing existing code to regenerating whole files.
- Check in with Anushka at every merge point (roughly every 2 hours).

Time gates (adjust to the real start time):
- Hour 0-2: PS selection, docs, master plan, task split, repo scaffold
- Hour 2-12: build the core flow only
- Hour 12-14: integration, all PRs merged
- **Hour 14: FEATURE FREEZE** (bug fixes only)
- Hour 14-17: testing, fixes, polish
- Hour 17-19: demo script, pitch rehearsal, backup demo video
- Hour 19-20: buffer. Do not start anything new.

---

## 9. Paste this into each teammate's CLAUDE.md

```
You are working on a 20-hour hackathon project with a 4-person team. Before doing anything in a session:
1. Read TEAM_PROTOCOL.md, docs/PRD.md, docs/ARCHITECTURE.md, docs/TECH_STACK.md, docs/DESIGN.md, docs/MASTER_PLAN.md, docs/OWNERSHIP.md, docs/contracts/ and the task file for <NAME> in tasks/.
2. Only edit files owned by <NAME>. Name owner-specific files `<name>_<purpose>`.
3. For anything listed as gated in TEAM_PROTOCOL.md section 4, stop and ask the user to get Anushka's approval first.
4. If another teammate's area needs a change, write a handoff in handoffs/ instead of editing their files.
5. Never guess or invent APIs, fields, packages or requirements. If unsure, say so and ask.
6. After each completed task, update updates/<name>_update.md in the format from TEAM_PROTOCOL.md section 6.
7. Keep changes small, commit often on branch <name>/<task>, never push to main.
```

---

## 10. Prompts for Anushka's Claude Code (use tomorrow, in this order)

**A. Master plan**
```
Read TEAM_PROTOCOL.md and docs/PRD.md, ARCHITECTURE.md, TECH_STACK.md, DESIGN.md. Create docs/MASTER_PLAN.md with: milestones by hackathon hour, the critical path, the MVP demo flow, risks and fallbacks, and the integration points. Time budget is 20 hours with roughly 15 usable. Do not add scope beyond the PRD MVP.
```

**B. Task split**
```
Using docs/MASTER_PLAN.md, split the work between Anushka (Next.js, integration), Jaiveer and Soum (Python / AI-ML), and Urooz (testing, demo, pitch, plus light coding). Rules: no two people edit the same file, work is parallelizable from hour 2 using the frozen contract in docs/contracts/, owner-specific files are prefixed with the owner's name, and shared files belong to Anushka. Create tasks/<name>_tasks.md for each person with: ordered tasks, files they own, inputs/outputs, done-criteria, stubs to use until dependencies land, and expected handoffs. Also create docs/OWNERSHIP.md and a starter handoffs/ folder. Flag every task that touches a gated item.
```

**C. Scaffold**
```
Create the directory structure from ARCHITECTURE.md and OWNERSHIP.md, add empty placeholder files with owner prefixes, .gitignore, .env.example, per-person CLAUDE.md files (from TEAM_PROTOCOL.md section 9), updates/ with a blank update file per person, then commit and push to main.
```
