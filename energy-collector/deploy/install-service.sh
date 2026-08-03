#!/usr/bin/env bash
# Install energy-collector sebagai systemd service di Linux (Raspberry Pi).
# Usage:
#   sudo ./deploy/install-service.sh
#   sudo ./deploy/install-service.sh /opt/energy-collector pi
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_DIR="${1:-$ROOT}"
RUN_USER="${2:-${SUDO_USER:-pi}}"
UNIT_NAME="energy-collector"
UNIT_SRC="$(cd "$(dirname "$0")" && pwd)/energy-collector.service"
UNIT_DST="/etc/systemd/system/${UNIT_NAME}.service"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Jalankan dengan sudo." >&2
  exit 1
fi

if [[ ! -x "${INSTALL_DIR}/.venv/bin/python" ]]; then
  echo "Venv belum ada di ${INSTALL_DIR}/.venv — buat dulu:" >&2
  echo "  cd ${INSTALL_DIR} && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

if [[ ! -f "${INSTALL_DIR}/.env" ]]; then
  echo "Peringatan: ${INSTALL_DIR}/.env belum ada. Salin dari .env.example lalu edit." >&2
fi

# Pastikan user di grup gpio (untuk RPi)
if getent group gpio >/dev/null 2>&1; then
  usermod -aG gpio "$RUN_USER" || true
fi

sed \
  -e "s|/opt/energy-collector|${INSTALL_DIR}|g" \
  -e "s|^User=.*|User=${RUN_USER}|" \
  -e "s|^Group=.*|Group=${RUN_USER}|" \
  "$UNIT_SRC" > "$UNIT_DST"

systemctl daemon-reload
systemctl enable "$UNIT_NAME"
systemctl restart "$UNIT_NAME"
systemctl --no-pager --full status "$UNIT_NAME" || true

echo
echo "OK. Perintah berguna:"
echo "  sudo systemctl status ${UNIT_NAME}"
echo "  sudo journalctl -u ${UNIT_NAME} -f"
echo "  sudo systemctl restart ${UNIT_NAME}"
echo "  sudo systemctl stop ${UNIT_NAME}"
