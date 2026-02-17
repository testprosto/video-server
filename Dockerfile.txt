FROM python:3.11-slim

WORKDIR /app

# Устанавливаем ffmpeg и aria2
RUN apt-get update && apt-get install -y \
    ffmpeg \
    aria2 \
    && rm -rf /var/lib/apt/lists/*

# Копируем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем проект
COPY . .

ENV ENV=production

CMD ["python", "app.py"]
