#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/ReVerfyx/TikToker.git"
APP_DIR="/opt/TikToker"

if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
  APP_USER="$USER"
else
  SUDO="sudo"
  APP_USER="$USER"
fi

echo "=== TikToker: установка/обновление ==="

$SUDO apt-get update
$SUDO apt-get install -y git curl python3 python3-venv python3-pip ffmpeg ca-certificates

if [ -d "$APP_DIR/.git" ]; then
  echo "Обновляю существующий репозиторий..."
  $SUDO git -C "$APP_DIR" fetch origin main
  $SUDO git -C "$APP_DIR" reset --hard origin/main
else
  echo "Клонирую TikToker..."
  $SUDO rm -rf "$APP_DIR"
  $SUDO git clone "$REPO_URL" "$APP_DIR"
fi

$SUDO chown -R "$APP_USER":"$APP_USER" "$APP_DIR"
cd "$APP_DIR"

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi

export PIP_DEFAULT_TIMEOUT=120
export PIP_RETRIES=10

.venv/bin/python -m pip install --timeout 120 --retries 10 --upgrade pip wheel
.venv/bin/pip install --timeout 120 --retries 10 -r requirements.txt
.venv/bin/playwright install chromium

mkdir -p cookies data/work logs
chmod 700 cookies

if [ ! -f config.yaml ]; then
  cp config.example.yaml config.yaml
fi

USER_HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"
[ -n "$USER_HOME" ] || USER_HOME="/root"

cat <<EOF | $SUDO tee /etc/systemd/system/tiktoker.service >/dev/null
[Unit]
Description=TikToker worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$APP_DIR
ExecStartPre=$APP_DIR/.venv/bin/python $APP_DIR/accounts.py refresh-if-stale 24
ExecStart=$APP_DIR/.venv/bin/python $APP_DIR/main.py
Restart=always
RestartSec=10
TimeoutStartSec=20min
Environment=PYTHONUNBUFFERED=1
Environment=HOME=$USER_HOME

[Install]
WantedBy=multi-user.target
EOF

cat <<'EOF' | $SUDO tee /usr/local/bin/tiktoker-accounts >/dev/null
#!/usr/bin/env bash
cd /opt/TikToker
if [ "$#" -eq 0 ]; then
  set -- show
fi
exec /opt/TikToker/.venv/bin/python /opt/TikToker/accounts.py "$@"
EOF

cat <<'EOF' | $SUDO tee /usr/local/bin/tiktoker-import >/dev/null
#!/usr/bin/env bash
cd /opt/TikToker
exec /opt/TikToker/.venv/bin/python /opt/TikToker/import_cookies.py "$@"
EOF

cat <<'EOF' | $SUDO tee /usr/local/bin/tiktoker-comment >/dev/null
#!/usr/bin/env bash
cd /opt/TikToker
exec /opt/TikToker/.venv/bin/python /opt/TikToker/comment.py "$@"
EOF

cat <<'EOF' | $SUDO tee /usr/local/bin/tiktoker-verify >/dev/null
#!/usr/bin/env bash
cd /opt/TikToker
exec /opt/TikToker/.venv/bin/python /opt/TikToker/manual_verify.py "$@"
EOF


$SUDO chmod +x /usr/local/bin/tiktoker-accounts /usr/local/bin/tiktoker-import /usr/local/bin/tiktoker-comment /usr/local/bin/tiktoker-verify
$SUDO systemctl daemon-reload
$SUDO systemctl enable tiktoker

echo
echo "========================================"
echo "TikToker установлен: $APP_DIR"
echo "========================================"
echo
echo "Импорт ZIP:"
echo "  tiktoker-import /путь/cookies.zip"
echo
echo "Проверить аккаунты и приватность:"
echo "  tiktoker-accounts scan"
echo
echo "Посмотреть список @username:"
echo "  tiktoker-accounts show"
echo
echo "Комментарий по ссылке:"
echo "  tiktoker-comment post --url 'https://www.tiktok.com/@user/video/...' --text 'Текст' --account '@username'"
echo
echo "Ручная CAPTCHA-проверка через RDP:"
echo "  tiktoker-verify --account 3 --url 'https://www.tiktok.com/'"
echo
echo "История комментариев:"
echo "  tiktoker-comment history"
echo
echo "Файлы списка:"
echo "  $APP_DIR/data/accounts.txt"
echo "  $APP_DIR/data/accounts.csv"
echo
echo "Настройка:"
echo "  nano $APP_DIR/config.yaml"
echo
echo "Запуск:"
echo "  sudo systemctl start tiktoker"
echo "  journalctl -u tiktoker -f"
