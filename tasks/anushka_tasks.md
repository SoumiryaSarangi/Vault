# Anushka: Lead, integrator, full-stack

You own the shared contracts and infra, the supervisor and chaos controller, the whole dashboard (except the landing page), and every merge to `main`. The scaffold already delivered A0; your coding starts with A2.

**Read:** everything in `docs/`. For each UI task, only the DESIGN §0 rows for that screen.

---

### A0. Scaffold · ✅ done (H0–2)
- Contracts: `common/models.py` (all of §7–8), `web/lib/contracts.ts`, `common/brain_api.py`, `docs/contracts/README.md`
- Shared infra: `config.py` (+ validation, `VAULT_DEMO`), `ids.py`, `hashing.py`, `log.py`, `netsim.py` (middleware + `/_chaos`), `rpc.py` (direct + one-hop relay + `forward`), `events.py` (all DESIGN §6.3 templates), `service.py` (`make_app`, `run_service`)
- CLI `python -m vault up|status|reset|seed`; supervisor `/procs` kill/start/restart; stale-child cleanup
- Every owner's placeholder with pinned signatures; stub apps that boot; `test_common.py` (9 tests green)
- Web: Next 16 + Tailwind 4 tokens, pages, `store.ts`, `sse.ts`, `api.ts`, `format.ts`, contract-validated fixtures; `npm run build` passes

### A1. Kickoff · H0–2
- Push `main`. Everyone runs README setup, `pytest`, `python -m vault up`. Send Jaiveer and Soum their START NOW prompts.
- Decide MASTER_PLAN §10 (REFORGE 4-laptop doc).

### A2. Chaos controller I · ✅ done (link, node slow/freeze/corrupt/disk_full/clear, `/chaos/corrupt` random, faults list, clear_all, kill/start reporting)
- **Files:** `supervisor/anushka_chaos_ctl.py`, `supervisor/anushka_app.py`
- `POST /chaos/link` (call `/_chaos/block` on both ends; `direction`), `POST /chaos/node/{pid}` (slow, freeze, corrupt, disk_full, clear → node `/_chaos/*`), `GET /chaos`, `POST /chaos/clear_all`. Each action: `POST meta /v1/incidents/fault` + `ExternalEvent` `chaos.*` with the DESIGN §5.8 copy. Kill via `/procs/{pid}/kill` also reports ground truth.
- Tolerate metadata being down (the event is lost; the action still happens).

### A3. Chaos controller II · ✅ done (power cut/restore + auto-restore, add machine, reset 3.8 s without seeding, seed + script endpoints wired to Urooz's code; they return 501 until U2/U3 land)
- `POST /power/cut` (all, or by label from `GET /v1/cluster`, optional `restore_after_s`), `POST /power/restore`, `POST /nodes/add` (spawn n7+ with labels, `rpc.set_addr`), `POST /cluster/reset` (kill all, wipe `data/`, start all, create default buckets from `cluster.default_buckets`, seed via `urooz_seed.seed`, target ≤ 20 s), `POST /demo/seed`, `POST /chaos/script` (execute `urooz_scripts.steps`, report fault units).
- Until Jaiveer's metadata exists, the event/ground-truth calls just log.

### A4. Brand components · ✅ done
- `VaultMark.tsx` (liquid metal, pauses offscreen / reduced motion), `VaultMarkLazy.tsx` (use this: `next/dynamic`, static fallback while loading), `VaultMarkStatic.tsx`, `Wordmark.tsx`. Rendered in Chromium with 0 console errors.

### A5. Console shell + Overview · ✅ done
- Top bar (health sentence, tabs, Sample data / Comparison mode badges), machine cards (φ sparkline, disk bar, label glyphs, relay/slow/fenced), 2D cluster view (hash ring, power-strip islands, cut cables, relay route, repair flows), timeline with Explain and in-place repair progress, 5 KPI tiles, chaos dock (all DESIGN §5.8 actions, wired to the supervisor), floating active-fault chips, toasts, E / P shortcuts.
- Fits 1280×720 with no page or list scroll (measured). Falls back to web/fixtures after 2 s when the stream isn't up.

### A6. Live wiring · H7 → H9 · **M3** · mostly done early in A5
- Done: SSE → store, pollers (procs, chaos, metrics, fate, runs) with back-off, chaos dock → supervisor, 2D cluster view, sample-data fallback.
- Left: verify against Jaiveer's real `/v1/stream` + `/v1/metrics` when J6 lands; `ConnectionBanner` ("Reconnecting to Vault…") when a live stream drops; boot/power-cut overlays are A10.

### A7. Files page · ✅ done (on sample data + live metadata; uploads need Soum's S5)
- Buckets (policy + overhead), dropzone with per-file progress rows (XHR PUT to the gateway) and "Saved on N machines" or §6.3 error copy, file table (size, protection, effective copies against each file's own target, healthy-piece dots, updated), empty state with Seed, inspect drawer (piece grid by chunk with machine initials coloured by state, minimum failure set, shared-domain warning, Download, Delete with confirm). One `/v1/inspect/objects` call for the whole table.

### A8. Fate page · H10.5 → H12
- Editable label matrix → `PATCH /v1/nodes/{id}/labels`, domain cards, histogram, at-risk list, advice, cluster-wide risk (DESIGN §5.5).
- The Oracle page moved to Urooz (U6, plan v1.1). Your part: the "Durability check" KPI tile reads her `GET /runs`.

### A9. 3D cluster view · STRETCH: start only if ahead at H12 (R7)
- `ClusterScene` per DESIGN §7 (ring, power islands, index icosahedron, state visuals, cut/relay lines, repair particle arcs); 2D toggle.

### A10. Overlays + settings · H12 → H13.5
- `PowerCutOverlay`, `BootOverlay` ("N of N files present. 0 lost." computed, never hard-coded), `SettingsDrawer` (mode, repair speed, grace, Explain, presenter, 2D, reset), presenter mode.

### Every merge point (H4, H7, H9, H11, H13)
Merge order Jaiveer → Soum → Urooz → Anushka; `pytest`; `vault up`; milestone check from MASTER_PLAN §3; read every `updates/*_update.md`; answer open handoffs.
