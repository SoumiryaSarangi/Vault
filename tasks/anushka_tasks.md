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
- Decide MASTER_PLAN §9 (REFORGE 4-laptop doc).

### A2. Chaos controller I · H2 → H4 · seam I5
- **Files:** `supervisor/anushka_chaos_ctl.py`, `supervisor/anushka_app.py`
- `POST /chaos/link` (call `/_chaos/block` on both ends; `direction`), `POST /chaos/node/{pid}` (slow, freeze, corrupt, disk_full, clear → node `/_chaos/*`), `GET /chaos`, `POST /chaos/clear_all`. Each action: `POST meta /v1/incidents/fault` + `ExternalEvent` `chaos.*` with the DESIGN §5.8 copy. Kill via `/procs/{pid}/kill` also reports ground truth.
- Tolerate metadata being down (the event is lost; the action still happens).

### A3. Chaos controller II · H4 → H6
- `POST /power/cut` (all, or by label from `GET /v1/cluster`, optional `restore_after_s`), `POST /power/restore`, `POST /nodes/add` (spawn n7+ with labels, `rpc.set_addr`), `POST /cluster/reset` (kill all, wipe `data/`, start all, create default buckets from `cluster.default_buckets`, seed via `urooz_seed.seed`, target ≤ 20 s), `POST /demo/seed`, `POST /chaos/script` (execute `urooz_scripts.steps`, report fault units).
- Until Jaiveer's metadata exists, the event/ground-truth calls just log.

### A4. Brand components · H3 (20 min) · Urooz needs these
- `components/brand/VaultMark.tsx` (TECH_STACK §6.5, IntersectionObserver pause), `VaultMarkStatic.tsx` (gradient stroke), `Wordmark.tsx`.

### A5. Console shell + Overview on fixtures · H4 → H7
- `ConsoleShell`, `TopBar`/`HealthSentence`/`ModeBanner`, `NodeList`/`NodeCard`/`PhiSparkline`/`DiskBar`/`LabelGlyphs`, `EventTimeline`/`EventItem` (Explain toggle), `KpiStrip`/`KpiTile`/`MttrBar`, `ChaosDock` UI (DESIGN §5.3, §5.8, §9, §10). Load `web/fixtures/*.json` into the store while the backend is off.
- **Done when:** Overview fits 1280×720 with no scroll and every state from the fixture reads right.

### A6. Live wiring · H7 → H9 · **M3**
- `connectStream()` in `ConsoleShell`; pollers (DESIGN §8.1); chaos dock → supervisor with toasts; `ActiveFaults` chips; `ClusterScene2D` (SVG) as the first cluster view; `ConnectionBanner`; backend-off state.

### A7. Files page · H9 → H10.5
- Buckets, dropzone with progress rows (`PUT` to gateway), `FileTable`/`ProtectionBadge`/`IflChip`/`PieceMatrix`, `InspectDrawer` + `FragmentGrid` + min-cut chips (DESIGN §5.4).

### A8. Fate + Oracle pages · H10.5 → H12
- Fate: editable label matrix → `PATCH /v1/nodes/{id}/labels`, domain cards, histogram, at-risk list, advice, cluster-wide risk (DESIGN §5.5).
- Oracle: two columns, big number, counters, chaos timeline strip, samples, "Run again" warning (DESIGN §5.6). Can go to Urooz via handoff if you're behind (R8).

### A9. 3D cluster view · H12 → H13.5 · first thing to cut (R7)
- `ClusterScene` per DESIGN §7 (ring, power islands, index icosahedron, state visuals, cut/relay lines, repair particle arcs); 2D toggle.

### A10. Overlays + settings · H12 → H13.5
- `PowerCutOverlay`, `BootOverlay` ("N of N files present. 0 lost." computed, never hard-coded), `SettingsDrawer` (mode, repair speed, grace, Explain, presenter, 2D, reset), presenter mode.

### Every merge point (H4, H7, H9, H11, H13)
Merge order Jaiveer → Soum → Urooz → Anushka; `pytest`; `vault up`; milestone check from MASTER_PLAN §3; read every `updates/*_update.md`; answer open handoffs.
