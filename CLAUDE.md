# Vault: team rules for every Claude Code session

20-hour hackathon, 4 people: Anushka (lead/integrator), Jaiveer (control plane), Soum (data plane), Urooz (oracle/demo/pitch).
Your personal rules are in `CLAUDE.local.md` (copy from `tasks/claude/<name>.CLAUDE.local.md`). If it is missing,
ask the user which teammate they are before doing anything.

Before doing anything in a session:
1. Read `docs/TEAM_PROTOCOL.md`, `docs/MASTER_PLAN.md`, `docs/OWNERSHIP.md`, `docs/contracts/README.md` and
   `tasks/<name>_tasks.md`. Read only the PRD/ARCHITECTURE/TECH_STACK/DESIGN sections your task names
   (ARCHITECTURE §0 and DESIGN §0 are the reading guides).
2. Only edit files owned by your teammate (OWNERSHIP.md). Name new files `<name>_<purpose>`.
3. For anything gated (TEAM_PROTOCOL §4: docs, dependencies, folder structure, contracts/models.py/contracts.ts,
   DB schema, env/config, build config, global styles/shared components, routing, deletes), STOP and ask the
   user to get Anushka's approval first.
4. If another teammate's area needs a change, write a handoff in `handoffs/` (template in handoffs/README.md).
   Never edit their files. Use a stub or fake until it lands.
5. Never guess or invent APIs, fields, packages, env vars or requirements. If unsure, say "I'm not sure" and ask.
   The contract is `backend/vault/common/models.py` + `web/lib/contracts.ts`; if code and docs disagree, ask Anushka.
6. After each completed task, update `updates/<name>_update.md` (format in TEAM_PROTOCOL §6).
7. Small commits on branch `<name>/<task>`, message `<name>: <what>`. Never push to main. Never force-push.
   Never run repo-wide formatters, `--fix` linters or find-and-replace.

Code conventions (TECH_STACK §5): every service-to-service call goes through `vault.common.rpc` (never a new
httpx client); every cross-process payload is a model from `vault.common.models`; events only via
`vault.common.events.emit`; blocking I/O in `asyncio.to_thread`; services expose `create_app(cfg, pid)` built
with `vault.common.service.make_app`.

## Git commits

Do not add "Co-Authored-By: Claude" or any Claude/Anthropic attribution lines to commit messages.
Do not add "Generated with Claude Code" or similar lines to pull request descriptions.
Write commit messages and PR descriptions as if authored solely by me.

Commands: `python -m vault up` · `python -m vault status` · `python -m pytest -q` · `cd web && npm run dev`

## Git commits

Do not add "Co-Authored-By: Claude" or any Claude/Anthropic attribution lines to commit messages.
Do not add "Generated with Claude Code" or similar lines to pull request descriptions.
Write commit messages and PR descriptions as if authored solely by me.
add this into CLAUDE.md