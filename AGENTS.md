# ATOS X — Agent Çalışma Kuralları

Trading bot (Binance USDⓈ-M Futures, paper + canlı). FastAPI + SQLite + TensorFlow.
Güvenilirlik > hız. Canlı sistem üzerinde yapılan her değişiklik kullanıcı onayı ister.

## Ortam (KRİTİK)

- Python: `C:\Users\svkts\AppData\Local\Programs\Python\Python311\python.exe`
- `.venv` KULLANILMAZ — içinde numpy/pandas yok. Tüm test/script/uvicorn sistem Python ile çalışır.
- TensorFlow: `tensorflow-intel==2.15.1` (shim DEĞİL; pandas 2.0.3 + numpy 1.26.4 uyumu). `tf.random` top-level yok — `model.py` try/except ile korumalı.
- Shell: Windows PowerShell 5.1 — `&&` YOK; `;` veya `if ($?) { }` kullan.
- `git push` PowerShell'de NativeCommandError gösterebilir ama başarılıdır; çıktıdan emin olmak için `git status` ile doğrula.

## Komutlar (workdir: `backend`)

- Test: `& "C:\Users\svkts\AppData\Local\Programs\Python\Python311\python.exe" -m pytest tests/ -q`
- Ruff: `ruff check app tests --select E9,F63,F7,F82,F,I` (temiz olmalı)
- Dev/paper sunucu: `& "C:\Users\svkts\AppData\Local\Programs\Python\Python311\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000` (workdir `backend`)
- Backtest tarama: `python scripts/scan_backtest.py --symbols 200 [--ttp] [--vol-sizing]`
- AI eğitim: `python scripts/train_ai.py --symbols 400 --epochs 30 --horizon 24 --atr-mult 1.0 --model ai_direction`
- AI değerlendirme: `python scripts/eval_ai.py --symbols 80 --recent-bars 200`
- gh CLI: `C:\Users\svkts\AppData\Local\Temp\opencode\gh\bin\gh.exe`

## Canlı Sistem

- Paper mod, port 8000, `trading=True`. PID her restart'ta değişir (`opencode-memory.md`'den oku).
- Health: `http://127.0.0.1:8000/health` · Dashboard: `http://127.0.0.1:8000/dashboard/html`
- Loglar: `C:\Users\svkts\AppData\Local\Temp\opencode\paper_run\{out,err}.log`
- DB: `backend/atos.db` — CANLI. Okuma için sqlite MCP `read_query` kullan (SELECT/PRAGMA).
- `backend/app/strategy/settings.json`: runtime ayarları. Değişiklikler PYTHON ile yazılır (PowerShell BOM kazası!). Yükleyici `utf-8-sig` okur.

## Güvenlik Kuralları

1. Canlı DB'ye DOĞRUDAN YAZMA (INSERT/UPDATE/DELETE) — kullanıcı onayı olmadan asla. Analiz salt-okunur.
2. Kill-switch, giriş durdurma, SL/TP değişikliği, restart → önce kullanıcıya sor.
3. `opencode-memory.md` ve `.opencode/` gitignore'lıdır (yerel). `opencode.json`, `AGENTS.md` commit edilebilir.
4. Canlı sunucu çalışırken backtest/optimizasyon çalıştırmak güvenlidir (yalnızca okuma + ayrı süreç).

## Kod Standartları

- Talep edilmedikçe yorum satırı ekleme.
- Her değişikliğe test ekle; tam süit yeşil + ruff temiz olmadan commit önerme.
- Kod/sembol adları İngilizce, dokümanlar Türkçe.
- Mevcut desenleri takip et: FastAPI, SQLAlchemy, pydantic-settings, `app/` içinde modüler yapı.
