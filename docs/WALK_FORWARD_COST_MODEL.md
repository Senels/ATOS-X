# Walk-Forward + Cost-Aware Evaluation

ATOS-X'in AI/backtest değerlendirmesi artık yalnızca ham tahmin doğruluğuna bakmamalıdır.
Binance Global USDⓈ-M Futures için sonuçlar zaman sıralı test pencerelerinde ve işlem
maliyetleri düşülerek ölçülmelidir.

## Kural

1. Train yalnızca geçmiş gözlemlerden oluşur.
2. Test/OOS penceresi train'den sonra gelir.
3. Pencereler arasında purge uygulanabilir.
4. Test sırasında seçilen parametreler yeniden optimize edilmez.
5. Net trade return hesabında fee + slippage + funding dikkate alınır.

## Kod

`backend/app/backtest/walk_forward.py`

- `make_windows()` → chronological train/test pencereleri
- `CostModel` → fee, slippage, funding varsayımları
- `trade_return()` → net directional trade getirisi
- `evaluate_walk_forward()` → OOS prediction stream özeti

Bu katman emir göndermez; yalnızca araştırma/backtest değerlendirmesidir.

## Varsayılan maliyetler

- fee: `0.05%` / side
- slippage: `0.02%` / side
- funding: `0` varsayılan

Canlı Binance funding verisi geldiğinde funding_rate_per_bar gerçek veriyle beslenmelidir.
Varsayılan değer gerçek piyasa funding oranı iddiası değildir.
