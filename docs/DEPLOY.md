# ATOS-X — Docker Deployment Kılavuzu

## Gereksinimler

| Araç | Minimum sürüm |
|------|---------------|
| Docker | 24.x |
| Docker Compose | v2 (`docker compose`) |
| Binance API Key | USDⓈ-M Futures izinli |
| Telegram Bot Token | BotFather'dan |

---

## 1. Hızlı Başlangıç

```bash
# 1. Depoyu klonla
git clone https://github.com/Senels/ATOS-X.git
cd ATOS-X

# 2. Ortam değişkenlerini yapılandır
cp backend/.env.example backend/.env
nano backend/.env   # API anahtarlarını doldur

# 3. Konteynerları başlat
make docker-up

# 4. Logları izle
make docker-logs
```

Backend `http://localhost:8000` adresinde çalışır; API dokümantasyonu `/docs` yolunda açılır.

---

## 2. Environment Değişkenleri

`backend/.env` dosyasında doldurulması zorunlu alanlar:

```env
BINANCE_API_KEY=<key>
BINANCE_SECRET_KEY=<secret>
BINANCE_TESTNET=False           # Canlı → False
PAPER_TRADING=False             # Canlı → False
LIVE_TRADING_ENABLED=True       # Canlı → True (üçü birlikte!)

TELEGRAM_TOKEN=<token>
TELEGRAM_CHAT_ID=<chat_id>

API_KEY=<güçlü-rastgele-anahtar>   # REST API güvenliği
```

Opsiyonel (Sprint 15-22 eklentileri):

```env
MTF_ENABLED=false               # Multi-timeframe sinyal
AI_MODEL_TYPE=dense             # dense | lstm | ensemble
VAR_CONFIDENCE=0.95
VAR_LOOKBACK_DAYS=30
DAILY_REPORT_HOUR=21
```

---

## 3. Docker Compose Servisleri

| Servis | Port | Açıklama |
|--------|------|----------|
| `atos-backend` | 8000 | FastAPI + trading motoru |
| `atos-nginx` | 80/443 | Reverse proxy (SSL opsiyonel) |

Veritabanı `atos-db` adlı Docker volume'da saklanır (`/app/legacy/atos.db`).

---

## 4. Makefile Komutları

```bash
make docker-build    # İmajı yeniden derle
make docker-up       # Servisleri başlat (arka planda)
make docker-down     # Servisleri durdur
make docker-logs     # Canlı log akışı
make docker-clean    # Servisleri durdur + volume'ları sil (⚠ VERİ SİLİNİR)
```

---

## 5. SSL Yapılandırması

`docker/nginx/nginx.conf` içindeki `# SSL PLACEHOLDER` yorumunu kaldırıp Certbot ile sertifika ekleyebilirsiniz:

```bash
docker compose run --rm certbot certonly --webroot \
    -w /var/www/certbot \
    -d atos.example.com
```

Ardından `nginx.conf` içinde HTTPS bloğunu etkinleştirin.

---

## 6. Güncelleme Prosedürü

```bash
git pull origin main
make docker-build
make docker-down
make docker-up
make docker-logs
```

Veritabanı volume'da kalır; uygulama güncellemelerinde veri kaybolmaz.

---

## 7. Yedekleme

Telegram `/yedek` komutu ile anlık yedek alınabilir. Otomatik yedek `legacy/backups/` dizinine yazılır.

Volume dışına yedek almak için:

```bash
docker run --rm \
    -v atos_atos-db:/data \
    -v $(pwd)/backup:/backup \
    busybox cp /data/atos.db /backup/atos_$(date +%Y%m%d).db
```

---

## 8. Sorun Giderme

| Belirti | Çözüm |
|---------|-------|
| `ModuleNotFoundError` | `make docker-build` ile yeniden derle |
| Binance bağlantı hatası | `.env` anahtarlarını kontrol et; `BINANCE_TESTNET=True` ile test et |
| Telegram mesajı gelmiyor | `TELEGRAM_TOKEN` ve `TELEGRAM_CHAT_ID` doğru mu? |
| Port 8000 zaten kullanımda | `docker compose down` ardından tekrar dene |

Loglar için: `make docker-logs` veya `docker compose logs atos-backend --tail 200`.
