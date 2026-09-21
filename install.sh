#!/usr/bin/env bash
set -euo pipefail

sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip ffmpeg

python3 -m venv .venv
.venv/bin/pip install --upgrade pip wheel
.venv/bin/pip install -r requirements.txt

mkdir -p cookies data/work
[ -f config.yaml ] || cp config.example.yaml config.yaml

echo "Готово. Настрой config.yaml и cookies/*.txt, затем: source .venv/bin/activate && python main.py"
