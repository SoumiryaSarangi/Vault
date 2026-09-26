# Vault on 4 laptops: setup and demo runbook

**Owner:** Soum · **Approved by:** Anushka (MASTER_PLAN §10) · **Date:** 2026-09-26

The core demo: **4 laptops, one storage machine each**. Close one laptop's lid and Vault notices, rebuilds that laptop's copies on the other three, and the dashboard shows every step. Open the lid and the laptop rejoins by itself.

The one-laptop demo (`python -m vault up`, 6 simulated machines) still works exactly as before and is the **fallback** if the network fails on stage.

---

## 1. Roles

| Laptop | Runs | Command |
|---|---|---|
| **1: the hub** (goes on the projector) | index, gateway, supervisor, durability check, dashboard, and storage machine **n1** | `python -m vault up --lan` and `cd web && npm run dev` |
| **2, 3, 4** | one storage machine each (**n2, n3, n4**) plus a small agent that lets the dashboard turn it off and on | `python -m vault join --hub <hub-ip> --id n2 --name "Doctor's Desk" --strip B` |
| **Any extra laptop** | one more machine, added live | the command shown in the dashboard under **Add → Real laptop** |
| **No spare laptop?** | a simulated machine running on the hub | dashboard **Add → Simulated** |

Only the hub's IP is ever typed in. No config file lists the other laptops, so a new IP from the hotspot doesn't break anything.

---

## 2. One-time setup (every laptop, before the demo day)

1. **Code and Python:** clone the repo, then in the repo folder:
   ```
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```
   Hub only: `cd web && npm ci`.
2. **Firewall:** open an **admin** PowerShell and run:
   ```
   netsh advfirewall firewall add rule name="Vault" dir=in action=allow protocol=TCP localport=3000,7000-7110
   ```
   When Windows asks whether Python may use the network, pick **Private networks → Allow**.
3. **Closing the lid must sleep:** Control Panel → Power Options → "Choose what closing the lid does" → **Sleep**, both *On battery* and *Plugged in*. (If it's set to "Do nothing", closing the lid kills nothing and the demo shows nothing.)
4. **Clock:** Settings → Time & language → **Set time automatically: On**. Vault tolerates clock differences, but keep them small anyway.

---

## 3. Network

- **Use a phone hotspot or a travel router, not the venue Wi-Fi.** Venue Wi-Fi usually blocks laptop-to-laptop traffic ("client isolation"), and then nothing can join.
- Put all 4 laptops on it and mark it as a **Private** network (Settings → Network → the Wi-Fi → Private).
- The hub's IP is printed when you start it (`Vault hub on 192.168.x.y`). You can also get it from `ipconfig`.

---

## 4. Start (about 2 minutes)

1. **Hub:** in the repo, with the venv active:
   ```
   python -m vault up --lan
   ```
   It prints its IP and the join command. In a second terminal: `cd web` then `npm run dev`.
2. **Laptops 2, 3, 4:** each runs **its own line**, with the hub's IP:
   ```
   python -m vault join --hub 192.168.1.101 --id n2 --name "Doctor's Desk" --strip B
   python -m vault join --hub 192.168.1.101 --id n3 --name "Lab Laptop"    --strip C
   python -m vault join --hub 192.168.1.101 --id n4 --name "Pharmacy PC"   --strip D
   ```
   Each prints `Joined the hub … Leave this window open.`
3. Open **http://&lt;hub-ip&gt;:3000/console** on the hub (any laptop works). You should see 4 cards, all **Healthy**. The 3 joined ones have a small laptop icon and their IP.
4. On the hub, run **`python -m vault reset`**. It wipes every laptop's data, restarts all 4 machines and uploads 200 demo files. The bottom tiles then show **Effective copies 3**.
5. Stick a note on each laptop, e.g. "Node 2 · Doctor's Desk". To change a name on screen, hover a card and click the ✎ pencil.

---

## 5. Demo scenes

| Scene | Do | Audience sees |
|---|---|---|
| **Close a lid** (main moment) | Close Laptop 3's lid | Lab Laptop: *Checking…* → *Not responding* → after ~8 s *Off · rebuilding*. The timeline says "Vault is rebuilding them (x% done)", and Effective copies goes back to 3. Files download fine throughout (Files tab). |
| **Open it again** | Open the lid, log in if asked | Within seconds Lab Laptop goes *Rejoining* → *Healthy*, the timeline shows it rejoined, and the extra copies are trimmed. |
| **Turn off from the dashboard** | **Turn off → Doctor's Desk** | The same flow as closing the lid, and repeatable. **Turn off → Turn back on** brings it back. |
| **Add a real laptop live** | **Add → Real laptop**, copy the command, run it on the new laptop | A 5th card appears, and the rebalancer moves a share of the copies onto it. |
| **Add without a device** | **Add → Simulated** | A machine running on the hub. It behaves like the others. |
| Everything else | Damage, Cut cable, Slow, Freeze, Power cut | Same as the one-laptop demo. A power cut on a strip turns off the laptop machine on that strip. |

**Say it right:** with 4 laptops and 3 copies per file, each file's copies are on **3 of the 4** laptops. When one dies, every file that had a copy there is rebuilt on a laptop that didn't have one. There is no single "spare" laptop.

---

## 6. When something goes wrong

| Symptom | Fix |
|---|---|
| `join` says **Can't reach the hub** | Is `vault up --lan` running on the hub? Are both laptops on the same hotspot? Is the IP right? Is the hub's firewall rule in place? |
| `join` says **The hub can't reach …** | The firewall rule is missing on **this** laptop (section 2, step 2), or the network is marked Public. |
| `join` says **n2 is already the laptop at …** | Two laptops used the same `--id`. Use the id it suggests. |
| A card shows **asleep or off the network** | That laptop is asleep or lost Wi-Fi. Wake it; its machine comes back by itself. |
| Closing the lid does nothing | The lid is set to "Do nothing" (section 2, step 3). |
| A joined laptop got a new IP after sleeping | Nothing to do: its agent notices and re-joins with the new address. |
| **The hub** got a new IP (different network) | Stop everything. Start `vault up --lan` again, then run `join` again on each laptop with the new IP. |
| Dashboard on another laptop is blank | Open it as `http://<hub-ip>:3000/console`, not `localhost`. |
| Nothing works on stage | Fallback: on the hub, stop everything and run the one-laptop demo: `python -m vault up`. |

Logs: hub `logs/lan/*.log`, joined laptops `logs/lan/agent-n2.log` and `logs/lan/n2.log`.

---

## 7. Rehearse on one laptop (no hardware needed)

Use the IP that `vault up --lan` prints, and give each agent its own agent port. Run each command in its own terminal:
```
python -m vault up --lan
python -m vault join --hub <printed-ip> --id n2 --name "Doctor's Desk" --strip B --agent-port 7072
python -m vault join --hub <printed-ip> --id n3 --name "Lab Laptop"    --strip C --agent-port 7073
python -m vault join --hub <printed-ip> --id n4 --name "Pharmacy PC"   --strip D --agent-port 7074
python -m vault reset
```
**No network at all?** In PowerShell, run `$env:VAULT_HUB="127.0.0.1"` before `vault up --lan`, and use `--hub 127.0.0.1`.

**Turn off / Turn back on** in the dashboard now goes through the agents, just like on real laptops.

---

## 8. How it works (for the curious and for judges)

- **Addresses:** `VAULT_HUB` makes every process look for the index, gateway and supervisor at the hub, and listen on the network (`common/config.py`). A joined machine advertises its own address (`VAULT_NODE_ADDR`) when it registers. Every other machine and the gateway learn it from the index's cluster view (`node/soum_pinger.py`), so repairs can copy straight between laptops.
- **Agent** (`node/soum_agent.py`): starts this laptop's machine, re-joins every 3 s (after a hub restart, a reset, a rename or an IP change), and obeys the hub's turn off / on / wipe.
- **Supervisor** (`supervisor/anushka_procs.py`, `anushka_cluster.py`): joined machines are "remote" children. Turn off/on and power cuts go to their agent. The supervisor asks each agent for its status every second; an agent that doesn't answer shows as *asleep or off the network*.
- **Lid closed:** the laptop sleeps, heartbeats stop, and the index's failure detector does the rest (suspect → confirm → down → 8 s grace → dead → rebuild). On wake the machine's lease has expired, so it refuses writes until it re-registers and reports what it still holds. Nothing lost, nothing stale.
- **Clocks:** failure detection and leases each run on one machine's clock. Orphan cleanup compares file ages on the node's own clock (`Inventory.sent_at`), so laptops with different clocks can't make fresh copies look old.
