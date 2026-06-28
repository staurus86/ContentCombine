# ContentCombine

Движок для контент-конвейера: собирает новости из RSS- и Telegram-источников,
отбирает по скорингу, переписывает через LLM и публикует дайджесты в Telegram,
VK и Google Sheets. Управление — через веб-админку.

## Возможности

- Парсинг источников: RSS-фиды и Telegram-каналы (через Telethon)
- Скоринг материалов по настраиваемым весам (виральность, частотность Keyso, тренды, заголовок)
- Сквозная дедупликация: одна история не повторяется из дайджеста в дайджест
- Рерайт текстов через любой OpenAI-совместимый эндпоинт (OpenAI, LiteLLM-гейтвей)
- Публикация в Telegram, VK, Google Sheets; если токена нет — пост копируется из UI
- Веб-админка: лента, ручная и автопубликация, аналитика расхода LLM
- Планировщик циклов парсинга с защитой от параллельных запусков

## Стек

Python · FastAPI · APScheduler · PostgreSQL (SQLite локально) · Docker

## Быстрый старт

```bash
git clone https://github.com/staurus86/ContentCombine.git
cd ContentCombine

python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate

pip install -r requirements.txt

cp .env.example .env   # заполни значения, минимум DATABASE_URL, OPENAI_API_KEY, ADMIN_PASSWORD

python main.py
```

Админка поднимется на `http://localhost:8080` (порт меняется через `PORT`).

Локально достаточно SQLite — укажи `DATABASE_URL=sqlite:///local.db`.
Для прода используется Postgres.

## Конфигурация

Все настройки — через переменные окружения. Полный список с комментариями
смотри в [`.env.example`](.env.example). Обязательный минимум:

| Переменная | Назначение |
|---|---|
| `DATABASE_URL` | строка подключения к БД |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | доступ к LLM |
| `LLM_MODEL` | модель рерайта (по умолчанию `gpt-4o-mini`) |
| `ADMIN_PASSWORD` | пароль входа в админку |
| `COOKIE_SECRET` | подпись сессионных cookie |

Интеграции (Keyso, Telegram, VK, Google Sheets) опциональны — оставь
соответствующие переменные пустыми, и связанные функции просто отключатся.

## Деплой

В репозитории есть `Dockerfile` и `railway.toml`. Деплой на Railway:
push в `master` → автоматический редеплой. Переменные окружения задаются
в настройках сервиса. Для любого другого хостинга подойдёт готовый Docker-образ.

## Структура

```
main.py          точка входа: планировщик + веб
config.py        конфигурация из ENV
scheduler.py     циклы парсинга
web.py           веб-сервер и админка
apis/            интеграции: Keyso, Telegram-публикация, кэш
bot/             Telegram-бот
core/            скоринг, дайджесты, observability
storage/         БД и Google Sheets
api/             эндпоинты аналитики
```

## Лицензия

MIT — см. [LICENSE](LICENSE). Бери каркас, меняй под себя, используй
в коммерции. Единственное условие — сохранить копирайт и текст лицензии.
