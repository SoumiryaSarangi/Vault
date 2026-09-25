# Vault: Tech Stack

**Status:** v1.0, frozen · **Owner:** Anushka (adding, removing or upgrading any dependency is gated, TEAM_PROTOCOL §4) · **Date:** 2026-09-26

**Verified on 2026-09-26:** every version below was installed together, the frontend passed `next build` with TypeScript strict, and a headless Chromium render showed the shader gradient, the liquid-metal logo and an R3F scene with **zero console errors**. The zfec, phi, atomic-write and SSE snippets in §6 were executed. **Do not change a version without asking Anushka.**

---

## 1. Runtimes

| Runtime | Version | Notes |
|---|---|---|
| **Python** | **3.12.x** (3.11 or 3.13 also fine) | **Not 3.14:** zfec has no 3.14 wheel and would need a C compiler. Check with `python --version`. |
| **Node.js** | **22 LTS** (≥ 20.9 required by Next 16) | Check with `node -v` |
| npm | 10+ | Comes with Node 22 |
| Git | any | |

Everything runs on macOS, Windows and Linux. No Docker, no Kubernetes, no database server.

---

## 2. Backend (Python)

`requirements.txt` (exact pins):
```
fastapi==0.141.1
uvicorn[standard]==0.54.0
httpx==0.28.1
pydantic==2.13.5
aiosqlite==0.22.1
sse-starlette==3.4.11
PyYAML==6.0.3
zfec==1.6.0.0
pytest==9.1.1
pytest-asyncio==1.4.0
```

| Package | Used for | Why this one |
|---|---|---|
| FastAPI + uvicorn | Every service's HTTP API | Async, pydantic models double as the contract, fast to write |
| httpx | All service-to-service calls (through `common/rpc.py`) | Async client with pooling, timeouts, streaming |
| pydantic v2 | Contract models (`common/models.py`) | Validation plus JSON schema for free |
| aiosqlite | Metadata DB (SQLite in WAL mode) | Stdlib SQLite, no server, crash-safe with `synchronous=FULL` |
| sse-starlette | `/v1/stream` and Oracle run streams | Correct SSE framing, keep-alives, disconnect handling |
| PyYAML | `vault.yaml` | |
| zfec | Reed-Solomon erasure coding (`ec42`) | Mature C core, wheels for macOS arm64, Windows, Linux (3.11–3.13) |
| pytest, pytest-asyncio | Unit tests for placement, fate, phi, EC, commit, storage | |

**Standard library, not packages:** `hashlib` (SHA-256 for integrity, `blake2b` for placement hashes), `asyncio` (subprocesses, tasks), `os.replace` / `os.fsync` (atomic writes), `secrets` (IDs), `math`/`statistics` (phi).

**Do not add:** Redis, Postgres, Kafka, gRPC/protobuf, Celery, Docker SDK, iptables tooling, a Raft library, `requests` (blocking), `reedsolo` (slow, pure Python), Python's built-in `hash()` for placement (randomized per process).

---

## 3. Frontend (web/)

`web/package.json` (exact pins, tested together):
```json
{
  "dependencies": {
    "next": "16.3.6",
    "react": "19.3.0",
    "react-dom": "19.3.0",
    "three": "0.186.1",
    "@react-three/fiber": "9.8.1",
    "@react-three/drei": "10.7.9",
    "@shadergradient/react": "2.4.20",
    "@paper-design/shaders-react": "0.0.81",
    "gsap": "3.15.0",
    "@gsap/react": "2.1.2",
    "lenis": "1.3.26",
    "zustand": "5.0.15",
    "lucide-react": "1.48.0"
  },
  "devDependencies": {
    "typescript": "5.9.3",
    "@types/react": "19.2.7",
    "@types/react-dom": "19.2.3",
    "@types/node": "22.19.1",
    "@types/three": "0.186.0",
    "tailwindcss": "4.3.3",
    "@tailwindcss/postcss": "4.3.3"
  }
}
```
Optional, P2 only: `"liquid-glass-js": "0.1.0"` (see §3.2). It installs cleanly with the set above.

| Package | Used for | Notes |
|---|---|---|
| Next.js 16 (App Router, Turbopack) | Landing + console | Server components by default; everything interactive is a `"use client"` component |
| React 19.3 | UI | R3F 9.8 supports React `>=19 <19.4`. **Don't upgrade React to 19.4+.** |
| three 0.186 + @react-three/fiber 9.8 + drei 10.7 | 3D cluster view (console only) | One `<Canvas>` per page. A `THREE.Clock is deprecated` console *warning* is harmless. |
| @shadergradient/react 2.4.20 | Animated gradient behind the landing hero | Uses our R3F and three (they're external imports). Landing only. |
| @paper-design/shaders-react 0.0.81 | `LiquidMetal`: the liquid-metal Vault logo | This *is* the liquid-logo effect, packaged. We don't copy the `paper-design/liquid-logo` repo. |
| gsap 3.15 + @gsap/react | Landing reveals, scroll story, number tweens | All plugins free, including ScrollTrigger |
| lenis 1.3 | Smooth scroll on the landing page only | Not in the console |
| zustand 5 | Client store fed by SSE | One store, selectors per component |
| lucide-react | Icons | Import icons individually |
| Tailwind CSS 4 | Styling | CSS-first config (`@theme` in `globals.css`), **no `tailwind.config.js`** |
| next/font (`Geist`, `Geist_Mono`) | Fonts | Self-hosted at build time, so it works offline after the first build |

**Do not add:** Framer Motion / `motion` (we use GSAP), a chart library (sparklines and bars are small custom SVGs), shadcn/ui (Tailwind + our own primitives are enough; extra files cost tokens), drei `<Environment preset=…>` (downloads HDRs from a CDN at runtime), drei `<Text>` (troika fetches a default font from a CDN; use `<Html>` labels), `<OrbitControls>` (fixed camera keeps the demo stable), Redux, React Query, socket.io (SSE is enough), dashersw/liquid-glass-js (§3.2).

### 3.1 Where each visual library goes (summary; details in DESIGN §4)

| Library | Landing | Console |
|---|---|---|
| `LiquidMetal` (Paper) | Hero logo | Boot/recovery screen logo only |
| `ShaderGradient` | Hero background | **Not used** (CSS gradient instead) |
| R3F scene | Not used (2D SVG story instead) | Cluster view (the only WebGL canvas) |
| GSAP + ScrollTrigger | Reveals + scroll story | Number tweens only |
| Lenis | Yes | No |
| Liquid glass (real refraction) | P2: one element over an HTML background | No (CSS glass) |

### 3.2 Liquid glass: finding and decision

- **dashersw/liquid-glass-js** (the repo we bookmarked) is **not on npm**, and it builds its refraction from an **html2canvas snapshot** of the page. html2canvas can't capture live WebGL, so over our shader gradient or 3D scene the glass would show a frozen or blank image. **Not used.**
- **`liquid-glass-js` on npm** (a different author, v0.1.0, published June 2026) refracts a *clone* of an HTML element with SVG displacement filters. It works in Chrome, Safari and Firefox, but also can't refract a WebGL canvas, and it's a brand-new single release. **P2 only**, for at most one element over a plain HTML background.
- **Everywhere else:** CSS glass (`backdrop-filter: blur() saturate()`), which *does* blur live WebGL underneath. Recipe in DESIGN §3.4.

---

## 4. Setup (each laptop, before the hackathon clock starts)

```bash
# 1. Python backend
cd vault
python3.12 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -c "import zfec, fastapi; print('backend ok')"

# 2. Frontend
cd web
npm ci                               # uses the committed package-lock.json
npm run build                        # also downloads and self-hosts fonts once
cd ..

# 3. Run
python -m vault up                   # supervisor → metadata, gateway, n1..n6, oracle
cd web && npm run dev                # http://localhost:3000   (P1: `vault up` starts this too)

# Useful
python -m vault reset                # wipe data, restart, create buckets, seed demo files
python -m vault status               # table of processes and node states
python -m pytest backend/vault/tests -q
curl -T samples/clinic/xray-0001.png http://127.0.0.1:7080/clinic/xray-0001.png
```

**Ports:** web 3000 · metadata 7000 · supervisor 7070 · gateway 7080 · oracle 7090 · nodes 7101–7109. If a port is busy, change `cluster.ports` in `vault.yaml` (gated).

**Environment variables:**

| Variable | Default | Used by |
|---|---|---|
| `VAULT_CONFIG` | `./vault.yaml` | all Python processes |
| `VAULT_DEMO` | unset | `1` → `dead_after_s: 8` for the stage demo |
| `NEXT_PUBLIC_META_URL` | `http://127.0.0.1:7000` | web |
| `NEXT_PUBLIC_GATEWAY_URL` | `http://127.0.0.1:7080` | web |
| `NEXT_PUBLIC_SUPERVISOR_URL` | `http://127.0.0.1:7070` | web |
| `NEXT_PUBLIC_ORACLE_URL` | `http://127.0.0.1:7090` | web |

---

## 5. Conventions

**Python**
- Package `vault` under `backend/`; run modules with `python -m vault.<service>`.
- Every HTTP call between services goes through `vault.common.rpc` (netsim, routing, relay, timeouts). **Never create another `httpx` client.**
- Every payload is a pydantic model from `vault.common.models`. No ad-hoc dicts across process boundaries.
- Events are created only via `vault.common.events.emit(type, subject, data)`, which fills `human` and `technical` from templates.
- Blocking I/O (`fsync`, `os.replace`, directory scans, large reads) runs in `asyncio.to_thread`.
- Logging: one line per event, `logs/<pid>.log`, format `ts pid level message`.
- Tests next to the pure logic: placement, fate (IFL), phi, EC round-trip, commit rules, storage atomicity.

**TypeScript**
- `web/lib/contracts.ts` mirrors `models.py` (same field names, snake_case kept as-is).
- One SSE connection (`web/lib/sse.ts`) feeds one zustand store (`web/lib/store.ts`). Components read with selectors, never fetch in loops.
- All WebGL components are client components loaded with `next/dynamic(..., { ssr: false })`.
- Numbers use `font-variant-numeric: tabular-nums` (`tabular-nums` in Tailwind).

---

## 6. Verified snippets (copy these; they ran on 2026-09-26)

### 6.1 Erasure coding with zfec (`common/ec.py`)
```python
import zfec

def ec_encode(chunk: bytes, k: int, m: int) -> list[bytes]:
    n = k + m
    pad = (-len(chunk)) % k
    data = chunk + b"\0" * pad
    size = len(data) // k
    blocks = [data[i*size:(i+1)*size] for i in range(k)]
    return zfec.Encoder(k, n).encode(blocks)          # n blocks: 0..k-1 data (systematic), k..n-1 parity

def ec_decode(frags: dict[int, bytes], k: int, m: int, chunk_size: int) -> bytes:
    idx = sorted(frags)[:k]                            # any k fragments
    primary = zfec.Decoder(k, k + m).decode([frags[i] for i in idx], idx)
    return b"".join(primary)[:chunk_size]

def ec_rebuild(frags: dict[int, bytes], k: int, m: int, want: int) -> bytes:
    idx = sorted(frags)[:k]
    primary = zfec.Decoder(k, k + m).decode([frags[i] for i in idx], idx)
    return zfec.Encoder(k, k + m).encode(list(primary), [want])[0]
```
Measured: 1 MiB chunk, encode ≈ 1.6 ms, decode ≈ 0.8 ms. A rebuilt fragment is byte-identical to the original.

### 6.2 Phi accrual (`common/phi.py`)
```python
import math, statistics
from collections import deque

class Phi:
    def __init__(self, window=100, min_std_s=0.2, first_s=0.5):
        self.iv = deque([first_s, first_s * 1.25], maxlen=window)
        self.last, self.min_std = None, min_std_s
    def heartbeat(self, now: float) -> None:
        if self.last is not None: self.iv.append(now - self.last)
        self.last = now
    def phi(self, now: float) -> float:
        if self.last is None: return 0.0
        t = now - self.last
        mean = statistics.fmean(self.iv)
        std = max(statistics.pstdev(self.iv), self.min_std)
        y = (t - mean) / std
        e = math.exp(-y * (1.5976 + 0.070566 * y * y))
        return -math.log10(e / (1 + e)) if t > mean else -math.log10(1 - 1 / (1 + e))
```
With 500 ms heartbeats: silence 1.0 s → φ 2.2, 1.5 s → φ 7.3, 2.0 s → φ 18. So φ ≥ 8 after ~1.6 s of silence, and a crash is confirmed `DOWN` at ~2.6 s.

### 6.3 Crash-safe write (`node/storage.py`, RealDisk)
```python
import os

def atomic_write(tmp_path: str, final_path: str, data: bytes) -> None:
    with open(tmp_path, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())                # bytes are on disk
    os.replace(tmp_path, final_path)        # atomic rename (also on Windows)
    if os.name != "nt":                     # make the rename itself durable
        fd = os.open(os.path.dirname(final_path), os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
# Call it via: await asyncio.to_thread(atomic_write, tmp, final, blob)
```

### 6.4 FastAPI service skeleton with SSE
```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse
import asyncio, json

@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [asyncio.create_task(loop()) for loop in (detector_loop, scheduler_loop)]  # background loops
    yield
    for t in tasks: t.cancel()

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"], allow_methods=["*"], allow_headers=["*"],
                   expose_headers=["ETag", "X-Vault-Seq", "X-Vault-Commit-Seq", "X-Vault-Read-Path"])

@app.get("/v1/stream")
async def stream():
    async def gen():
        while True:
            yield {"event": "snapshot", "data": json.dumps(build_snapshot())}
            await asyncio.sleep(0.5)
    return EventSourceResponse(gen())
# run: uvicorn vault.metadata.app:app --host 127.0.0.1 --port 7000 --workers 1
```

### 6.5 Liquid-metal logo (landing hero, boot screen)
```tsx
"use client";
import { LiquidMetal } from "@paper-design/shaders-react";

export function VaultMark({ size = 240 }: { size?: number }) {
  return (
    <LiquidMetal
      image="/brand/vault-mark.svg"        // DESIGN §2.2; bold, simple shapes work best
      colorBack="#00000000"                // transparent background
      colorTint="#ffffff"
      style={{ width: size, height: size }}
    />
  );
}
```

### 6.6 Shader gradient (landing hero background only)
```tsx
"use client";
import { ShaderGradientCanvas, ShaderGradient } from "@shadergradient/react";

export function HeroBackground() {
  return (
    <ShaderGradientCanvas style={{ position: "absolute", inset: 0 }} pixelDensity={1} fov={45}>
      <ShaderGradient type="waterPlane" animate="on" uSpeed={0.1}
        color1="#0b1026" color2="#1c2a5e" color3="#05060a" cDistance={3.6} />
    </ShaderGradientCanvas>
  );
}
```

### 6.7 Loading WebGL components
```tsx
"use client";
import dynamic from "next/dynamic";
const ClusterScene = dynamic(() => import("@/components/scene/ClusterScene"), {
  ssr: false,
  loading: () => <div className="h-full w-full animate-pulse bg-white/5 rounded-2xl" />,
});
```

### 6.8 SSE → zustand
```ts
// web/lib/sse.ts
import { useVault } from "./store";
export function connectStream(url = process.env.NEXT_PUBLIC_META_URL + "/v1/stream") {
  const es = new EventSource(url);
  es.addEventListener("snapshot", (e) => useVault.getState().setSnapshot(JSON.parse((e as MessageEvent).data)));
  es.addEventListener("event", (e) => useVault.getState().pushEvent(JSON.parse((e as MessageEvent).data)));
  es.onerror = () => useVault.getState().setConnected(false);   // EventSource retries by itself
  es.onopen = () => useVault.getState().setConnected(true);
  return () => es.close();
}
```

### 6.9 Tailwind 4 setup
```js
// web/postcss.config.mjs
export default { plugins: { "@tailwindcss/postcss": {} } };
```
```css
/* web/app/globals.css: tokens from DESIGN §3 go in @theme */
@import "tailwindcss";
@theme {
  --color-bg-0: #05060a;
  --color-ok: #34d399;
  /* … */
}
```

---

## 7. Offline and venue readiness

- Run `pip install -r requirements.txt` and `npm ci && npm run build` on every laptop **before** the clock starts. After that, nothing needs the internet except Claude.
- Fonts are self-hosted by `next/font` at build time. No CDN assets at runtime: no drei presets, no troika default font, no remote images.
- Commit `web/package-lock.json` so `npm ci` gives everyone identical versions.
- Last resort on one USB drive: `pip download -r requirements.txt -d wheels/` (per OS) and a copy of the repo with `web/node_modules`. Virtual environments aren't portable between machines, so don't copy `.venv`.
