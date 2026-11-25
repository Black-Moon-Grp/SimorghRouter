#!/usr/bin/env bash
set -euo pipefail

# V2Ray Smart Router - One-Click Ubuntu/Debian Installer
if [[ $EUID -ne 0 ]]; then
  echo "Error: Please run as root (sudo bash deploy/install.sh)"
  exit 1
fi

APP_DIR="/opt/v2ray-router"
DATA_DIR="/var/lib/v2ray-router"
ASSETS_DIR="/usr/local/share/xray"
XRAY_BIN="/usr/local/bin/xray"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "[1/6] Installing system dependencies..."
apt-get update -y
apt-get install -y python3 python3-venv python3-pip curl unzip ufw ca-certificates

echo "[2/6] Installing latest Xray-core (Linux x64)..."
TMP_XRAY="$(mktemp -d)"
curl -fsSL -o "${TMP_XRAY}/xray.zip" \
  "https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip"
unzip -o "${TMP_XRAY}/xray.zip" xray -d /usr/local/bin/
chmod +x "${XRAY_BIN}"
rm -rf "${TMP_XRAY}"

echo "[3/6] Downloading GeoIP and Geosite databases..."
mkdir -p "${ASSETS_DIR}" "${DATA_DIR}"
curl -fsSL -o "${ASSETS_DIR}/geoip.dat" \
  "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geoip.dat"
curl -fsSL -o "${ASSETS_DIR}/geosite.dat" \
  "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geosite.dat"

echo "[4/6] Setting up application and Python virtual environment..."
mkdir -p "${APP_DIR}"
cp -r "${SCRIPT_DIR}/app" "${APP_DIR}/"
cp "${SCRIPT_DIR}/requirements.txt" "${APP_DIR}/"

if [[ ! -f "${APP_DIR}/.env" ]]; then
  cp "${SCRIPT_DIR}/.env.example" "${APP_DIR}/.env"
  SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
  sed -i "s|change-this-to-a-long-random-secret-in-production|${SECRET}|g" "${APP_DIR}/.env"
fi

python3 -m venv "${APP_DIR}/venv"
"${APP_DIR}/venv/bin/pip" install --upgrade pip
"${APP_DIR}/venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

echo "[5/6] Configuring UFW firewall..."
ufw allow OpenSSH || true
ufw allow 8000/tcp comment "V2Ray Router Admin Panel" || true
ufw allow 443/tcp comment "VLESS TCP Inbound" || true
ufw allow 8443/tcp comment "VLESS WS Inbound" || true
ufw allow 2083/tcp comment "Trojan Inbound" || true
ufw --force enable || true

echo "[6/6] Installing and starting systemd service..."
cp "${SCRIPT_DIR}/deploy/v2ray-router.service" /etc/systemd/system/v2ray-router.service
systemctl daemon-reload
systemctl enable v2ray-router.service
systemctl restart v2ray-router.service

PUBLIC_IP=$(curl -fsSL https://api.ipify.org || echo "<SERVER_IP>")
echo "=========================================================="
echo " Installation complete!"
echo " Admin Panel: http://${PUBLIC_IP}:8000"
echo " Default Login: admin / admin123 (CHANGE IMMEDIATELY!)"
echo " Service logs: journalctl -u v2ray-router -f"
echo "=========================================================="
