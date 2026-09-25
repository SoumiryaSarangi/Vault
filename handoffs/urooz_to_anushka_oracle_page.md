# Handoff: urooz -> anushka
Date/Time: 2026-09-26, last dev hour
Status: IN PROGRESS
Topic: Oracle page (U6 UI part)
What I need changed / added: Anushka builds web/app/console/oracle/page.tsx (DESIGN §5.6) so Urooz can focus on U5 (runs API) and the pitch.
Exact files or endpoints involved: web/app/console/oracle/page.tsx, web/components/oracle/*, web/lib/oracle.ts; reads Oracle GET /runs, GET /runs/{id}, POST /runs.
Expected input / output: RunSummary / RunStatus / RunCreated from models.py (§7.5).
Why it is needed: the Oracle scene of the demo (PRD §9, 2:20).
Deadline: feature freeze.
What I will use as a stub until then: web/fixtures/oracle_runs.json.
Gated? no (agreed by Anushka and Urooz in chat)
