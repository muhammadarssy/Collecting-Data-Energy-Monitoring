#!/usr/bin/env bash
# Install energy-collector sebagai systemd service (Raspberry Pi atau mini PC Linux).
# Usage:
#   sudo ./deploy/install-service.sh
#   sudo ./deploy/install-service.sh /opt/energy-collector
#   sudo ./deploy/install-service.sh /opt/energy-collector nama_user
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_DIR="${1:-$ROOT}"
UNIT_NAME="energy-collector"
UNIT_DST="/etc/systemd/system/${UNIT_NAME}.service"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Jalankan dengan sudo." >&2
  exit 1
fi

if [[ ! -d "$INSTALL_DIR" ]]; then
  echo "Direktori tidak ada: ${INSTALL_DIR}" >&2
  exit 1
fi

if [[ ! -f "${INSTALL_DIR}/main.py" ]]; then
  echo "main.py tidak ada di ${INSTALL_DIR}" >&2
  exit 1
fi

# User: arg 2 → SUDO_USER → pemilik folder. Jangan default 'pi' (gagal di mini PC).
if [[ -n "${2:-}" ]]; then
  RUN_USER="$2"
elif [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
  RUN_USER="$SUDO_USER"
else
  RUN_USER="$(stat -c '%U' "$INSTALL_DIR")"
fi

if ! id "$RUN_USER" >/dev/null 2>&1; then
  echo "User '${RUN_USER}' tidak ada." >&2
  exit 1
fi

RUN_GROUP="$(id -gn "$RUN_USER")"

if [[ ! -x "${INSTALL_DIR}/.venv/bin/python" ]]; then
  echo "Venv belum ada di ${INSTALL_DIR}/.venv — buat dulu:" >&2
  echo "  sudo -u ${RUN_USER} python3 -m venv ${INSTALL_DIR}/.venv" >&2
  echo "  sudo -u ${RUN_USER} ${INSTALL_DIR}/.venv/bin/pip install -r ${INSTALL_DIR}/requirements.txt" >&2
  exit 1
fi

if [[ ! -f "${INSTALL_DIR}/.env" ]]; then
  echo "Peringatan: ${INSTALL_DIR}/.env belum ada. Salin dari .env.example lalu edit." >&2
fi

# gpio = Raspberry Pi; dialout = USB serial (Pi & mini PC)
SUPP=()
for g in gpio dialout; do
  if getent group "$g" >/dev/null 2>&1; then
    usermod -aG "$g" "$RUN_USER" || true
    SUPP+=("$g")
  fi
done

SUPP_LINE=""
if [[ ${#SUPP[@]} -gt 0 ]]; then
  SUPP_LINE="SupplementaryGroups=${SUPP[*]}"
fi

unit_quote() {
  local s=$1
  s=${s//\\/\\\\}
  s=${s//\"/\\\"}
  printf '"%s"' "$s"
}

WD="$(unit_quote "$INSTALL_DIR")"
PY="$(unit_quote "${INSTALL_DIR}/.venv/bin/python")"
MAIN="$(unit_quote "${INSTALL_DIR}/main.py")"
ENVF="-$(unit_quote "${INSTALL_DIR}/.env")"

cat > "$UNIT_DST" <<EOF
[Unit]
Description=Energy Meter Data Collector
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_GROUP}
${SUPP_LINE}

WorkingDirectory=${WD}
EnvironmentFile=${ENVF}
ExecStart=${PY} ${MAIN}

Restart=on-failure
RestartSec=5
TimeoutStopSec=30

StandardOutput=journal
StandardError=journal
SyslogIdentifier=energy-collector

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$UNIT_NAME"
systemctl restart "$UNIT_NAME"

echo
if systemctl is-active --quiet "$UNIT_NAME"; then
  echo "OK. Service ${UNIT_NAME} aktif (user=${RUN_USER} group=${RUN_GROUP})."
else
  echo "Service gagal start. Log terakhir:" >&2
  journalctl -u "$UNIT_NAME" -n 50 --no-pager >&2 || true
  systemctl --no-pager --full status "$UNIT_NAME" >&2 || true
  exit 1
fi

echo "  sudo systemctl status ${UNIT_NAME}"
echo "  sudo journalctl -u ${UNIT_NAME} -f"
echo "  sudo systemctl restart ${UNIT_NAME}"
echo "  sudo systemctl stop ${UNIT_NAME}"
