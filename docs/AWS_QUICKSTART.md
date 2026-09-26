# Vault on AWS: 30-minute quickstart

**For:** whoever deploys the hosted demo right now. **Owner:** Anushka.
This is the fast path of `docs/DEPLOYMENT.md` Option A: **one EC2 instance, everything on it, Caddy in front with HTTPS and a password.** No domain needed. One script does the install.

> **Vault has no login of its own.** The Caddy password below is the only lock. Never open ports 7000–7107 in the security group.

---

## Timeline

| Minutes | Step |
|---|---|
| 0–3 | Make a read-only GitHub token (§1) |
| 3–8 | Launch the instance with the script (§2) |
| 8–18 | Wait for the script (about 8–10 min), watching the log (§3) |
| 18–22 | Open the URL, log in, check the demo (§4) |
| 22+ | Spare time for fixes (§5) |

---

## 1. GitHub token (the repo is private)

GitHub → Settings → Developer settings → **Fine-grained tokens** → Generate:
- Repository access: **only** `anushkaa2205/vault`
- Permissions: **Contents: Read-only**
- Expiration: 1 day

Copy it. The script uses it once to clone, then removes it from the git config.

## 2. Launch the instance (EC2 console → Launch instance)

| Field | Value |
|---|---|
| Name | `vault-demo` |
| AMI | **Ubuntu Server 24.04 LTS**, 64-bit (x86) |
| Instance type | **c7i.xlarge** (not `t3`: burst credits run out and slow the heartbeats) |
| Key pair | yours (only for SSH debugging) |
| Network | default VPC, **Auto-assign public IP: Enable** |
| Security group | New: **HTTP 80 from Anywhere**, **HTTPS 443 from Anywhere**, **SSH 22 from My IP** |
| Storage | **40 GiB gp3** |
| Advanced details → **User data** | paste the script below, with the **three values at the top** filled in |

```bash
#!/bin/bash
# Vault demo bootstrap for Ubuntu 24.04 (runs once, as root). Log: /var/log/cloud-init-output.log
set -euxo pipefail
GH_TOKEN="PASTE_READ_ONLY_TOKEN"
DASH_USER="team"
DASH_PASS="PICK_A_PASSWORD"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y git python3.12-venv caddy curl
curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
apt-get install -y nodejs

# Code
mkdir -p /opt/vault && chown ubuntu:ubuntu /opt/vault
sudo -H -u ubuntu git clone "https://x-access-token:${GH_TOKEN}@github.com/anushkaa2205/vault.git" /opt/vault
cd /opt/vault
sudo -H -u ubuntu git remote set-url origin https://github.com/anushkaa2205/vault.git   # token off disk

# Backend
sudo -H -u ubuntu python3.12 -m venv .venv
sudo -H -u ubuntu .venv/bin/pip install -r requirements.txt
sudo -H -u ubuntu .venv/bin/pip install -e . --no-deps

# Dashboard: same-origin paths through Caddy
sudo -H -u ubuntu tee web/.env.production.local >/dev/null <<'EOF'
NEXT_PUBLIC_META_URL=/api/meta
NEXT_PUBLIC_GATEWAY_URL=/api/gw
NEXT_PUBLIC_SUPERVISOR_URL=/api/sup
NEXT_PUBLIC_ORACLE_URL=/api/oracle
EOF
(cd web && sudo -H -u ubuntu npm ci && sudo -H -u ubuntu npm run build)

# Services
cat > /etc/systemd/system/vault.service <<'EOF'
[Unit]
Description=Vault cluster
After=network-online.target
[Service]
User=ubuntu
WorkingDirectory=/opt/vault
Environment=VAULT_DEMO=1
ExecStart=/opt/vault/.venv/bin/python -m vault up
Restart=on-failure
KillMode=control-group
[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/vault-web.service <<'EOF'
[Unit]
Description=Vault dashboard
After=vault.service
[Service]
User=ubuntu
WorkingDirectory=/opt/vault/web
ExecStart=/usr/bin/npm start -- -H 127.0.0.1 -p 3000
Restart=on-failure
[Install]
WantedBy=multi-user.target
EOF

# HTTPS without a domain: <public-ip-with-dashes>.sslip.io resolves to this instance
PUBLIC_IP=$(curl -s https://checkip.amazonaws.com | tr -d '[:space:]')
HOST="$(echo "$PUBLIC_IP" | tr . -).sslip.io"
HASH=$(caddy hash-password --plaintext "$DASH_PASS")
cat > /etc/caddy/Caddyfile <<EOF
${HOST} {
	basicauth {
		${DASH_USER} ${HASH}
	}
	handle_path /api/meta/* {
		reverse_proxy 127.0.0.1:7000 {
			flush_interval -1
		}
	}
	handle_path /api/gw/* {
		reverse_proxy 127.0.0.1:7080
	}
	handle_path /api/sup/* {
		reverse_proxy 127.0.0.1:7070
	}
	handle_path /api/oracle/* {
		reverse_proxy 127.0.0.1:7090 {
			flush_interval -1
		}
	}
	handle {
		reverse_proxy 127.0.0.1:3000
	}
}
EOF

systemctl daemon-reload
systemctl enable --now vault vault-web
systemctl restart caddy

# Seed the demo: 6 machines, 200 files
sleep 20
sudo -H -u ubuntu /opt/vault/.venv/bin/python -m vault reset || true
echo "https://${HOST}/console" | tee /opt/vault/URL.txt
```

Click **Launch instance**.

## 3. Watch it come up

- EC2 → the instance → **Connect → EC2 Instance Connect** (or SSH as `ubuntu`).
- `tail -f /var/log/cloud-init-output.log` until you see the `https://…sslip.io/console` line (about 8–10 min; most of it is `npm ci` and `npm run build`).
- The URL is also in `/opt/vault/URL.txt`. It is your public IP with dots turned into dashes, for example `https://13-234-56-78.sslip.io/console`.

## 4. Check the demo (browser)

Open the URL, log in with `team` / your password, and check:

- [ ] "Your data is safe. 6 of 6 machines are healthy.", **no** "Sample data" badge, effective copies **3**, 200 files
- [ ] Turn off → Lab Laptop → it goes down, gets rebuilt, and the recovery-time tile fills in (about 15 s)
- [ ] Cut cable → Vault index ↔ Records Room → relay badge, **0** repairs → Clear
- [ ] Files: upload a file, open it, download it
- [ ] Before presenting: Settings → **Reset demo**

## 5. If something is wrong

| Symptom | Fix |
|---|---|
| Browser: certificate error | sslip.io hit a Let's Encrypt limit. Quick fallback: in `/etc/caddy/Caddyfile` change the first line to `:80 {`, run `sudo systemctl restart caddy`, and use `http://<public-ip>/console` (the password is then sent unencrypted: demo only) |
| Page loads, top bar says "Vault isn't running" | `sudo systemctl status vault` · `tail -50 /opt/vault/logs/meta.log` · `sudo systemctl restart vault` then `cd /opt/vault && .venv/bin/python -m vault reset` |
| Dashboard 502 | `sudo systemctl status vault-web` · `journalctl -u vault-web -n 50` |
| Script stopped halfway | `grep -n -i error /var/log/cloud-init-output.log`; fix, then run the remaining lines by hand as `sudo` |
| Timeline full of "answering slowly" | Instance too small or out of CPU credits: use `c7i.xlarge` |
| Need a code update | `cd /opt/vault && git pull && (cd web && npm ci && npm run build) && sudo systemctl restart vault vault-web` (`git pull` needs the token again: `git pull https://x-access-token:<token>@github.com/anushkaa2205/vault.git main`) |

## 6. Vercel backup (dashboard only)

The Vercel copy of `web/` has **no backend**. After 2 seconds the dashboard switches to the built-in sample data and shows a **"Sample data"** badge, so it's honest and safe as a backup for showing the UI. Chaos buttons won't do anything there. **Don't** point the Vercel build at the AWS backend: it would be cross-origin behind a password, and the live stream won't work that way. For the live demo, use the AWS URL.

## 7. After the demo

EC2 → **Terminate** the instance (the volume is deleted with it), and **delete the GitHub token**. Don't just stop the instance: the public IP changes on restart, and the sslip.io address with it.
