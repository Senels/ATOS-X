# ATOS X — AI Validation / Sprint 15-16 Foundation

## Amaç

AI eğitim ve backtest sonuçlarının finansal zaman serisi sızıntısıyla yapay
şekilde iyimserleşmesini engellemek. Kapsam yalnızca Binance Global
USDⓈ-M Futures verisidir.

## Eklenen temel katmanlar

- `backend/app/ai/validation.py`
  - chronological train/validation split
  - purge gap
  - overlapping LSTM sequence + forecast horizon için minimum purge hesabı
- `backend/app/ai/dataset.py`
  - OHLCV schema / duplicate / ordering / numeric / price-range / volume
    kontrolleri
  - SHA-256 dosya hash'i
  - sıralı ve deterministik dataset manifesti
- `backend/tests/test_ai_validation.py`
- `backend/tests/test_ai_dataset.py`

## Kritik kalan iş

`backend/app/ai/model.py` içindeki mevcut `train_test_split(...,
shuffle=True)` çağrıları henüz kaldırılmadı. Bu nedenle mevcut model eğitim
fonksiyonu **production-grade leakage-safe kabul edilmemelidir**.

Bir sonraki değişiklikte:

1. Dense eğitim chronological split'e taşınacak.
2. LSTM eğitim overlapping-window purge ile ayrılacak.
3. Scaler yalnızca train bölgesinde `fit` edilecek.
4. Walk-forward evaluation eklenecek.
5. Fee + slippage + funding dahil approved/rejected trade economics raporlanacak.

## Güvenlik sınırı

Bu katman canlı emir göndermez. Eğitim, validation ve backtest içindir.
