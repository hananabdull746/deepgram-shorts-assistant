#!/usr/bin/env bash
set -Eeuo pipefail
APP_NAME="clipcue"
APP_USER="clipcue"
APP_GROUP="clipcue"
INSTALL_DIR="/opt/clipcue"
DATA_DIR="/var/lib/clipcue"
LOG_DIR="/var/log/clipcue"
ENV_DIR="/etc/clipcue"
ENV_FILE="$ENV_DIR/clipcue.env"
SERVICE_FILE="/etc/systemd/system/clipcue.service"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${EUID}" -ne 0 ]]; then echo "Run with sudo: sudo bash ./install.sh" >&2; exit 1; fi
if [[ ! -f "$SOURCE_DIR/requirements.txt" || ! -d "$SOURCE_DIR/app" ]]; then echo "Run this installer from a complete ClipCue repository clone." >&2; exit 1; fi
if [[ ! -r /etc/os-release ]]; then echo "Cannot detect Linux distribution." >&2; exit 1; fi
. /etc/os-release
if [[ "${ID:-}" != "ubuntu" || ! "${VERSION_ID:-}" =~ ^(22\.04|24\.04)$ ]]; then echo "Supported operating systems: Ubuntu 22.04 and 24.04. Detected: ${PRETTY_NAME:-unknown}." >&2; exit 1; fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip ffmpeg fonts-dejavu-core curl ca-certificates
if ! getent group "$APP_GROUP" >/dev/null; then groupadd --system "$APP_GROUP"; fi
if ! id "$APP_USER" >/dev/null 2>&1; then useradd --system --gid "$APP_GROUP" --home-dir "$DATA_DIR" --create-home --shell /usr/sbin/nologin "$APP_USER"; fi
install -d -o root -g root -m 0755 "$INSTALL_DIR"
install -d -o "$APP_USER" -g "$APP_GROUP" -m 0750 "$DATA_DIR" "$DATA_DIR/uploads" "$DATA_DIR/projects"
install -d -o "$APP_USER" -g "$APP_GROUP" -m 0750 "$LOG_DIR"
install -d -o root -g "$APP_GROUP" -m 0750 "$ENV_DIR"
tar --exclude='./.git' --exclude='./.venv' --exclude='./venv' --exclude='./data' --exclude='./.pytest_cache' \
  -C "$SOURCE_DIR" -cf - . | tar -C "$INSTALL_DIR" -xf -
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

GENERATED_PASSWORD=""
if [[ ! -f "$ENV_FILE" ]]; then
  GENERATED_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"
  SESSION_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  cat > "$ENV_FILE" <<EOF
APP_HOST=127.0.0.1
APP_PORT=8090
DATA_DIR=$DATA_DIR
DATABASE_PATH=$DATA_DIR/clipcue.sqlite3
ADMIN_USERNAME=admin
ADMIN_PASSWORD=$GENERATED_PASSWORD
SESSION_SECRET=$SESSION_SECRET
COOKIE_SECURE=false
DEEPGRAM_API_KEY=
DEEPGRAM_MODEL=nova-3
DEEPGRAM_LANGUAGE=en
DEEPGRAM_TIMEOUT_SECONDS=570
PUBLIC_BASE_URL=
MAX_UPLOAD_BYTES=2147483648
MAX_VIDEO_SECONDS=10800
MAX_VIDEO_PIXELS=2073600
MAX_CLIPS_PER_PROJECT=12
EOF
  chmod 0640 "$ENV_FILE"
  chown root:"$APP_GROUP" "$ENV_FILE"
fi
install -m 0644 "$INSTALL_DIR/deploy/clipcue.service" "$SERVICE_FILE"
chown -R root:root "$INSTALL_DIR"
chmod -R u=rwX,go=rX "$INSTALL_DIR"
chown -R "$APP_USER":"$APP_GROUP" "$DATA_DIR" "$LOG_DIR"
systemctl daemon-reload
systemctl enable --now "$APP_NAME.service"
systemctl restart "$APP_NAME.service"
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8090/health >/dev/null 2>&1; then break; fi
  sleep 1
done
if ! curl -fsS http://127.0.0.1:8090/health; then echo "ClipCue did not become healthy. Inspect: sudo journalctl -u $APP_NAME -n 100 --no-pager" >&2; exit 1; fi
printf '\nClipCue installed and running loopback-only on port 8090.\n'
printf 'Remote workstation tunnel: ssh -L 8090:127.0.0.1:8090 ubuntu@<server-ip>\n'
if [[ -n "$GENERATED_PASSWORD" ]]; then
  printf 'Initial login username: admin\nInitial login password: %s\nSave this password now; it will not be printed again.\n' "$GENERATED_PASSWORD"
else
  printf 'Existing environment file preserved: %s\n' "$ENV_FILE"
fi
printf 'Set DEEPGRAM_API_KEY in %s, then restart: sudo systemctl restart clipcue\n' "$ENV_FILE"
printf 'No public firewall port is opened by this installer.\n'
