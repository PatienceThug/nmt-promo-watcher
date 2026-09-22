# NMT kazanç araştırma motoru

Bu aşama bir veri ve karar temeli kurar. Canlı pazar/hesap entegrasyonu henüz yoktur.
Uygun bulunan aday bile doğrulanmış kâr veya alım talimatı değildir. Eski Brain
komutları statik `nmt_rules.json` hesaplayıcıları olarak kalır; yeni motorun
kanıt filtresi yalnızca yeni fırsat raporuna uygulanır.

## Kullanım

Python 3.12, `requests==2.32.5`, `beautifulsoup4==4.13.5` gerekir.
Repo kökünden çalıştırın:

```bash
python nmt_profit.py
python nmt_profit.py --snapshot examples/opportunities.json --json
python nmt_sources.py --guide collections --guide power-blocks
```

İlk komut mevcut muhasebe kayıtlarını salt okunur okur. Aday dosyası yoksa
veri kapsamı **eksik** görünür. Muhasebe dosyası yoksa işlem hata verir;
kayıp dosya sıfır bakiye gibi sunulmaz. Rapor state'i değiştirmez.

`examples/opportunities.json` tamamen sentetik, doğrulanmamış bir şema örneğidir.
Canlı veri yerine kullanılmamalıdır. Kişisel girdileri git dışında tutulan
`nmt_opportunities.json` dosyasına koyun. Bütçe NMT cinsindedir; TL/USD kurunu
bu sürüm otomatik olarak tahmin etmez.

## Veri sözleşmesi

### Envanterden koleksiyon eşleştirme

Mevcut snapshot içine `inventory`, `listings`, `collection_definitions` listeleri
eklenebilir. Bu, NMT'nin resmî dışa aktarma formatı değildir; HPE'nin kullanıcı
tarafından sağlanan veri formatıdır. Hesap bağlantısı oluşturmaz.

Envanter satırı: `id`, `model`, `rarity`, `level` (pozitif tam sayı), `state`
(`Idle`), `claim_charges` (pozitif tam sayı), `evidence`.
İlan satırı aynı alanlarla `state: Listed` ve ayrıca `price_nmt` içerir.
Kimlikler tüm satırlarda benzersiz olmalı; aynı figür iki kaynakta görünüyorsa
önce kimlik çakışması çözülmelidir. Kayıtların evidence alanı aşağıdaki sözleşmeyi kullanır.

Koleksiyon tanımı: benzersiz `id`, `evidence` ve dört elemanlı `slots` listesi.
Her slot `model`, `rarity`, `level` içerir. Karşılaştırma tam eşleşmedir.
HPE mevcut kullanılabilir figürleri önce seçer, sonra en ucuz güncel ilanları
eşleştirir. Tek figürü aynı koleksiyonun iki slotunda kullanmaz. Sıfır hakkı
olan, eski, doğrulanmamış veya yanlış durumdaki kayıtlar eşleşmez.

Koleksiyonlar bağımsız alternatiflerdir; farklı koleksiyon sonuçlarında aynı
figür görünebilir. Birlikte satın alma planı sayılmazlar. Raporlanan maliyet
yalnızca ek alış fiyatlarının toplamıdır; komisyon, mevcut figürün fırsat
maliyeti ve günlük ödül dikkate alınmadan kâr önerisi yapılmaz. Kaynak
eksikliği raporlanır; kullanıcının envanteri boş varsayılmaz.

Üst alanlar: `schema_version: 1`, `budget_nmt`, `sources`, `candidates`.

Kaynak bölümleri: inventory, marketplace, collections, power_blocks, merge,
token, promos, events, rules. Her kaynak ve her adayın `evidence` kaydı:

| Alan | Anlamı |
|---|---|
| status | ok, missing, blocked veya conflict |
| source | Kaynak URL'si veya kullanıcı tarafından sağlanan kaydın referansı |
| observed_at | Saat dilimi içeren ISO zaman damgası |
| ttl_seconds | Verinin kullanım ömrü; fiyat için kısa tutulmalı |
| verification | Gerçekten gözlenen/verisi kontrol edilen kayıt için observed; diğerleri unreviewed |

Bu işaretler veri sağlayıcısının beyanıdır, kriptografik doğrulama değildir.
Fiyat, ücret, figür durumu ve satış kıyaslarının tamamını kontrol etmeden
`observed` yazmayın. İlerideki doğrulanmış veri adaptörleri bu kaydı üretmelidir.
Gelecek tarih, saat dilimi eksikliği, eski kayıt veya erişim hatası adayı durdurur.

Her adayda şunlar gerekir:

| Alan | Anlamı |
|---|---|
| id / kind | Benzersiz aday kimliği; flip veya collection |
| asset_ids | Sahip olunan figürler ve alınacak ilanlar dahil tüm benzersiz kimlikler |
| buy_nmt | Yeni alımların toplamı |
| owned_opportunity_cost_nmt | Kullanılacak mevcut figürlerin vazgeçilen net satış değeri |
| exit_value_nmt | Süre sonundaki tahmini brüt satış değeri; satış garantisi değildir |
| exit_basis | Uygunluk için completed_sales; ilan fiyatı tek başına yeterli değildir |
| fee_fraction / other_costs_nmt | Satış komisyonu oranı ve diğer toplam nakit masrafları |
| stress_haircut_fraction | Ödül ve net çıkış değerine uygulanan düşüş senaryosu, 0–1 |
| target_profit_nmt / horizon_days | Minimum senaryo kazancı ve karşılaştırma süresi |
| evidence | Aday fiyatlarının ve koşullarının doğrulama kaydı |

Koleksiyon ek alanları: `daily_nmt`, `minimum_claim_charges`, `planned_claims`,
`claim_before_unpack: true`, `exact_slots_verified: true`. Son alan model,
nadirlik, seviye ve Idle koşullarının tamamının kontrol edildiğini belirtir.
Tahsilat sayısı en az bir olmalı ve kalan en düşük hakkı aşmamalıdır.
Süre boyunca birikim varsayılır; son tahsilat açmadan önce, süre sonunda yapılır.
Aradaki tahsilatlar hakları süre bitmeden tüketmemelidir. Oyun sürümündeki
birikim/claim kuralları ayrıca kontrol edilmelidir.

## Hesaplar

```
net_satış = çıkış_değeri × (1 − komisyon)
ödül = günlük_NMT × gün (flip için 0)
taban_senaryo = net_satış + ödül − alış − mevcut_figür_fırsat_maliyeti − diğer_masraflar
stres_senaryosu = (net_satış + ödül) × (1 − düşüş) − aynı_maliyetler
azami_alış = (net_satış + ödül) × (1 − düşüş) − mevcut_figür_fırsat_maliyeti − diğer_masraflar − hedef
```

Ödüller ve satışlar tahmindir; model satılamama olasılığını ölçmez. Stres
kesintisi beklenen değer veya istatistiksel güven aralığı değildir. Fiyatın
NMT karşılığı korunurken USD/TL karşılığı düşebilir. Net kâr ifadesi raporda
yalnızca senaryoya aittir; muhasebede gerçekleşmiş kâr olarak kaydedilmez.

Bütçe seçimi en fazla 18 uygun adayı aynı süre için bütün kombinasyonlarla
karşılaştırır, aynı figür/ilanı tekrar kullanmaz. Nakit gereksinimi alış + diğer
masraflardır. Tahmini satış/ödül gelirlerini yeni satın alımların finansmanı
olarak önceden harcamaz. Bu sınır aşılırsa sessizce aday atmak yerine raporlar.

## Muhasebe

Sermaye giriş/çıkışı faaliyet gelirinden ayrıdır. Tekrarlanan kimlikler bir kez
sayılır; aynı kimlikte farklı tutar ve geçersiz kayıt açıkça işaretlenir.
Mevcut defter kullanıcı kaydıdır, bağımsız işlem doğrulaması değildir.
Satış-varlık maliyet eşleştirmesi bulunmadığı için **gerçekleşmiş kâr bilinmiyor**.
Power Blocks tur sonuçları ikinci kez muhasebeye eklenmez.

## Resmî rehber değişiklikleri

`nmt_sources.py` sabit resmî URL listesini, yönlendirmeleri takip etmeden okur.
403/429 erişim hatasıdır; başarılı sayfa çekimi mekanik doğrulaması sayılmaz.
Rehberler canlı envanter veya pazar fiyatının yerine geçmez.
Önceki başarılı içerik/hash başarısız kontrolde korunur. Değişiklik görünür
kalmaya devam eder; insan incelemesinden sonra ilgili kaydın `change_pending`
alanı yerel dosyada false yapılabilir. Son kontrol saati mutlaka dikkate alınmalıdır.
JavaScript boş kabuğu, challenge veya yeniden düzenlenen bir sayfa insan
incelemesi gerektirebilir. URL listesi tüm oyunun eksiksiz kapsamı değildir.

Çıktı `nmt_source_checks.json` içinde kalır; kuralları kendiliğinden değiştirmez
ve fırsat raporuna güncel fiyat etiketi taşımaz. Ağ yokken muhasebe ve örnek
senaryo raporu çalışmaya devam eder.

## Sıradaki geliştirme işleri

1. Desteklenen okuma arayüzüyle veya kullanıcı dışa aktarımıyla envanter/pazar
   verisini bağlamak; satış geçmişinin erişilebilirliğini doğrulamak.
2. Gerçekleşmiş satışın varlık maliyetini eşleştirmek, işlem kimliğiyle mükerrerliği önlemek.
3. Eksik koleksiyon parçalarını canlı ilanlarla otomatik eşleştirmek.
4. Promosyon ve etkinlik son tarihlerini aynı kanıt sözleşmesine geçirmek.
5. Power Blocks tur bazlı kural ve alan kayıtlarını eklemek; gelecekteki kareleri
   tahmin ettiği iddia edilen modeller üretmemek.

Bildirimler, otomatik alım/satım ve para hareketleri bu sürümde başlatılmaz.
