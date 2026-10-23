#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
#  JBD BMS Monitor — Установка на Raspberry Pi 4
#  Запустите: chmod +x install.sh && sudo ./install.sh
# ══════════════════════════════════════════════════════════════
set -e

echo "══════════════════════════════════════════════════════"
echo "  🔋 JBD BMS Monitor — Установщик"
echo "══════════════════════════════════════════════════════"

# 1. Установка Docker (если нет)
if ! command -v docker &> /dev/null; then
    echo "📦 Устанавливаю Docker..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sh get-docker.sh
    rm get-docker.sh
    usermod -aG docker "$SUDO_USER"
    echo "✅ Docker установлен"
else
    echo "✅ Docker уже установлен"
fi

# 2. Включаем автозапуск Docker при загрузке
systemctl enable docker
systemctl start docker

# 3. Установка Docker Compose plugin (если нет)
if ! docker compose version &> /dev/null; then
    echo "📦 Устанавливаю Docker Compose plugin..."
    apt-get update && apt-get install -y docker-compose-plugin
    echo "✅ Docker Compose установлен"
else
    echo "✅ Docker Compose уже установлен"
fi

# 4. Проверяем Bluetooth
echo ""
echo "🔍 Проверяю Bluetooth адаптер..."
if hciconfig hci0 &> /dev/null; then
    echo "✅ Bluetooth адаптер найден:"
    hciconfig hci0 | head -3
else
    echo "⚠️  Bluetooth адаптер не обнаружен!"
    echo "   Убедитесь, что он включён: sudo hciconfig hci0 up"
fi

# 5. Создаём .env из примера (если нет)
cd "$(dirname "$0")"
if [ ! -f .env ]; then
    cp .env.example .env
    echo ""
    echo "📝 Создан файл .env — ОБЯЗАТЕЛЬНО отредактируйте его:"
    echo "   nano .env"
    echo ""
    echo "   Укажите:"
    echo "   • BMS_MAC_ADDRESS — MAC адрес BMS (найдите через bluetoothctl → scan on)"
    echo "   • BOT_TOKEN       — токен от @BotFather в Telegram"
    echo "   • WEBAPP_URL      — будет определён автоматически после первого запуска"
else
    echo "✅ Файл .env уже существует"
fi

echo ""
echo "══════════════════════════════════════════════════════"
echo "  Готово! Дальнейшие шаги:"
echo ""
echo "  1. Отредактируйте .env:  nano .env"
echo "  2. Запустите:            docker compose up -d --build"
echo "  3. Узнайте HTTPS URL:    docker logs bms_tunnel 2>&1 | grep trycloudflare"
echo "  4. Впишите URL в .env:   nano .env"
echo "  5. Перезапустите:        docker compose restart bms-monitor"
echo "  6. Откройте бот в Telegram и напишите /start"
echo "══════════════════════════════════════════════════════"
