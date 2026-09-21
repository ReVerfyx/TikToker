# TikToker

Автоматический конвейер для разрешённых/лицензированных видео: поиск YouTube, SQLite-защита от повторов, скачивание yt-dlp, нарезка FFmpeg, русские подписи/хештеги, очередь TikTok и удаление файлов после успешной публикации.

> Используйте только собственные видео либо контент, на загрузку и повторную публикацию которого у вас есть разрешение.

## Возможности

- поиск одновременно по запросам и списку YouTube-каналов;
- интервал поиска по умолчанию 1 час;
- нарезка на части любой длины;
- 9:16 с размытым фоном или оригинальное соотношение сторон;
- SQLite хранит video_id и очередь после перезапуска;
- TikTok через Netscape cookies.txt;
- количество TikTok-аккаунтов не ограничено кодом: список читается из config.yaml;
- распределение per_video или per_part;
- задержка между публикациями настраивается, по умолчанию 60 секунд;
- локальные хештеги без API;
- обязательные теги каждого поста: `#fyp #рек #рекомендации`;
- systemd для 24/7 и автозапуска.

## Ubuntu 24.04

    git clone https://github.com/ReVerfyx/TikToker.git
    cd TikToker
    bash install.sh
    nano config.yaml

Импорт JSON/TXT/ZIP cookies:

    python import_cookies.py /путь/cookies.zip

Импортёр принимает ZIP с любым числом JSON- или Netscape TXT-профилей и создаёт
`cookies/account001.txt`, `account002.txt` и т.д. TikToker автоматически
подхватывает их все.

Если экспорт содержит `sid_guard`, но не содержит HttpOnly `sessionid`,
импортёр проверяет стандартную структуру `sid_guard`. Когда первый сегмент
является 32-символьным session-токеном, он локально добавляет совместимые
`sessionid`, `sessionid_ss` и `sid_tt`. Значения cookies не коммитятся
в GitHub и остаются только на сервере.

После настройки:

    sudo systemctl start tiktoker
    sudo systemctl status tiktoker
    journalctl -u tiktoker -f

Для запуска вручную:

    source .venv/bin/activate
    python main.py
