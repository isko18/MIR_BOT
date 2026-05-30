FROM python:3.12-slim

WORKDIR /app

# Зависимости для Pillow (изображения)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg62-turbo \
    zlib1g \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Папки создаются при запуске (data/, photo/)
RUN mkdir -p data photo

# .env монтируется при запуске или передаётся через переменные окружения

ENV PYTHONUNBUFFERED=1

CMD ["python", "-u", "main.py"]
