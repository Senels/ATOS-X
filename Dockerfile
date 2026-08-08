# ─────────────────────────────────────────────────────────────────────────────
# ATOS X — Çok aşamalı Docker build
# ─────────────────────────────────────────────────────────────────────────────

# ── Aşama 1: Builder ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Sistem bağımlılıkları (derleme araçları)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Bağımlılıkları yükle (paket kodundan önce — önbellek katmanı)
COPY backend/pyproject.toml ./backend/
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e "./backend[dev]"

# ── Aşama 2: Runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Güvenlik: root olmayan kullanıcı
RUN useradd --create-home --shell /bin/bash atos
WORKDIR /app

# Sistem çalışma zamanı bağımlılıkları
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Builder'dan yüklü paketleri kopyala
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Uygulama kodunu kopyala
COPY backend/ ./backend/

# Kullanıcı izinleri
RUN chown -R atos:atos /app
USER atos

# Ortam değişkenleri
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/backend

# Sağlık kontrolü
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
