#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then
  echo "Запусти без sudo: bash install.sh"
  echo "Скрипт сам вызовет sudo только там, где это нужно."
  exit 1
fi

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_USER="$USER"

sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip ffmpeg
sudo apt-get install -y chromium-browser || sudo apt-get install -y chromium || true

python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip wheel
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"
"$APP_DIR/.venv/bin/playwright" install chromium || true

mkdir -p "$APP_DIR/cookies" "$APP_DIR/data/work"
[ -f "$APP_DIR/config.yaml" ] || cp "$APP_DIR/config.example.yaml" "$APP_DIR/config.yaml"

cat <<EOF | sudo tee /etc/systemd/system/tiktoker.service >/dev/null
[Unit]
Description=TikToker worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/python $APP_DIR/main.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable tiktoker

echo
echo "Готово."
echo "1) nano $APP_DIR/config.yaml"
echo "2) положи cookies-файлы в $APP_DIR/cookies/"
echo "3) sudo systemctl start tiktoker"
echo "4) journalctl -u tiktoker -f"
