# Развёртывание на сервере

Проект: `/home/kurut/`

## Быстрый старт (Docker)

### 1. Подготовка

```bash
cd /путь/к/проекту
cp .env.example .env
nano .env   # заполнить токены, ADMIN_IDS и остальные параметры
```

### 2. Запуск с Redis (рекомендуется)

```bash
cd devops
docker compose up -d
docker compose logs -f bot   # логи
```

### 3. Запуск без Redis (упрощённый)

```bash
cd devops
docker compose -f docker-compose.simple.yml up -d
```

---

## systemd (без Docker)

### 1. Установка зависимостей

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv redis-server
```

### 2. Размещение проекта

```bash
sudo mkdir -p /opt/telegram-bot
sudo chown $USER:$USER /opt/telegram-bot
cd /opt/telegram-bot

# Скопировать файлы проекта
# git clone ... или scp/rsync

python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
nano .env   # настроить
```

### 3. Redis (опционально)

```bash
# В .env добавить:
REDIS_URL=redis://localhost:6379/0

# Redis уже установлен как сервис, стартует автоматически
sudo systemctl enable redis-server
sudo systemctl start redis-server
```

### 4. Сервис systemd

```bash
sudo useradd -r -s /bin/false bot 2>/dev/null || true
sudo chown -R bot:bot /opt/telegram-bot
sudo cp telegram-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable telegram-bot
sudo systemctl start telegram-bot
sudo systemctl status telegram-bot
```

---

## Переменные окружения (.env)

| Переменная | Обязательно | Описание |
|------------|-------------|----------|
| `USER_BOT_TOKEN` | да | Токен пользовательского бота от @BotFather |
| `ADMIN_BOT_TOKEN` | да | Токен админ-бота |
| `ADMIN_IDS` | да | Telegram ID админов через запятую (@userinfobot) |
| `PAYMENT_QR_PAYLOAD` | да | URL или текст для QR оплаты |
| `SUPPORT_USERNAME` | нет | Юзернейм поддержки без @ |
| `WELCOME_BRAND` | нет | Бренд в приветствии |
| `REDIS_URL` | нет | Redis для FSM (масштабирование); пусто = MemoryStorage |

Полный список — см. `.env.example`.

---

## Папки

- `data/` — SQLite база (`app.db`), создаётся автоматически
- `photo/` — локальные логотипы букмекеров (`1xbet.jpg`, `1win.png` и т.д.)

---

## Проверка работы

1. Написать `/start` пользовательскому боту
2. Должно прийти приветствие с клавиатурой
3. Админ-бот должен получать заявки на пополнение
