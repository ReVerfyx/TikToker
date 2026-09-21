# TikToker

Автоматический конвейер для разрешённых/лицензированных видео: поиск YouTube, защита от повторов через SQLite, скачивание yt-dlp, нарезка FFmpeg, локальные русские хештеги, очередь TikTok, несколько аккаунтов и systemd.

> Используйте только собственные видео либо контент, на загрузку и повторную публикацию которого у вас есть разрешение.

## Установка на Ubuntu 24.04 одной командой

```bash
curl -fsSL https://raw.githubusercontent.com/ReVerfyx/TikToker/main/install.sh | bash
```

Проект устанавливается в `/opt/TikToker` и при повторном запуске этой же команды обновляется из GitHub.

## Cookies

Импорт ZIP/JSON/TXT:

```bash
tiktoker-import /root/cookies.zip
```

Cookies сохраняются только на VPS в `/opt/TikToker/cookies/` и исключены из Git. Если имя файла в ZIP — числовой TikTok user ID, импортёр сохраняет его в имени локального файла вида `account_<user_id>.txt`, чтобы точнее сопоставлять профиль с `@username`.

## Список TikTok-аккаунтов

Проверить все аккаунты:

```bash
tiktoker-accounts scan
```

Посмотреть сохранённый список:

```bash
tiktoker-accounts show
```

Результаты сохраняются в:

- `data/accounts.txt` — простой список;
- `data/accounts.csv` — таблица с cookie-файлом, @username, публичностью и статусом;
- `data/accounts.json` — реестр, который читает сам TikToker.

Основной бот использует только аккаунты со статусом `public`. Приватные, недоступные, неавторизованные и нераспознанные аккаунты остаются в списке, но автоматически исключаются из публикации.

При запуске systemd реестр проверяется заново, только если он старше 24 часов.

## Настройка

```bash
nano /opt/TikToker/config.yaml
```

Обязательные хештеги каждой публикации:

```text
#fyp #рек #рекомендации
```

Остальные теги генерируются локально из названия и описания YouTube-видео, без Gemini и других платных API.

## Запуск

```bash
sudo systemctl start tiktoker
sudo systemctl status tiktoker
journalctl -u tiktoker -f
```

Перезапуск после изменения конфига:

```bash
sudo systemctl restart tiktoker
```

## Обновление

Та же команда, что и для установки:

```bash
curl -fsSL https://raw.githubusercontent.com/ReVerfyx/TikToker/main/install.sh | bash
```
