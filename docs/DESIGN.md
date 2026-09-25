# Vault: Design

**Status:** v1.0, frozen · **Owner:** Anushka (tokens, theme, layout shell and shared components are gated, TEAM_PROTOCOL §4) · **Date:** 2026-09-26
**Read with:** `PRD.md` (persona, demo), `ARCHITECTURE.md` §7–8 (the data every screen shows), `TECH_STACK.md` §3 and §6 (libraries and tested snippets). Motion and performance rules follow the team's `smooth-3d-websites` skill (§4.3).

## 0. Reading guide

| You are building | Read |
|---|---|
| Landing page | §1, §2, §3, §4, §5.2, §9 |
| Console shell, Overview, KPIs, chaos dock | §1, §3, §5.1, §5.3, §5.8–5.10, §6, §8, §9, §10 |
| 3D cluster view | §3.1, §3.5, §4, §7 |
| Files, Fate, Oracle pages | §3, §5.4–5.6, §6, §8 |
| Backend event copy (`common/events.py`) | §6 only |

---

## 1. Design goals

The dashboard has two audiences at once: a **clinic manager** who needs one sentence ("Is my data safe? Do I need to do anything?") and a **judge** who needs to *see* failure, detection and repair happen. Five principles:

1. **Calm when healthy, loud when not.** At rest the console is dark, quiet and mostly monochrome. Color appears only when state changes, so a judge's eye goes straight to the problem.
2. **Every number has a sentence.** No metric appears without a plain-language line beside it. Explain mode adds the technical line underneath.
3. **Wow once, then clarity.** The liquid-metal logo and shader gradient belong to the landing page and the power-restore moment. The console is fast, legible and projector-friendly.
4. **Show cause and effect.** Every chaos button produces a visible reaction within 1 second (a node dims, a cable turns red, a timeline entry appears), then the repair visibly follows.
5. **Two clicks to break anything.** Chaos actions are big buttons with a target menu. No typing on stage.

---

## 2. Brand

### 2.1 Name, tagline, voice
- **Name:** Vault. Wordmark in capitals, wide tracking: `V A U L T`.
- **Tagline:** *Storage that heals itself.*
- **Subline:** *S3-grade durability on a few ordinary machines. No cloud. No sysadmin.*
- **Voice:** calm, specific, reassuring. A good nurse, not a sysadmin. Lead with safety, then what happened, then what Vault is doing, then what (if anything) you should do. Full rules in §6.

### 2.2 Logo

The mark is a bold rounded **V**. Simple thick shapes read best through the liquid-metal shader (tested; fine detail gets lost).

`web/public/brand/vault-mark.svg` (shader input: the shape comes from the alpha channel):
```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200">
  <path d="M40 40 L100 160 L160 40" fill="none" stroke="#000" stroke-width="34"
        stroke-linecap="round" stroke-linejoin="round"/>
</svg>
```
Static version (top bar, favicon, anywhere a shader is too heavy): the same path with a vertical gradient stroke `#FFFFFF → #8A94A8`.

| Where | Treatment |
|---|---|
| Landing hero | `LiquidMetal` at 200–240 px over the shader gradient |
| Boot/recovery overlay | `LiquidMetal` at 160 px |
| Console top bar | Static SVG, 22 px, plus wordmark |
| Favicon | Static SVG on `#05060A` |

---

## 3. Design tokens

All tokens live in `web/app/globals.css` under `@theme` (Tailwind 4). Components never hard-code hex values.

### 3.1 Color

**Surfaces and text** (dark theme only; this is a control room):

| Token | Hex | Use |
|---|---|---|
| `--color-bg-0` | `#05060A` | Page |
| `--color-bg-1` | `#0A0C13` | Panel base (under glass) |
| `--color-bg-2` | `#11141F` | Raised, hover |
| `--color-bg-3` | `#181C2A` | Inputs, pressed, selected |
| `--color-line` | `#FFFFFF14` | Hairlines (8%) |
| `--color-line-strong` | `#FFFFFF29` | Focused borders (16%) |
| `--color-fg-0` | `#F2F4F8` | Primary text |
| `--color-fg-1` | `#A7AEC0` | Secondary text |
| `--color-fg-2` | `#6C7489` | Muted captions (never for important text) |
| `--color-brand` | `#B8CCFF` | Ice steel: links, focus ring, active tab |
| `--color-brand-strong` | `#7F9CFF` | Primary buttons |
| `--color-naive` | `#F97316` | Naive-mode banner and tint only |

**State colors.** Color never carries meaning alone: every state also has an icon (lucide) and a word.

| Token | Hex | Meaning | Icon | Word on card |
|---|---|---|---|---|
| `--color-ok` | `#34D399` | Healthy / safe | `CircleCheck` | Healthy |
| `--color-suspect` | `#FBBF24` | Suspicious, checking | `ScanEye` | Checking… |
| `--color-partitioned` | `#A78BFA` | Reachable only through a relay | `Route` | Via {peer} |
| `--color-down` | `#FB923C` | Not responding, within grace | `PowerOff` | Not responding {s}s |
| `--color-dead` | `#F87171` | Declared dead, its data is being rebuilt | `OctagonX` | Off · rebuilding |
| `--color-repair` | `#22D3EE` | Rebuilding or moving copies | `Wrench` | Rebuilding |
| `--color-rejoin` | `#60A5FA` | Rejoining | `RefreshCw` | Rejoining |
| `--color-fenced` | `#94A3B8` | Fenced (refuses writes) | `Lock` | Paused for safety |
| `--color-corrupt` | `#F472B6` | Damaged copy found | `FileWarning` | Damaged copy |
| (outline of suspect) | `#FBBF24` | Slow flag | `Snail` | Slow |

**Effective-copies scale:** ≥ target → `ok`; 2 → `suspect` (amber); 1 → `dead` (red). Unreadable → red with `CircleSlash`.

**Power-strip island tints** (3D and Fate page): A `#7F9CFF`, B `#34D399`, C `#FBBF24`, D `#F472B6`, E `#22D3EE`, each at 12% opacity for fills and 60% for outlines.

**Console background** (CSS only, no WebGL):
```css
background:
  radial-gradient(1200px 600px at 70% -10%, rgb(88 110 255 / 0.14), transparent 60%),
  radial-gradient(900px 500px at 0% 110%, rgb(52 211 153 / 0.06), transparent 60%),
  var(--color-bg-0);
```
In Naive mode, swap the first gradient's color to `rgb(249 115 22 / 0.14)`.

All state colors on `--color-bg-1` exceed 4.5:1 contrast.

### 3.2 Typography

Fonts via `next/font/google`: **Geist** (UI) and **Geist Mono** (numbers, IDs, technical lines). Numbers always use `tabular-nums`.

| Style | Size / line height | Weight | Font | Use |
|---|---|---|---|---|
| Display | `clamp(3rem, 7vw, 6.5rem)` / 0.95, tracking −0.04em | 600 | Geist | Landing headline |
| Lead | 20 / 30 | 400 | Geist | Landing subline |
| Page title | 28 / 34 | 600 | Geist | Console page titles |
| Health sentence | 18 / 26 | 500 | Geist | Top bar status |
| Card title | 16 / 22 | 500 | Geist | Node names, panel titles |
| Body | 14 / 20 | 400 | Geist | Timeline, tables |
| Technical | 13 / 18 | 400 | Geist Mono | Explain lines, IDs |
| Caption | 12 / 16 | 500, tracking 0.04em, uppercase | Geist | Section labels only |
| KPI value | 32 / 36 | 500 | Geist Mono | KPI tiles |
| Wordmark | 14, tracking 0.32em | 600 | Geist | Top bar |

Minimum text size is 12 px (captions only). Projector rule: anything a judge must read is ≥ 14 px, and ≥ 16 px in presenter mode.

### 3.3 Spacing, radius, layout

- Spacing scale: 4 px base (4, 8, 12, 16, 20, 24, 32, 48).
- Radius: cards and panels 16 px, controls 10 px, chips 999 px.
- Console grid at 1280×720 (the minimum; nothing on Overview may scroll at this size):
  - Top bar 56 px · main area (fills) · KPI strip 88 px · chaos dock 64 px
  - Main: left column 260 px (machines) · center fluid (3D view) · right column 340 px (timeline)
  - At ≥ 1600 px wide: right column 400 px and KPI values 40 px.

### 3.4 Glass

CSS glass for every panel that sits over the 3D view or gradient. It blurs live WebGL underneath, which real-refraction libraries can't (TECH_STACK §3.2).
```css
.glass {
  background: linear-gradient(180deg, rgb(255 255 255 / 0.06), rgb(255 255 255 / 0.02)), rgb(10 12 19 / 0.55);
  backdrop-filter: blur(18px) saturate(140%);
  -webkit-backdrop-filter: blur(18px) saturate(140%);
  border: 1px solid var(--color-line);
  box-shadow: inset 0 1px 0 rgb(255 255 255 / 0.06), 0 12px 32px rgb(0 0 0 / 0.35);
  border-radius: 16px;
}
```
Rules: at most 4 glass panels visible at once, never glass inside glass, and no glass on elements that animate their size.

### 3.5 Motion

| Token | Value | Use |
|---|---|---|
| `--ease-out` | `cubic-bezier(.16,1,.3,1)` (GSAP `expo.out`) | Entrances |
| `--ease-in-out` | `cubic-bezier(.65,0,.35,1)` | State changes |
| `--dur-fast` | 120 ms | Hover, press |
| `--dur-base` | 240 ms | State color changes, timeline item enter |
| `--dur-panel` | 400 ms | Drawers, overlays |
| `--dur-hero` | 900–1200 ms, stagger 0.05 s | Landing text reveals only |

- **Numbers:** tween to the new value over 600 ms (`power2.out`) only when the value changes. Never animate a number that's being read in a live demo for longer than 600 ms.
- **Suspect pulse:** period = `clamp(1.6 s − φ/10, 0.4 s, 1.6 s)`, so the pulse visibly speeds up as suspicion grows.
- **Timeline item enter:** `translateY(8px) → 0`, opacity 0 → 1, 240 ms.
- **Reduced motion** (`prefers-reduced-motion`): no pulses, no number tweens (instant), no Lenis, 2D cluster view, landing content static.

---

## 4. Visual libraries: where and how

### 4.1 Placement

| Library | Where | Why there |
|---|---|---|
| `LiquidMetal` (Paper shaders) | Landing hero logo; boot/recovery overlay | The brand's "wow" moment. Also the emotional beat when power comes back. |
| `ShaderGradient` | Landing hero background only | Premium first impression. Too costly behind a live dashboard. |
| React Three Fiber | Console Overview: the cluster view | The one place 3D explains something: machines, power islands, cut cables, repair flows. |
| GSAP + ScrollTrigger | Landing reveals and scroll story; console number tweens | One animation library for everything |
| Lenis | Landing only | Smooth scroll story. The console doesn't scroll. |
| Real liquid glass (`liquid-glass-js`) | **P2**, at most one lens over the landing proof band (an HTML background) | It refracts HTML clones, not WebGL. CSS glass everywhere else. |

### 4.2 Performance budget

| Page | WebGL contexts | Budget |
|---|---|---|
| Landing | 2 (gradient + logo) | Hero complete within 1.5 s of load, no blocking loader. Both shaders pause when scrolled out of view (`ShaderGradient animate="off"`, `LiquidMetal speed={0}` via IntersectionObserver). |
| Console | 1 (cluster view) | < 8 ms per frame on an integrated laptop GPU; ≤ 60 draw calls; ≤ 2,000 particles; `dpr={[1, 1.5]}` |
| Boot overlay | 1 (logo) | Unmounted when dismissed |

### 4.3 Smoothness rules (from the `smooth-3d-websites` skill)

1. **One clock on the landing page:** Lenis and ScrollTrigger run on `gsap.ticker` (`lenis.raf(time*1000)`, `gsap.ticker.lagSmoothing(0)`), and scroll-linked animation uses `scrub: 1`.
2. **Damp, don't set:** pointer and state-driven 3D values ease toward targets with `maath/easing` (`damp`, `damp3`, `dampC`), with `dt` clamped to 0.05.
3. **Separate transforms by owner** in 3D: `stage` (layout) → `node` (state: sink, scale) → `mesh` (idle).
4. **Cap pixel ratio:** R3F `dpr={[1, 1.5]}`; ShaderGradient `pixelDensity={1}`.
5. **Animate only `transform` and `opacity`** in the DOM. Use `gsap.quickTo` for anything pointer-driven.
6. **No allocation in `useFrame`:** reuse vectors and colors; memoize geometries and materials.
7. **No flash, no shift:** reserve the space for every canvas; fade canvases in after their first frame; call `ScrollTrigger.refresh()` on `load`.
8. **Respect people:** reduced motion honored; a CSS gradient fallback when WebGL fails; nothing hijacks keyboard scrolling.
9. **Verify:** run the skill's `check_site.py` smoke test on the landing page (desktop + mobile screenshots, console errors, blank-canvas check) before the feature freeze.

---

## 5. Screens

### 5.1 Information architecture

| Route | Page | Purpose |
|---|---|---|
| `/` | Landing | 10-second story: what Vault is, why it matters, open the console |
| `/console` | Overview | Live cluster: machines, 3D view, timeline, KPIs, chaos dock |
| `/console/files` | Files | Upload, list, download, inspect a file's pieces and effective copies |
| `/console/fate` | Fate | Shared dependencies, relabeling, at-risk files, advice |
| `/console/oracle` | Durability check | Vault vs Naive runs, violations, samples |

Console tabs sit in the top bar. The chaos dock and KPI strip appear on every console page.

### 5.2 Landing (`/`)

```
┌──────────────────────────────────────────────────────────────────────────┐
│ V A U L T                                     ● Live: 6 machines · 204 files│  ← live pill hides if backend is off
│                                                                            │
│                         [ liquid-metal V ]                                 │  ShaderGradient full-bleed behind
│                     Storage that heals itself.                            │
│      S3-grade durability on a few ordinary machines. No cloud. No sysadmin.│
│              ( Open the console → )     Watch it break ↓                  │
├──────────────────────────────────────────────────────────────────────────┤
│ STORY (sticky, 4 beats × 100vh, scrubbed)                                  │
│  1 Machines fail. Every day.          [2D cluster: one machine turns red] │
│  2 Vault notices in seconds.          [φ meter rises, peers are asked]    │
│  3 And rebuilds before anyone notices.[copies stream to healthy machines] │
│  4 Even when three copies share a plug.[power island glows, a copy moves] │
├──────────────────────────────────────────────────────────────────────────┤
│ PROOF   Effective copies 3  ·  Recovered in 14 s  ·  0 violations / 500 faults │
│         (from the latest durability check; labeled "sample" if none)      │
│                         ( Open the console → )                            │
│ Built at <hackathon> by Anushka, Jaiveer, Soum and Urooz                  │
└──────────────────────────────────────────────────────────────────────────┘
```
- Hero entrance: logo fades in first (400 ms), then the headline words rise with `expo.out` and 0.05 s stagger (SplitText optional).
- Primary CTA: CSS glass pill with a magnetic hover (`gsap.quickTo`, strength 0.35). Secondary: text link that scrolls to the story.
- Story visual: `ClusterScene2D` in scripted mode (no backend needed), driven by ScrollTrigger progress. Captions left, visual right; stacked on narrow screens.
- Keep it short: judges spend about 10 seconds here. Build budget for this page: ~3 hours.

### 5.3 Console Overview (`/console`)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ [V] VAULT  ● Your data is safe. 6 of 6 machines are healthy.  Overview Files Fate Check  [⚙] │ 56
├───────────────┬───────────────────────────────────────────────┬──────────────┤
│ MACHINES      │                                               │ WHAT'S       │
│ ┌───────────┐ │                                               │ HAPPENING    │
│ │● Reception│ │           3D CLUSTER VIEW                      │ [Explain ◻]  │
│ │ PC Healthy│ │   machines on a ring, standing on power        │ 12:03:11     │
│ │ n1 φ0.3 ▁▂│ │   islands; the Vault index at the center;      │ Lab Laptop   │
│ │ ▬▬▬▬░ 42% │ │   cut cables in red, relays in violet,         │ stopped      │
│ └───────────┘ │   repair flows as cyan particle arcs           │ responding…  │
│  … × 6        │                                               │ …            │
├───────────────┴───────────────────────────────────────────────┴──────────────┤
│ Effective copies │ Recovery time    │ Availability │ Storage overhead │ Durability check │ 88
│ 3 ▮▮▮            │ 14.0 s ▮▮▮░      │ 99.8%        │ 3.0× clinic      │ 0 / 500 faults   │
├──────────────────────────────────────────────────────────────────────────────┤
│ [⏻ Turn off] [🐞 Damage] [✂ Cut cable] [🐌 Slow] [❄ Freeze] [⚡ Power cut] [⇄ Move plug] [+ Add] [Clear] │ 64
└──────────────────────────────────────────────────────────────────────────────┘
```
(Icons in the wireframe stand for lucide icons: `Power`, `Bug`, `Unplug`, `Snail`, `Snowflake`, `Zap`, `PlugZap`, `Plus`, `Eraser`.)

**Top bar:** static mark + wordmark · **health sentence** (§6.2, colored dot by level, `aria-live="polite"`) · tabs · mode badge (Naive only) · settings.

**Machine card** (260 × 76 px, one per node, click to select):
- Row 1: state icon + display name (16/500) · state word chip on the right (§3.1).
- Row 2 (mono 13): `n3 · φ 0.4 · 402 copies`, a 60 px φ sparkline (last 30 s).
- Row 3: disk bar (4 px) with % · label glyphs (`Zap` A, `Network` S2, `HardDrive` D3) at 12 px.
- Relay: the chip reads "Via Doctor's Desk" in `partitioned` color. Slow: snail glyph. Fenced: lock glyph.
- Selected: 1 px `--color-brand` outline; the 3D node shows a selection ring.

**Timeline** (right column): newest on top, max 50 visible, older collapse. Each item: time (mono, relative under 60 s: "12s ago"), severity icon, human line (14 px), technical line (13 px mono, `--color-fg-2`) when **Explain** is on. `repair.progress` updates its incident's item in place with a thin progress bar instead of adding lines. Chaos actions ("You cut the cable between…") use a neutral icon so the user's actions and Vault's reactions are distinguishable.

**KPI strip** (5 tiles, click to open detail):

| Tile | Value | Detail line | Tone |
|---|---|---|---|
| Effective copies | min IFL (e.g. `3`) + mini histogram 1/2/3 | "204 files · 0 at risk" | by §3.1 scale |
| Recovery time | last MTTR (`14.0 s`) + stacked bar detect / grace / repair | "detect 1.9 · wait 8.0 · rebuild 4.1" | neutral; repair color while an incident is open |
| Availability | `99.8%` (60 s) | "100% of files readable now" | ok ≥ 99.9, suspect ≥ 99, dead below |
| Storage overhead | cluster `2.4×` | "clinic 3.0× · archive 1.5× · scratch 2.0×" | neutral |
| Durability check | violations of latest run (`0`) | "500 faults · Vault mode · 2 min ago" | ok if 0, dead otherwise |

**Chaos dock:** each button opens a small popover with targets (machine names with state dots). Two clicks maximum. Active faults show as removable chips above the dock: `Cable cut: Vault index ↔ Records Room ✕`. The dock stays visible in Naive mode (with an orange top border).

### 5.4 Files (`/console/files`)

```
┌──────────────┬──────────────────────────────────────────────────────────────┐
│ BUCKETS      │  ┌──────────── Drop files to store them in clinic ─────────┐  │
│ clinic       │  └──────────────────────────────────────────────────────────┘  │
│ 3 copies 3.0×│  Name              Size   Protection  Eff. copies  Pieces   Updated │
│ archive      │  xray-0042.png     471 KB 3 copies    ● 3         ▪▪▪      2m ago  │
│ 4+2 code 1.5×│  lab-report-113.pdf 2.1 MB 3 copies   ● 2         ▪▪▪ ▪▪▫  1m ago  │
│ scratch      │  …                                                            │
│ 2 copies 2.0×│                                   [Seed demo files]           │
└──────────────┴──────────────────────────────────────────────────────────────┘
```
- **Protection** badge wording: `rep3` → "3 copies", `rep2` → "2 copies", `ec42` → "4+2 code". Tooltip explains: "Split into 4 pieces plus 2 backup pieces. Any 4 rebuild the file. Uses 1.5× space."
- **Pieces** mini-matrix: one column per chunk (max 8, then "+N"), one dot per fragment, colored by fragment state.
- Upload: drag-and-drop or picker; each file shows a progress row (bytes sent), then the `PutResult` ("Saved on 3 machines"). Errors use §6 copy ("Only 1 of 2 machines confirmed. Nothing was saved; try again.").
- **Inspect drawer** (480 px, right): name, size, ETag (first 12 chars), version and commit number; a fragment grid (rows = pieces, columns = chunks, cells = machine initials colored by state); **"To lose this file, these must all fail:"** followed by the minimum failure set as chips (e.g. `Power Strip A` `Power Strip B` `Power Strip C`); buttons Download · Delete.

### 5.5 Fate (`/console/fate`)

```
┌────────────────────────────────────────────┬──────────────────────────────┐
│ WHAT YOUR MACHINES SHARE                   │ EFFECTIVE COPIES             │
│             Power   Switch  Disk   Software│  3 ████████████████ 204      │
│ Reception PC  [A▾]   [S1▾]  [D1▾]   1.0    │  2 ░ 0                        │
│ Doctor's Desk [A▾]   [S2▾]  [D2▾]   1.0    │  1 ░ 0                        │
│ Lab Laptop    [B▾]   [S2▾]  [D3▾]   1.0    │                               │
│ …                                          │ FILES AT RISK (0)             │
│ ┌ Power Strip A ┐ ┌ Power Strip B ┐ …       │ ADVICE                        │
│ │ 2 machines    │ │ 2 machines    │         │ "Power Strip A feeds 4 of 6…" │
│ │ 0 files at risk│ │ 0 files at risk│        │ CLUSTER-WIDE RISK             │
│ └───────────────┘ └───────────────┘         │ "All machines run 1.0…"       │
│ [⚡ Cut power strip ▾]                       │                               │
└────────────────────────────────────────────┴──────────────────────────────┘
```
- The matrix cells are editable selects (relabel). A change shows a confirmation toast: "Moved Lab Laptop to Power Strip A. Checking your files…"
- Cells are tinted with their power-strip island color (§3.1) so shared values visibly group.
- Domain cards turn red when files are at risk because of that domain.
- The histogram animates as the auditor moves copies (numbers tween, bars grow).

### 5.6 Durability check (`/console/oracle`)

```
┌─────────────────────────────────┬─────────────────────────────────┐
│ VAULT (safety on)        ● Done  │ NAIVE (safety off)       ● Done  │
│            0                     │            N                     │
│      violations                  │      violations                  │
│ Lost 0 · Damaged 0 · Resurrected 0│ Lost · Damaged · Resurrected …   │
│ Stale reads 0 · Phantom reads 0  │                                  │
│ 4,812 ops · 14 unknown · 47 faults│ 4,790 ops · … · 47 faults       │
│ [Run again]                      │ [Run again]                      │
├─────────────────────────────────┴─────────────────────────────────┤
│ TIMELINE  0s ───●crash n2──●cut──●slow──●power B──●corrupt── 60s  │  chaos markers above,
│           Naive ─────────────│││───────││──────│││─────────       │  violation ticks below
├───────────────────────────────────────────────────────────────────┤
│ WHAT WENT WRONG IN NAIVE MODE                                      │
│ k042 was saved at 12:03:11 and is now missing.                     │
│ k117 returned bytes that were never written (damaged copy served). │
└───────────────────────────────────────────────────────────────────┘
```
- The big number is the story: 0 in `ok`, anything else in `dead`, 72 px mono.
- "Run again" warns first: "A check takes about 2 minutes and resets the demo cluster."
- While running: state pill, elapsed/60 s bar, live counters via SSE.
- Every sample has a human line and, in Explain mode, the technical line.

### 5.7 Boot and power-cut overlays

- **Power cut (all):** when the SSE stream drops right after a `chaos.power_cut` action, dim the console to 20% and show centered: "Power cut. Every machine is off." The dashboard itself keeps running.
- **Power restored:** full-screen glass overlay with the `LiquidMetal` logo (160 px). Lines appear as events arrive: "Vault's index recovered 204 files from its log in 310 ms" · "Reception PC discarded 1 unfinished write" · "6 of 6 machines are back". Final line compares file counts captured before and after: **"204 of 204 files present. 0 lost."** Auto-dismiss after 3 s or on click.
- The "0 lost" line is computed from data (file count before vs after, plus `unreadable_files`), never hard-coded.

### 5.8 Chaos action menus

| Button | Menu | Result copy (toast + timeline) |
|---|---|---|
| Turn off | machine list | "You turned off Lab Laptop." |
| Damage copies | count 1 / 10 / 50 | "You damaged 10 random copies." |
| Cut cable | pick A, pick B (incl. "Vault index", "Vault gateway"), both ways / one way | "You cut the cable between Vault index and Records Room." |
| Slow | machine, 300 / 800 / 2000 ms | "You slowed Records Room by 800 ms." |
| Freeze | machine, 10 s | "You froze Pharmacy PC for 10 s." |
| Power cut | Everything · Power Strip A / B / C, auto-restore after 8 s (default on) | "You cut Power Strip A." |
| Move plug (relabel) | machine → power strip | "You moved Lab Laptop to Power Strip A." |
| Add machine | name + labels | "You added Storeroom PC." |
| Clear | all faults | "You fixed every cable and speed problem." |
| (Settings) Reset demo | confirm dialog | "Demo reset: 6 machines, 204 files." |

Control-plane display names: `meta` = "Vault index", `gw` = "Vault gateway".

### 5.9 Settings drawer

Mode (Vault / Naive, with a confirm and a clear explanation) · repair speed slider (10–200 MB/s) · "wait before rebuilding" slider (5–60 s) with the tradeoff in one line ("Shorter = faster recovery, but rebuilds machines that are only rebooting") · Explain mode · Presenter mode · 2D view · Reset demo.

### 5.10 Presenter mode

Root font size 18 px, secondary text brighter (`--color-fg-1` → `#C9CFDC`), KPI values 40 px, technical lines hidden unless Explain is on, timeline shows 20 items. Toggle with the settings drawer or `P`. Keyboard shortcuts (P1, shown with `?`): `1`–`6` select machine, `K` turn off selected, `C` damage 10 copies, `E` toggle Explain, `P` presenter mode.

---

## 6. Content design (the plain-language system)

### 6.1 Voice rules

1. Lead with safety, then what happened, then what Vault is doing, then what you should do (if anything).
2. Human lines use display names ("Lab Laptop"), never IDs. IDs, φ, epochs and fids belong in the technical line.
3. Words: **copies** (not replicas or fragments), **pieces** (EC fragments), **damaged** (not corrupt), **can't reach** (not partitioned), **turned off / not responding** (not crashed), **rebuild** (not re-replicate), **Power Strip A / Network Switch S1 / Disk Batch D1 / Software 1.0** for label values.
4. Numbers: "3 copies", "12 s", "63%", "2.1 MB". Relative time under a minute ("12s ago"), clock time after.
5. Never say "guaranteed" or "proven". Say "checked" and "measured".
6. One sentence per line, at most two. No exclamation marks except "All files are fully protected again."

### 6.2 Status sentence (top bar, `summary.human`)

| Level | Template |
|---|---|
| `ok` | "Your data is safe. {alive} of {total} machines are healthy." |
| `degraded` | "Your data is safe, but {n} files have fewer copies than usual. Vault is rebuilding them ({pct}% done)." |
| `at_risk` | "{n} files are one failure away from loss. Vault is rebuilding them first. Please don't turn off other machines." |
| `critical` | "{n} files can't be read right now because the machines holding them are off. Turn {names} back on." |
| Naive mode prefix | "Comparison mode: safety features are off. " + the sentence above |

### 6.3 Event copy (backend templates in `vault/common/events.py`)

| Type | Severity | Human | Technical |
|---|---|---|---|
| `node.joined` | info | "{node} joined Vault." | "register {id} epoch={epoch} labels={labels}" |
| `node.suspect` | warn | "{node} is slow to answer. Checking…" | "φ={phi:.1f} ≥ {threshold}; last heartbeat {s:.1f}s ago" |
| `node.partitioned` | warn | "Vault can't reach {node} directly, but {peer} can. Routing through {peer}." | "{src}→{id} failed; {peer}→{id} ok; relay active" |
| `link.relayed` | warn | "The cable between {a} and {b} looks cut. Messages now go through {relay}." | "link {a}↔{b} blocked ({dir}); relay {r}" |
| `link.restored` | success | "{a} and {b} can talk directly again." | "link {a}↔{b} ok; relay released" |
| `node.down` | warn | "{node} stopped responding. Your files are still readable." | "φ={phi:.1f}; no peer reached {id} in {confirm}s" |
| `node.recovered_in_grace` | success | "{node} is back after {s}s. No rebuild needed." | "heartbeat resumed before dead_after={x}s; avoided repair of {chunks} chunks" |
| `node.dead` | danger | "{node} has been off for {s}s. Rebuilding its {n} copies on other machines." | "DEAD; epoch {e}→{e2}; {chunks} chunks below target; incident #{inc}" |
| `node.fenced` | warn | "{node} lost contact with Vault and stopped accepting new files, to stay safe." | "lease expired {s}s ago; writes refused" |
| `node.slow` | info | "{node} is answering slowly. Vault will read from other machines first." | "p50 rtt {ms}ms > {limit}ms" |
| `node.rejoined` | success | "{node} is back. Removed {n} extra copies it no longer needs." | "re-registered epoch {e}; kept {kept}, trimmed {trim}" |
| `node.drained` | success | "{node} is empty and can be unplugged." | "all fragments moved; RETIRED" |
| `node.startup_discarded` | info | "{node} started up and discarded {n} unfinished write(s) from before the power cut." | "removed {n} tmp/*.part" |
| `meta.recovered` | success | "Vault's index recovered {files} files from its log in {ms} ms." | "SQLite WAL recovery; {versions} committed versions; {pending} pending expired" |
| `write.quorum_failed` | warn | "{file} couldn't be saved: only {got} of {w} machines confirmed it. Nothing was saved; try again." | "quorum_not_met W={w} got={got}" |
| `read.failover` (aggregated per 10 s) | info | "Served {n} downloads from backup copies because {node} didn't answer." | "failover reads={n} from {id}" |
| `fragment.corrupt` | warn | "Found a damaged copy of {file} on {node}. Replacing it from a healthy copy." | "sha256 mismatch fid={fid} ctx={ctx}; quarantined" |
| `fragment.missing` | warn | "A copy of {file} is missing from {node}. Rebuilding it." | "fid={fid} absent in inventory" |
| `repair.started` | info | "Rebuilding {n} copies ({size}) that were on {node}. Files closest to loss go first." | "incident #{inc}: {p0} P0, {p1} P1 jobs; {mbps} MB/s cap" |
| `repair.progress` | info | "Rebuilding: {pct}% done, about {eta}s left." | "{done}/{total} jobs; {mbps:.0f} MB/s" |
| `repair.completed` | success | "All files are fully protected again! Recovered in {mttr:.1f}s." | "detect {d:.1f}s + wait {g:.1f}s + rebuild {r:.1f}s; {bytes} moved" |
| `repair.blocked` | warn | "Can't rebuild {n} copies yet: {reason}." | "no eligible target: {why}" |
| `repair.failed` | danger | "Couldn't rebuild a copy of {file} after 3 tries. Vault will keep trying." | "job {job} failed: {error}" |
| `scrub.completed` | info | "Checked {n} copies on {node}. {c} damaged found." | "scrub pass {ms}ms, {mb} MB" |
| `fate.at_risk` | danger | "{n} files have all their copies on {domain}. One problem there would lose them. Moving copies now." | "IFL=1 for {n} files; shared {key}={value}" |
| `fate.fixed` | success | "{n} files now survive losing {domain}. Effective copies: {ifl}." | "moves={m}; IFL {before}→{after}" |
| `fate.limited` | warn | Advice sentence, e.g. "Power Strip A feeds 4 of 6 machines. Give Lab Laptop or Records Room its own power supply to get 3 independent copies." | "max IFL {x} < target {t}; {constraint}" |
| `fate.cluster_wide` | info | "All machines run {domain}. One bug there could affect all of them." | "{key}={value} covers every node; excluded from scoring" |
| `rebalance.started` | info | "{node} joined. Moving about {pct}% of your data onto it." | "{moves} moves planned; ideal {ideal:.1%}" |
| `rebalance.completed` | success | "Balanced. Moved {actual:.0%} of your data (ideal {ideal:.0%})." | "{bytes} moved in {s:.1f}s" |
| `gc.cleaned` (aggregated) | info | "Cleaned up {size} of leftovers from unfinished uploads and old versions." | "trimmed {n} fragments" |
| `config.mode_changed` | warn / success | Naive: "Comparison mode: safety features are off. Vault now behaves like a basic storage system." · Vault: "Safety features are back on." | "safety={flags}" |
| `chaos.*` | neutral | §5.8 result copy | "{action} {params}" |
| `oracle.run_started` | info | "Durability check started ({mode} mode): {clients} clients, {duration}s of chaos." | "run {id} seed={seed}" |
| `oracle.violation` | danger | "Durability check found a problem: {sample}" | "{kind} key={key} {detail}" |
| `oracle.run_completed` | success / danger | "Durability check finished: {faults} faults, {v} violations." | "ops={ops} unknown={u}" |

Domain names in human copy: `power=A` → "Power Strip A", `switch=S1` → "Network Switch S1", `disk_batch=D1` → "Disk Batch D1", `version=1.0` → "Software 1.0".

### 6.4 Formatting

- Bytes: `2.1 MB` (base 1000 for display), percentages without decimals unless < 1% or > 99% (`99.8%`).
- Durations: `1.9 s`, `14 s`, `2 min`. Times: `12:03:11` (24 h, local).
- IDs in technical lines: shorten fids to `v_4f1c…_00002_f1`.

---

## 7. 3D cluster view (`ClusterScene`)

**Layout:**
- Camera: perspective, `fov 35`, position `[0, 5.5, 9]`, looking at the origin. **Fixed** (no OrbitControls); a gentle damped parallax of ±0.3 units follows the pointer.
- Machines evenly spaced on a ring of radius 3.4 (angle `i/N·2π − π/2`), in node-ID order. The ring itself is a thin line in `--color-line`: it *is* the consistent-hash ring.
- **Power islands:** a flat rounded platform under each group of machines sharing a `power` label, tinted per §3.1, with an `<Html>` label ("Power Strip A"). Relabeling slides a machine's island membership (the platform re-forms over ~600 ms). A power cut on a strip turns its island black and its machines dark.
- **Vault index** at the center: a small icosahedron (radius 0.5, wireframe, brand color), slowly rotating, pulsing briefly with write traffic.

**Machine appearance** (drei `RoundedBox` 0.9 × 1.2 × 0.9, radius 0.12; `MeshPhysicalMaterial` color `#1a1f2e`, metalness 0.6, roughness 0.35, clearcoat 1 on desktop, `MeshStandardMaterial` on touch):

| State | Visual |
|---|---|
| ALIVE | Emissive state color at 0.25, steady |
| SUSPECT | Amber emissive pulse, period from φ (§3.5) |
| PARTITIONED | Violet emissive; relay path drawn (below) |
| DOWN | Orange slow blink (1 Hz) |
| DEAD | Red, intensity 0.1, sinks 0.25 units, 60% opacity |
| REJOINING | Blue, rises back into place |
| FENCED | Lock badge (`<Html>`) |
| Slow | Snail badge (`<Html>`) |
| Selected | Thin brand-colored ring on the floor |

A vertical emissive strip on each machine's front face shows disk usage.

**Links and flows:**
- Only abnormal links are drawn. A cut cable is a red dashed line (drei `<Line dashed>`). A relay route is a violet dashed polyline A → relay → B with an animated dash offset.
- **Repair and move flows:** for each active job (≤ 8), a raised quadratic arc from source to target with 12 instanced particles traveling along it: cyan for repair, brand color for fate or rebalance moves.
- **Writes:** faint white particles from the index to machines, sampled (1 per 5 writes, ≤ 20 in flight).
- **Damage found:** a pink ring burst at the machine (scale 1 → 2, opacity 1 → 0, 800 ms).

**Labels:** `<Html>` only (display name under each machine, island names). Never drei `<Text>` (it fetches a font from a CDN).

**Data:** the scene reads only the zustand snapshot. It never fetches. Visual values damp toward targets derived from the snapshot (§4.3).

**2D fallback (`ClusterScene2D`, SVG):** same layout, circles for machines, arcs for islands, lines for links, dots on paths for flows. Used when WebGL fails, with reduced motion, when the 2D toggle is on, and (in scripted mode) for the landing story.

---

## 8. Components and data

### 8.1 Data flow

- **One SSE connection** to `/v1/stream` feeds the zustand store: `snapshot` (every 500 ms) and `events` (ring buffer of 200).
- Polling: `/v1/metrics` every 2 s; `/v1/fate` every 2 s on the Fate page and whenever `summary` changes; supervisor `/procs` and `/chaos` every 1 s; oracle `/runs` every 2 s (SSE while a run is active); gateway list on the Files page on demand and after uploads.
- φ history per node (last 60 samples) is kept client-side from snapshots.

Store shape (`web/lib/store.ts`):
```ts
type VaultStore = {
  connected: boolean; snapshot: Snapshot | null; events: VaultEvent[];
  metrics: Metrics | null; fate: FateReport | null; faults: Fault[]; procs: Proc[]; runs: RunSummary[];
  phiHistory: Record<string, number[]>;
  ui: { selectedNode: string | null; explain: boolean; presenter: boolean; view2d: boolean };
  // setters: setSnapshot, pushEvent, setConnected, setMetrics, setFate, setFaults, setProcs, setRuns, setUi
};
```

### 8.2 Inventory

| Component | Props | Source |
|---|---|---|
| `ConsoleShell` | children | connects SSE, pollers, presenter class |
| `TopBar` | — | `snapshot.summary`, `snapshot.mode`, `connected` |
| `HealthSentence` | `summary`, `explain` | store |
| `ModeBanner` | `mode` | store |
| `NodeList` / `NodeCard` | `node`, `phiHistory`, `selected`, `onSelect` | `snapshot.nodes` |
| `PhiSparkline` | `values: number[]` | store |
| `DiskBar` | `used`, `capacity` | node |
| `LabelGlyphs` | `labels` | node |
| `ClusterScene` / `ClusterScene2D` | `snapshot`, `selectedId`, `onSelect`, `scripted?` | store |
| `EventTimeline` / `EventItem` | `events`, `explain` | store |
| `KpiStrip` / `KpiTile` | `label`, `value`, `unit`, `tone`, `detail`, `onClick` | metrics, summary, runs |
| `MttrBar` | `detect`, `grace`, `repair` | `metrics.last_incident` |
| `ChaosDock` / `ChaosButton` / `TargetMenu` | `nodes`, `faults`, `onAction` | supervisor |
| `ActiveFaults` | `faults`, `onClear` | supervisor |
| `BucketList`, `UploadDropzone`, `UploadRow` | `bucket`, … | gateway |
| `FileTable`, `ProtectionBadge`, `IflChip`, `PieceMatrix` | `objects` | gateway list + object health |
| `InspectDrawer`, `FragmentGrid` | `bucket`, `key` | `/v1/objects/{b}/{k}/health` |
| `FateMatrix`, `DomainCard`, `IflHistogram`, `AtRiskList`, `AdviceCard`, `ClusterWideRisk` | from `FateReport` | `/v1/fate` |
| `OracleColumn`, `ViolationBreakdown`, `RunCounters`, `ChaosTimelineStrip`, `ViolationSamples` | `run` | oracle |
| `BootOverlay`, `PowerCutOverlay`, `ConnectionBanner` | — | store |
| `SettingsDrawer`, `ShortcutsHelp` | `config` | metadata `/v1/config` |
| `VaultMark` (LiquidMetal), `VaultMarkStatic`, `Wordmark` | `size` | — |
| `HeroBackground`, `Hero`, `StoryScroller`, `ProofBand`, `CtaButton` | — | landing; `ProofBand` reads latest run if reachable |

Folder mapping: `components/brand`, `components/console`, `components/scene`, `components/files`, `components/fate`, `components/oracle`, `components/ui` (buttons, chips, popovers, drawer, toast, tooltip).

---

## 9. States

| Situation | What the user sees |
|---|---|
| Backend not running | Console: "Vault isn't running. Start it with `python -m vault up`." with a Retry button. Landing: the live pill hides; proof band says "sample". |
| First load | Skeleton cards (pulsing `bg-white/5`) for ≤ 1 s; the 3D view fades in after its first frame. |
| SSE dropped (not a power cut) | Amber banner at the top: "Reconnecting to Vault…" (EventSource retries automatically). |
| No files | Files: "No files yet. Drop files here, or seed the demo set." with a Seed button. |
| No durability runs | Oracle: "No checks yet. A check takes about 2 minutes and resets the demo cluster." |
| Naive mode | Orange 4 px top border, mode badge, sentence prefix (§6.2), orange-tinted background gradient. |
| Upload failure | Row turns red with the §6.3 copy; nothing else changes. |
| Reduced motion / 2D | Static states, instant numbers, `ClusterScene2D`. |

---

## 10. Accessibility

- Every state uses icon + word + color (§3.1). Never color alone.
- Focus ring: 2 px `--color-brand`, offset 2 px, on every interactive element. The chaos dock, menus and drawers are fully keyboard operable (Tab, Enter, Esc).
- `aria-live="polite"` on the health sentence and the timeline, `assertive` for `danger` events only.
- The 3D view has an `aria-label` summary ("6 machines, all healthy") and is `aria-hidden` for detail; the machine list is the accessible equivalent.
- Contrast: body text ≥ 4.5:1 and large numbers ≥ 3:1 on their backgrounds.

---

## 11. Demo polish checklist (before the feature freeze)

- [ ] Overview fits 1280×720 with no scrolling; check at 1920×1080 on the venue projector if possible.
- [ ] Every chaos button produces a visible change within 1 s.
- [ ] The health sentence is correct in every level (test each with chaos).
- [ ] Presenter mode is readable from 4 m away.
- [ ] 2D toggle works, and WebGL failure falls back to it.
- [ ] Boot overlay's "0 lost" line matches the actual file count.
- [ ] Landing passes `check_site.py` (no console errors, no blank canvas) at desktop and mobile sizes.
- [ ] No text overlaps the 3D view's labels at 1280 px.
