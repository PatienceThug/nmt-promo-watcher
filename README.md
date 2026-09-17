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
