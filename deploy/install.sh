#!/bin/bash
# Claude Telegram bot — serverga o'rnatish.
#
# Ishlatish (root):
#     DOMAIN=bot.example.com bash deploy/install.sh
#
# Agar domen allaqachon boshqa nginx konfiguratsiyasida band bo'lsa, uni
# ko'rsating — skript o'sha fayldan domenni ajratadi va zaxiralaydi:
#     DOMAIN=bot.example.com OLD_CONF=/etc/nginx/sites-available/eski \
#         bash deploy/install.sh
#
# Nima qiladi:
#   1. (ixtiyoriy) domenni eski konfiguratsiyadan ajratadi
#   2. nginx konfiguratsiyasini qo'yadi (WebSocket -> 127.0.0.1:8787)
#   3. TLS sertifikat oladi (certbot)
#   4. systemd xizmatlarini yoqadi
#
# nginx sintaksisi buzilsa DARHOL to'xtaydi va eski holatni qaytaradi —
# serverdagi boshqa saytlar xavf ostida qolmaydi.

set -euo pipefail

DOMAIN="${DOMAIN:?DOMAIN kiritilmagan. Masalan: DOMAIN=bot.example.com bash $0}"
OLD_CONF="${OLD_CONF:-}"
APP_DIR="${APP_DIR:-/home/claudebot/claude-tg}"
DEPLOY="$APP_DIR/deploy"

step() { echo; echo "=== $* ==="; }

if [ "$(id -u)" -ne 0 ]; then
    echo "Root kerak:  sudo DOMAIN=$DOMAIN bash $0" >&2
    exit 1
fi
if [ ! -d "$DEPLOY" ]; then
    echo "Topilmadi: $DEPLOY  (APP_DIR ni to'g'rilang)" >&2
    exit 1
fi

# --- 1. Domenni eski konfiguratsiyadan ajratamiz (ixtiyoriy) --------------
if [ -n "$OLD_CONF" ]; then
    step "1/5  $DOMAIN ni eski konfiguratsiyadan ajratamiz"
    BACKUP="$OLD_CONF.bak-claude-tg"
    [ -f "$BACKUP" ] || cp "$OLD_CONF" "$BACKUP"
    echo "  zaxira: $BACKUP"
    if grep -q "$DOMAIN" "$OLD_CONF"; then
        escaped="${DOMAIN//./\\.}"
        sed -i -E "s/(^[[:space:]]*server_name[^;]*)[[:space:]]${escaped}\b/\1/" "$OLD_CONF"
        sed -i -E "s/(^[[:space:]]*server_name)[[:space:]]+${escaped}\b/\1 /" "$OLD_CONF"
        echo "  olib tashlandi"
    else
        echo "  allaqachon yo'q"
    fi
    grep -n server_name "$OLD_CONF" | sed 's/^/    /'
else
    step "1/5  Eski konfiguratsiya ko'rsatilmagan — o'tkazamiz"
fi

# --- 2. Yangi konfiguratsiya ---------------------------------------------
step "2/5  nginx konfiguratsiyasi ($DOMAIN)"
sed "s/__DOMAIN__/$DOMAIN/g" "$DEPLOY/claude-tg.nginx" \
    > /etc/nginx/sites-available/claude-tg
ln -sfn /etc/nginx/sites-available/claude-tg /etc/nginx/sites-enabled/claude-tg

if ! nginx -t; then
    echo >&2
    echo "  XATO: nginx sintaksisi buzildi. Eski holatni qaytaramiz." >&2
    rm -f /etc/nginx/sites-enabled/claude-tg
    if [ -n "$OLD_CONF" ] && [ -f "$OLD_CONF.bak-claude-tg" ]; then
        cp "$OLD_CONF.bak-claude-tg" "$OLD_CONF"
    fi
    nginx -t
    exit 1
fi
systemctl reload nginx
echo "  nginx qayta yuklandi"

# --- 3. TLS ---------------------------------------------------------------
step "3/5  TLS sertifikat"
if [ -d "/etc/letsencrypt/live/$DOMAIN" ]; then
    echo "  sertifikat allaqachon bor"
else
    certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos \
        --register-unsafely-without-email --redirect
    nginx -t && systemctl reload nginx
fi

# --- 4. systemd -----------------------------------------------------------
step "4/5  systemd xizmatlari"
cp "$DEPLOY/claude-tg-bot.service"   /etc/systemd/system/
cp "$DEPLOY/claude-tg-agent.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now claude-tg-bot.service
sleep 6
systemctl enable --now claude-tg-agent.service
sleep 4

# --- 5. Holat -------------------------------------------------------------
step "5/5  Holat"
systemctl --no-pager --lines=0 status claude-tg-bot.service   | head -4
systemctl --no-pager --lines=0 status claude-tg-agent.service | head -4
echo
echo "Loglar:"
echo "  journalctl -u claude-tg-bot   -f"
echo "  journalctl -u claude-tg-agent -f"
echo
echo "Agentlar uchun manzil:  wss://$DOMAIN/agent"
