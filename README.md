# NMT Promo Watcher

NMT.GG promo kodlarını takip eden otomatik tarayıcı.

- GitHub Actions zamanlaması `*/5 * * * *`: hedef her 5 dakikada bir taramadır. GitHub gecikmeleri nedeniyle kesin 5 dakika garantisi yoktur.
- Hızlı tarama Telegram, NMT sayfaları ve ücretsiz resmi X kaynağını kontrol eder. Her beşinci çalıştırma ve elle/kod değişikliğiyle başlatılan çalıştırmalar kapsamlı tarama yapar.
- Yeni kod için önce `🚨 NMT PROMO:` başlıklı GitHub Issue açılır. Telegram bildirimi bu kayıttan üretilir.
- Telegram ana bildirim kanalıdır; GitHub Issues yedek kayıttır. Yeni kod bulunmazsa rutin mesaj gönderilmez.
- İlk Telegram kurulumunda eski issue'lar baz alınır; geçmiş uyarılar topluca gönderilmez.

## Telegram

Actions secrets içinde `TELEGRAM_BOT_TOKEN` ve tercihen açıkça belirlenmiş `TELEGRAM_CHAT_ID` kullanılır. Mevcut kurulum kayıtlı sohbeti de kullanabilir. Token veya kullanılabilir sohbet bulunamazsa bildirim adımı hata verir. Token değerini dosyaya, issue'ya veya loglara yazmayın.

Gönderilen her uyarı ayrı ayrı kaydedilir. Sonraki gönderim başarısız olsa bile önceki teslimatlar korunur; gönderilemeyenler sonraki çalıştırmada tekrar denenir. GitHub issue listesi tüm sayfalarıyla okunur.

## Hata bildirimleri

Normal tarama hatalarında GitHub'da `⚠️ NMT WATCHER HEALTH: Workflow` kaydı açılır ve Telegram adımı yine çalışır. Telegram veya durum kaydı başarısızsa ayrıca GitHub Issue oluşturulmaya çalışılır. Çalıştırma başarısız olarak kalır; hata gizlenmez.

İptal, tüm işin zaman aşımı, GitHub kesintisi veya yetki kaybında bu hata bildirimi de gönderilemeyebilir. Actions sayfası son kontrol noktasıdır. Mesajın Telegram'a gönderilmesi telefonda görüldüğü anlamına gelmez; Telegram ve telefon bildirim izinleri açık olmalıdır.

## Yetkiler ve GitHub yedeği

Workflow yalnızca gereken `contents: write` (durum kaydı) ve `issues: write` (uyarı kaydı) yetkilerini ister.

- Çalıştırmalar: https://github.com/PatienceThug/nmt-promo-watcher/actions
- Yedek uyarılar: https://github.com/PatienceThug/nmt-promo-watcher/issues
- GitHub'dan da bildirim almak için repoda Watch → Custom → Issues seçin. E-posta teslimatı kişisel GitHub bildirim ayarlarınıza bağlıdır.


## Reliability and source coverage (2026-09-21)

- Issue #41 was a state push failure (`fatal error in commit_refs`), not a failed Telegram scan. State now uses Git Data API commits, non-forced ref updates, and four bounded retries that re-read and merge the latest state.
- Delivery receipts are persisted after each notification. Telegram delivery and GitHub commits cannot form a single transaction: a crash between them can still replay one message.
- Primary scans deliver before slow social discovery. Social discovery has a 180-second budget; incomplete coverage is recorded in the Actions summary.
- Telegram promo posts also support isolated `<code>`/`<pre>` tokens after emoji or explanatory text. Community channels must identify NMT in the post text or links. Existing freshness and duplicate filters remain active.
- The Actions summary and `scan-evidence` artifact show readable channels, post counts, candidate counts and latest post dates. A candidate is not a confirmed redeemable code. X 403/429 means incomplete X coverage; no personal X credentials or paid API are used.
- Repeated workflow failures share an open health issue instead of opening a new issue every run. Delivery failures have a distinct Telegram message.
- The fast core retains Telegram and official web pages. Deep X/YouTube discovery remains in the social scanner; duplicate core YouTube/mirror searches no longer delay primary notifications. Image/video-only codes are not read.

Run regression checks: `python -m unittest -v test_radar.py test_telegram_notify.py test_social_watcher.py`.
