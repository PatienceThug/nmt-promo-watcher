# NMT Promo Watcher

NMT.GG promo kodlarını takip eden otomatik tarayıcı.

- GitHub Actions ile her 5 dakikada bir çalışır.
- NMT.GG, herkese açık Telegram kaynakları ve YouTube araması taranır.
- İlk çalışmada mevcut kodlar baz alınır; sonraki çalışmalarda yalnızca yeni görülen kodlar alarm üretir.
- Yeni kod bulunduğunda repoda `🚨 NMT PROMO` başlıklı bir GitHub Issue açılır.

> Not: GitHub Actions zamanlanmış işler tam saniyesinde çalışma garantisi vermez; yoğunluk durumunda kısa gecikmeler olabilir.
