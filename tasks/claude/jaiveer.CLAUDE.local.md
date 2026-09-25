# Personal rules: Jaiveer
<!-- Copy this file to the repo root as CLAUDE.local.md (git-ignored). Claude Code loads it automatically. -->

You are working for **Jaiveer** on the Vault hackathon project (see CLAUDE.md for the team rules).
Control plane. You own jaiveer_* files: common/jaiveer_{phi,placement,fate}.py, metadata/jaiveer_*, brain/jaiveer_*, tests/test_jaiveer_*, tests/jaiveer/.

Every session:
1. Read docs/TEAM_PROTOCOL.md, docs/MASTER_PLAN.md, docs/OWNERSHIP.md, docs/contracts/ and tasks/jaiveer_tasks.md.
2. Only edit files owned by Jaiveer. Name new owner-specific files `jaiveer_<purpose>`.
3. For anything listed as gated in TEAM_PROTOCOL §4, stop and ask the user to get Anushka's approval first.
4. If another teammate's area needs a change, write a handoff in handoffs/ (`jaiveer_to_<name>_<topic>.md`) instead of editing their files.
5. Never guess or invent APIs, fields, packages or requirements. If unsure, say so and ask.
6. After each completed task, update updates/jaiveer_update.md in the format from TEAM_PROTOCOL §6.
7. Keep changes small, commit often on branch jaiveer/<task>, never push to main.
