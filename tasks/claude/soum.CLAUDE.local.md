# Personal rules: Soum
<!-- Copy this file to the repo root as CLAUDE.local.md (git-ignored). Claude Code loads it automatically. -->

You are working for **Soum** on the Vault hackathon project (see CLAUDE.md for the team rules).
Data plane. You own soum_* files: common/soum_ec.py, node/soum_*, gateway/soum_*, brain/soum_{reconciler,gc,rebalancer}.py, tests/test_soum_*, tests/soum/.

Every session:
1. Read docs/TEAM_PROTOCOL.md, docs/MASTER_PLAN.md, docs/OWNERSHIP.md, docs/contracts/ and tasks/soum_tasks.md.
2. Only edit files owned by Soum. Name new owner-specific files `soum_<purpose>`.
3. For anything listed as gated in TEAM_PROTOCOL §4, stop and ask the user to get Anushka's approval first.
4. If another teammate's area needs a change, write a handoff in handoffs/ (`soum_to_<name>_<topic>.md`) instead of editing their files.
5. Never guess or invent APIs, fields, packages or requirements. If unsure, say so and ask.
6. After each completed task, update updates/soum_update.md in the format from TEAM_PROTOCOL §6.
7. Keep changes small, commit often on branch soum/<task>, never push to main.
