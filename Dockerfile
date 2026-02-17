FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    ffmpeg \
    aria2 \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir yt-dlp curl_cffi brotli
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV ENV=production

CMD ["python", "app.py"]
