# TikToker

Автоматический конвейер для разрешённых/лицензированных видео:

1. Ищет новые ролики по YouTube-запросам и заданным каналам.
2. Запоминает `video_id` в SQLite и не обрабатывает повторно.
3. Скачивает через `yt-dlp`.
4. Делит FFmpeg на части заданной длины.
5. Генерирует русские подписи и хештеги; Gemini API опционален.
6. Публикует части в TikTok через cookies.txt, используя `tiktok-uploader`.
7. Поддерживает несколько TikTok-аккаунтов.
8. После успешной публикации удаляет локальные части.
9. Работает 24/7 через systemd.

> Используйте только собственные видео либо контент, на загрузку и повторную публикацию которого у вас есть разрешение.

## Ubuntu 24.04

```bash
git clone https://github.com/ReVerfyx/TikToker.git
cd TikToker
sudo bash install.sh
cp config.example.yaml config.yaml
nano config.yaml
```

Положите cookies трёх аккаунтов:

```text
cookies/account1.txt
cookies/account2.txt
cookies/account3.txt
```

Формат cookies — Netscape cookies.txt.

Запуск вручную:

```bash
source .venv/bin/activate
python main.py
```

Автозапуск:

```bash
sudo cp tiktoker.service /etc/systemd/system/tiktoker.service
sudo systemctl daemon-reload
sudo systemctl enable --now tiktoker
sudo systemctl status tiktoker
```

Логи:

```bash
journalctl -u tiktoker -f
```

## Настройка

Все основные параметры находятся в `config.yaml`: поисковые запросы, каналы, длина частей, интервал поиска, задержка между публикациями, cookies и Gemini API.
