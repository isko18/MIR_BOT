#!/bin/bash
# Запуск бота локально
set -e
cd "$(dirname "$0")"

if [ ! -f .env ]; then
    echo "Создайте .env (скопируйте .env.example и заполните токены)"
    exit 1
fi

if [ ! -d .venv ]; then
    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
fi

.venv/bin/python -u main.py
