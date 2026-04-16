# Cloud Deployment Guide — Make the pipeline independent of your local PC

**Last Updated:** 2026-04-15
**Goal:** Run the entire Peanut Reacts pipeline 24/7 on a cloud server, with zero dependency on your home machine being online.

---

## TL;DR Recommendation

**Primary pick: Hetzner CPX31 in Falkenstein/Helsinki — €15/month (~$16)**
- 4 dedicated AMD EPYC vCPU
- 8 GB RAM
- 160 GB NVMe SSD
- **20 TB included egress** (we need ~500 GB/month — massive headroom)
- Ubuntu 24.04 LTS
- 99.9% uptime SLA
- 5-minute setup with the script in `deploy/setup-vps.sh`

**Free alternative: Oracle Cloud Always Free (Ampere A1 ARM)**
- 4 OCPU / 24 GB RAM / 200 GB / 10 TB egress — **$0 forever**
- Catch: A1 capacity is hard to get in popular regions; try Phoenix, London, Mumbai
- Use as a hot standby or migrate after Hetzner is stable

**Avoid:** AWS, GCP, Azure (egress fees would add $40-90/month). Hugging Face Spaces (no persistent daemon support). Railway/Render/Fly.io (bandwidth fees). RunPod (overkill for non-GPU work).

---

## Why move off your local machine

| Issue | Cloud fixes it |
|-------|----------------|
| Pipeline pauses when you close the laptop | VPS runs 24/7, never sleeps |
| YouTube live stream dies if your PC reboots | VPS reboots restart automatically via systemd/Docker restart policy |
| Local IP rate-limited by Twitch/YouTube | Cloud datacenter IPs have higher rate ceilings |
| Storage fills up your local SSD | 160 GB on VPS, separate from your computer |
| Power outages = pipeline downtime | Cloud has redundant power |
| Need a single screen for monitoring | Dashboard accessible at `http://VPS_IP:7860` from any browser |

---

## Cost comparison (April 2026)

| Provider | Spec | Monthly | Egress fees | Verdict |
|----------|------|---------|-------------|---------|
| **Hetzner CPX31 (EU)** | 4 vCPU / 8 GB / 160 GB | **€15** | 20 TB included | **Best paid pick** |
| **Hetzner CAX21 ARM (EU)** | 4 vCPU / 8 GB / 80 GB | **€7** | 20 TB included | Cheapest stable EU |
| **Oracle Always Free** | 4 OCPU / 24 GB / 200 GB | **$0** | 10 TB free | **Best if you get capacity** |
| **Contabo VPS S** | 4 vCPU / 8 GB / 100 GB NVMe | **$8** | 32 TB soft cap | Cheapest, less reliable |
| **DigitalOcean Basic** | 4 vCPU / 8 GB / 160 GB | $48 | 5 TB | Overpriced vs Hetzner |
| **AWS EC2 t3.medium** | 2 vCPU / 4 GB | $30 + storage | **$40-45 egress** | Skip — egress kills it |
| **Railway** | 2 vCPU / 8 GB | $5 + usage | **$0.10/GB egress** | Skip — bandwidth surprises |

**Recommendation:** Start with Hetzner CPX31 (~$16/mo). If Oracle Free Tier gives you A1 capacity later, migrate for $0/mo.

---

## What gets deployed

The pipeline runs as 2-3 Docker containers (see `deploy/docker-compose.yml`):

1. **`scheduler`** — APScheduler daemon running all channel pipelines on cron
2. **`dashboard`** — Gradio dashboard on port 7860
3. **`livestream`** (optional) — FFmpeg RTMP loop for the 24/7 stream

Persistent state lives in named Docker volumes:
- `peanut_data` → SQLite databases (`pipeline.db`, `learning.db`)
- `peanut_output` → Generated videos and intermediate files
- `./tokens/` → OAuth tokens (one-time setup)
- `./secrets/` → Client secrets and API keys

---

## Quick Start — Hetzner Setup (10 minutes)

### 1. Create the Hetzner account
- Sign up at [hetzner.com/cloud](https://www.hetzner.com/cloud)
- Add a payment method
- Create a new project: "peanut-reacts"

### 2. Create a CPX31 server
- Location: Falkenstein, Nuremberg, or Helsinki (EU = 20 TB egress)
- Image: Ubuntu 24.04 LTS
- Type: **CPX31** (4 vCPU / 8 GB / 160 GB / €15.46/mo)
- SSH key: upload your public key
- Name: `peanut-prod-01`
- Click **Create & Buy now**

You'll get an IP address like `5.78.123.45` within 30 seconds.

### 3. Connect via SSH
```bash
ssh root@5.78.123.45
```

### 4. Run the setup script
```bash
# On the VPS:
curl -fsSL https://YOUR_REPO/deploy/setup-vps.sh | sudo bash

# OR if you push the repo to GitHub first:
git clone https://github.com/YOUR_USERNAME/peanut-reacts /opt/peanut-reacts
cd /opt/peanut-reacts/deploy
sudo bash setup-vps.sh
```

This installs Docker, sets up firewall, creates the `peanut` user, and prepares directories.

### 5. Transfer your secrets and tokens

From your **local PC** (in PowerShell or WSL):
```bash
# Copy OAuth client secret
scp ~/Downloads/client_secret_*.json root@5.78.123.45:/opt/peanut-reacts/secrets/client_secret.json

# Copy YouTube refresh token (already authenticated locally)
scp ~/.peanut_reacts/youtube_token.json root@5.78.123.45:/opt/peanut-reacts/tokens/youtube_token.json

# Copy your .env
scp .env root@5.78.123.45:/opt/peanut-reacts/.env
```

On the VPS, fix permissions:
```bash
sudo chown -R peanut:peanut /opt/peanut-reacts
sudo chmod 600 /opt/peanut-reacts/secrets/* /opt/peanut-reacts/.env
```

### 6. Start the containers
```bash
cd /opt/peanut-reacts
sudo docker compose -f deploy/docker-compose.yml up -d --build
```

Wait ~3 minutes for the Docker image to build (one-time).

### 7. Verify it's running
```bash
sudo docker compose -f deploy/docker-compose.yml logs -f scheduler
```

You should see:
```
[INFO] Loaded 6 channels from channels.yaml
[INFO] Database ready at /data/pipeline.db
[INFO] Scheduled [uk_clips]: 0 9 * * 1,3,5
[INFO] Scheduled [hasanabi_archive]: 0 10 * * *
...
[INFO] Pipeline scheduler started. 6 channels registered.
```

### 8. Open the dashboard
Browse to `http://5.78.123.45:7860` — your Gradio dashboard is live on the cloud.

---

## OAuth migration (the critical step)

The trickiest part: YouTube OAuth tokens have a refresh mechanism that needs to work from the cloud IP.

### Option A: Pre-authenticate locally, copy the token
This is what the quickstart does. Works because the refresh token is portable across machines.

### Option B: Re-authenticate on the VPS using a tunnel
If Option A fails (refresh token is sometimes IP-bound):

```bash
# On your local PC:
ssh -L 8080:localhost:8080 root@VPS_IP

# Then on the VPS:
sudo -u peanut bash
cd /opt/peanut-reacts
PYTHONPATH=src python -c "
from pathlib import Path
from peanut_reacts.upload.youtube_auth import get_authenticated_service
service = get_authenticated_service(
    Path('/opt/peanut-reacts/secrets/client_secret.json'),
    token_path=Path('/opt/peanut-reacts/tokens/youtube_token.json'),
)
"
```

The local browser opens, you authorize, the token saves to the VPS.

---

## Day-to-day operations

### View live logs
```bash
sudo docker compose -f deploy/docker-compose.yml logs -f scheduler
```

### Trigger a one-shot run
```bash
sudo docker compose -f deploy/docker-compose.yml exec scheduler \
    python -m peanut_reacts.scheduler.runner --run-once uk_clips
```

### Update the pipeline code
```bash
cd /opt/peanut-reacts
sudo git pull
sudo docker compose -f deploy/docker-compose.yml up -d --build
```

### Check disk usage
```bash
sudo docker system df
sudo du -sh /var/lib/docker/volumes/peanut-reacts_peanut_output
```

### Rotate logs (cron job)
The scheduler logs go to stdout (captured by Docker). Set up log rotation:
```bash
sudo tee /etc/docker/daemon.json <<EOF
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "100m",
    "max-file": "5"
  }
}
EOF
sudo systemctl restart docker
```

---

## Hardening (production checklist)

- [ ] SSH key only (disable password auth: `PasswordAuthentication no` in `/etc/ssh/sshd_config`)
- [ ] UFW firewall enabled (only 22 + 7860 open) — done by setup script
- [ ] Fail2ban active for SSH brute-force protection — done by setup script
- [ ] Docker images updated regularly (`docker compose pull && docker compose up -d`)
- [ ] Automatic security updates enabled (`unattended-upgrades`)
- [ ] Off-site backup of `tokens/`, `secrets/`, `pipeline.db`, `learning.db`
- [ ] Discord webhook configured for failure alerts
- [ ] Restart-on-failure tested (`sudo systemctl restart docker`)

---

## Backup strategy

Critical state to back up weekly:
- `/opt/peanut-reacts/tokens/` — YouTube OAuth tokens
- `/opt/peanut-reacts/secrets/` — Client secrets + API keys
- `/var/lib/docker/volumes/peanut-reacts_peanut_data/_data/pipeline.db`
- `/var/lib/docker/volumes/peanut-reacts_peanut_data/_data/learning.db`
- `/opt/peanut-reacts/channels.yaml` — Channel configuration

Quick backup script (cron weekly):
```bash
#!/usr/bin/env bash
BACKUP_DIR="/opt/peanut-reacts/backups/$(date +%Y%m%d)"
mkdir -p "$BACKUP_DIR"
cp -r /opt/peanut-reacts/tokens "$BACKUP_DIR/"
cp -r /opt/peanut-reacts/secrets "$BACKUP_DIR/"
cp /opt/peanut-reacts/channels.yaml "$BACKUP_DIR/"
sudo docker run --rm -v peanut-reacts_peanut_data:/data -v "$BACKUP_DIR":/backup alpine \
    tar czf /backup/data.tar.gz /data
# Upload to S3/B2/Backblaze for off-site
```

---

## Scaling later (when you outgrow CPX31)

| Stage | Upgrade path |
|-------|------------|
| 5+ channels at full scale | Hetzner **CPX41** (8 vCPU / 16 GB / 240 GB / €30/mo) |
| Add NVENC GPU encoding | Hetzner **EX44** dedicated server (RTX 4000 / €100/mo) |
| Multiple VPS for sharding | Use Hetzner Cloud private network |
| Object storage for big videos | Hetzner Storage Box (1 TB / €4/mo) |

---

## Files added to the repo

| File | Purpose |
|------|---------|
| `deploy/Dockerfile` | Python 3.12 + FFmpeg + the full pipeline |
| `deploy/docker-compose.yml` | 3-service stack (scheduler + dashboard + livestream) |
| `deploy/.env.example` | Environment variable template |
| `deploy/setup-vps.sh` | One-shot VPS bootstrap (Docker, firewall, user) |
| `deploy/peanut-pipeline.service` | Systemd alternative for non-Docker setups |
| `requirements.txt` | Updated with all production dependencies |

---

## Open questions to resolve before deploying

1. **Where to host the git repo?** — Public GitHub is easiest, but secrets must NOT be committed. Use private repo or push only the code (not `.env`, `tokens/`, `secrets/`).
2. **Domain name?** — Optional but `peanut.yourdomain.com` is nicer than an IP for the dashboard.
3. **HTTPS for dashboard?** — Add Caddy or Nginx as a reverse proxy with Let's Encrypt.
4. **Off-site backup target?** — Backblaze B2 is cheap (~$0.005/GB/mo) and easy.

These can all be added later — the minimum viable cloud deployment is the 8 steps above.

---

## Total cost summary

| Item | Monthly |
|------|---------|
| Hetzner CPX31 VPS | €15.46 (~$16) |
| Hetzner egress (within 20 TB free) | $0 |
| DeepSeek API (LLM, ~2K reactions/day) | $1-5 |
| Optional: fal.ai for backgrounds | $5-30 |
| Optional: Suno Pro for music | $10 |
| Optional: DistroKid (Spotify dist) | $1.92 ($23/year) |
| **TOTAL (minimum)** | **~$17/mo** |
| **TOTAL (full ambient pipeline)** | **~$60/mo** |

With ~$17/month you have:
- 24/7 autonomous reaction pipeline
- 24/7 ambient channel generation
- 24/7 livestream
- All 5 channels running on cron
- Dashboard accessible from anywhere
- Learning loop continuously improving
- Full feedback experiments tracked

This pays for itself the moment one channel hits monetization.
