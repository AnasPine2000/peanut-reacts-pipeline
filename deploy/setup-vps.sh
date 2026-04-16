#!/usr/bin/env bash
# One-shot setup script for a fresh Linux VPS
# Tested on Ubuntu 22.04 / 24.04 LTS, Debian 12, Oracle Linux 9
#
# Usage on a fresh VPS:
#   curl -fsSL https://raw.githubusercontent.com/YOUR_REPO/main/deploy/setup-vps.sh | sudo bash
# OR after cloning the repo:
#   cd peanut-reacts/deploy && sudo bash setup-vps.sh
#
# After this completes:
#   1. Edit /opt/peanut-reacts/.env with your API keys
#   2. Place client_secret.json in /opt/peanut-reacts/secrets/
#   3. Run: cd /opt/peanut-reacts && docker compose up -d
#   4. Check logs: docker compose logs -f scheduler

set -euo pipefail

INSTALL_DIR="/opt/peanut-reacts"
REPO_URL="${REPO_URL:-}"

echo "════════════════════════════════════════════════════════"
echo "   PEANUT REACTS PIPELINE - VPS SETUP"
echo "════════════════════════════════════════════════════════"

# 1. Update system + install base packages
echo "[1/6] Updating system packages..."
if command -v apt-get &> /dev/null; then
    apt-get update -y
    apt-get install -y \
        ca-certificates curl gnupg lsb-release \
        git ufw fail2ban tmux htop \
        unattended-upgrades
elif command -v dnf &> /dev/null; then
    dnf -y update
    dnf -y install ca-certificates curl gnupg git tmux htop firewalld fail2ban
fi

# 2. Install Docker (official script)
echo "[2/6] Installing Docker..."
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | sh
    systemctl enable --now docker
fi

# 3. Install Docker Compose plugin if missing
if ! docker compose version &> /dev/null; then
    apt-get install -y docker-compose-plugin || true
fi

# 4. Create install directory + non-root user
echo "[3/6] Creating install directory..."
mkdir -p "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR/tokens"
mkdir -p "$INSTALL_DIR/secrets"
mkdir -p "$INSTALL_DIR/logs"

if ! id -u peanut &> /dev/null; then
    useradd -r -s /bin/bash -d "$INSTALL_DIR" peanut
    usermod -aG docker peanut
fi

# 5. Clone or update the repo
echo "[4/6] Fetching pipeline code..."
if [[ -n "$REPO_URL" ]]; then
    if [[ -d "$INSTALL_DIR/.git" ]]; then
        cd "$INSTALL_DIR" && git pull
    else
        git clone "$REPO_URL" "$INSTALL_DIR"
    fi
else
    echo "  (REPO_URL not set, skipping git clone — assuming code is already in $INSTALL_DIR)"
fi

# 6. Set up firewall
echo "[5/6] Configuring firewall..."
if command -v ufw &> /dev/null; then
    ufw --force enable
    ufw allow 22/tcp     # SSH
    ufw allow 7860/tcp   # Gradio dashboard
    ufw default deny incoming
    ufw default allow outgoing
elif command -v firewall-cmd &> /dev/null; then
    firewall-cmd --permanent --add-port=22/tcp
    firewall-cmd --permanent --add-port=7860/tcp
    firewall-cmd --reload
fi

# 7. Set up automatic security updates
if command -v unattended-upgrades &> /dev/null; then
    dpkg-reconfigure -plow unattended-upgrades || true
fi

# 8. Create .env from template if missing
if [[ ! -f "$INSTALL_DIR/.env" && -f "$INSTALL_DIR/deploy/.env.example" ]]; then
    cp "$INSTALL_DIR/deploy/.env.example" "$INSTALL_DIR/.env"
    chmod 600 "$INSTALL_DIR/.env"
    echo "  Created $INSTALL_DIR/.env from template — edit with your keys!"
fi

chown -R peanut:peanut "$INSTALL_DIR"

echo "[6/6] Done."
echo "════════════════════════════════════════════════════════"
echo "  NEXT STEPS:"
echo "  1. Edit env: sudo nano $INSTALL_DIR/.env"
echo "  2. Add OAuth: sudo cp ~/client_secret.json $INSTALL_DIR/secrets/"
echo "  3. Add token: sudo cp ~/youtube_token.json $INSTALL_DIR/tokens/"
echo "  4. Build + run: cd $INSTALL_DIR && sudo docker compose -f deploy/docker-compose.yml up -d"
echo "  5. Check logs: sudo docker compose -f deploy/docker-compose.yml logs -f scheduler"
echo "  6. Dashboard: http://YOUR_VPS_IP:7860"
echo "════════════════════════════════════════════════════════"
