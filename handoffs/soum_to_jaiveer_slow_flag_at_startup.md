# Handoff: soum -> jaiveer
Date/Time: Sat 26 Sep 2026, ~H7
Status: OPEN
Topic: `node.slow` fires for every machine at startup (false alarm on the timeline)

What I need changed / added:
Don't set the slow flag (or emit `node.slow`) from ping loss while a node, or the cluster, is still starting up.
For example, ignore reach rows during `detector.startup_grace_s`, or until the peer has been ALIVE for a few
seconds. Also, the `technical` line shows the RTT rule even when the loss rule fired.

Exact files or endpoints involved:
Your detector / membership slow-flag logic (`brain/jaiveer_detector.py` or `jaiveer_membership.py`). Input is
`Heartbeat.reach` from my pinger (`node/soum_pinger.py`).

Expected input / output (with example):
Seen live on `python -m vault up` (fresh data, main + S6), about 21 s **before** any chaos:
```
ts=…636.11 node.slow n1 "p50 rtt 0ms > 300ms"
ts=…636.11 node.slow n2 "p50 rtt 19ms > 300ms"
… n3, n4, n5, n6, then n3–n6 again 0.5 s later
```
The RTTs are 0–24 ms, so this must be the "≥ 20% ping loss" rule. During boot, nodes ping peers that
aren't listening yet, and my reach rows truthfully say `ok: false` for those. Expected: no `node.slow` at
boot. After a real power cut and restore the timeline would otherwise show 6 "answering slowly" messages
right on stage (PRD §9, 2:00 scene).

Why it is needed (which feature depends on it):
Demo timeline and the "Pull the plug" scene. Slow machines are also read last and skipped as repair
sources, so a wrong flag right after restore could briefly skew repair source choice.

Deadline (hour of hackathon): before H11 (M4 faults) if possible.

What I will use as a stub until then:
Nothing needed. My reach rows stay truthful: a failed ping is `{"ok": false, "rtt_ms": null}`, a successful
one has the real RTT. meta and gw are pinged via `/_vault/health`.

Gated? (yes/no, if yes Anushka approved: yes/no): no (inside Jaiveer's files)
