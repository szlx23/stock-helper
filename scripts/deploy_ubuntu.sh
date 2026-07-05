#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-stock-helper}"
APP_DIR="${APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
APP_USER="${APP_USER:-${SUDO_USER:-$(id -un)}}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8501}"
DB_DIR="${DB_DIR:-/var/lib/stock-helper}"
DB_PATH="${DB_PATH:-${DB_DIR}/stock_helper.db}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run with sudo:"
  echo "  sudo bash scripts/deploy_ubuntu.sh"
  exit 1
fi

if [ ! -f "${APP_DIR}/pyproject.toml" ]; then
  echo "pyproject.toml not found in ${APP_DIR}"
  echo "Run this script from the cloned stock-helper repository, or set APP_DIR=/path/to/repo."
  exit 1
fi

echo "Deploying ${SERVICE_NAME}"
echo "APP_DIR=${APP_DIR}"
echo "APP_USER=${APP_USER}"
echo "PORT=${PORT}"
echo "DB_PATH=${DB_PATH}"

apt-get update
apt-get install -y git python3 python3-venv python3-pip

mkdir -p "${DB_DIR}"
chown -R "${APP_USER}:${APP_USER}" "${DB_DIR}"
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"

sudo -u "${APP_USER}" "${PYTHON_BIN}" -m venv "${APP_DIR}/.venv"
sudo -u "${APP_USER}" "${APP_DIR}/.venv/bin/python" -m pip install --upgrade pip
sudo -u "${APP_USER}" "${APP_DIR}/.venv/bin/python" -m pip install -e "${APP_DIR}"

cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<SERVICE
[Unit]
Description=A-share stock helper web app
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
Environment=STOCK_HELPER_DB=${DB_PATH}
Environment=TZ=Asia/Shanghai
ExecStart=${APP_DIR}/.venv/bin/uvicorn stock_helper.app:app --host ${HOST} --port ${PORT}
Restart=always
RestartSec=5
TimeoutStopSec=30
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=${DB_DIR}

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

echo
echo "Deployment finished."
echo "Service status:"
systemctl --no-pager --full status "${SERVICE_NAME}" || true
echo
echo "Open: http://SERVER_IP:${PORT}"
echo "Health: http://SERVER_IP:${PORT}/healthz"
echo "Logs: sudo journalctl -u ${SERVICE_NAME} -f"
