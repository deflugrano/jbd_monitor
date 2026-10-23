FROM python:3.11-slim

# Системные зависимости для Bluetooth BLE на ARM64 (Raspberry Pi 4)
RUN apt-get update && apt-get install -y --no-install-recommends \
    bluez \
    libbluetooth-dev \
    libdbus-1-dev \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Зависимости Python (кэшируются в слое)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Исходный код
COPY . .

EXPOSE 8000

# Healthcheck — проверяем что веб-сервер жив
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -f http://localhost:8000/api/health || exit 1

CMD ["python3", "main.py"]
