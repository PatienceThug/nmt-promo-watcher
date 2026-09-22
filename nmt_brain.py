"""NMT Brain v1: Telegram ledger + read-only decision calculators."""
import base64, hashlib, json, os, shlex, time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
import nmt_strategy as strat

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
STATE_PATH = Path("nmt_brain_state.json")
TG_STATE_PATH = Path("telegram_state.json")
IST = ZoneInfo("Europe/Istanbul")
BOARD = Decimal("10000")
HIT_PAY = Decimal("15")


def num(v):
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        raise ValueError("Geçerli bir sayı gir.")


def show(v, places=4):
    v = Decimal(v)
    s = f"{v:.{places}f}".rstrip("0").rstrip(".")
    return s or "0"


def tg(method, payload=None, params=None):
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    last = None
    for attempt in range(3):
        try:
            if payload is not None:
                r = requests.post(url, json=payload, timeout=(10, 20))
            else:
                r = requests.get(url, params=params, timeout=(10, 20))
            r.raise_for_status()
            data = r.json()
            if not data.get("ok"):
                raise RuntimeError(f"Telegram API failed: {method}")
            return data.get("result")
        except (requests.RequestException, RuntimeError) as exc:
            last = exc
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Telegram {method} failed after retries: {last}")


def send(chat_id, text, buttons=None):
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    tg("sendMessage", payload)


def recover_chat_id(enc):
    if not BOT_TOKEN or not enc:
        return ""
    try:
        raw = base64.urlsafe_b64decode(enc.encode())
        key = hashlib.sha256((BOT_TOKEN + "::nmt-chat-id").encode()).digest()
        out = bytes(b ^ key[i % len(key)] for i, b in enumerate(raw)).decode()
        return out if out.lstrip("-").isdigit() else ""
    except Exception:
        return ""


def saved_chat_id():
    try:
        data = json.loads(TG_STATE_PATH.read_text(encoding="utf-8"))
        return recover_chat_id(data.get("chat_id_enc", ""))
    except Exception:
        return ""


def load_state():
    default = {
        "version": 2, "initialized": False, "last_update_id": 0, "ledger": [], "power_rounds": [],
        "strategy": {"footprints": [], "profile_updated_at": ""},
        "settings": {"daily_outflow_limit_nmt": "0", "manual_usd_per_nmt": "0"},
        "power": {"enabled": True, "interval_minutes": 10, "next_at": "", "last_sent_at": "", "last_placed_at": ""}
    }
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    for k, v in default.items():
        data.setdefault(k, v)
    data.setdefault("strategy", {})
    for k, v in default["strategy"].items():
        data["strategy"].setdefault(k, v)
    data.setdefault("settings", {})
    for k, v in default["settings"].items():
        data["settings"].setdefault(k, v)
    data.setdefault("power", {})
    for k, v in default["power"].items():
        data["power"].setdefault(k, v)
    return data


def save_state(state):
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def get_updates(after):
    return tg("getUpdates", params={
        "offset": int(after) + 1,
        "timeout": 0,
        "allowed_updates": json.dumps(["message", "callback_query"]),
    }) or []


def utcnow():
    return datetime.now(timezone.utc)


def set_power_due(state, minutes=None):
    mins = int(minutes if minutes is not None else state["power"].get("interval_minutes", 10))
    state["power"]["next_at"] = (utcnow() + timedelta(minutes=mins)).isoformat()


def power_due(state):
    if not state.get("power", {}).get("enabled", True):
        return False
    raw = state["power"].get("next_at") or ""
    if not raw:
        set_power_due(state)
        return False
    try:
        due = datetime.fromisoformat(raw)
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        return utcnow() >= due.astimezone(timezone.utc)
    except Exception:
        set_power_due(state)
        return False


def send_power_reminder(chat_id, state):
    send(
        chat_id,
        "⚡ POWER BLOCKS — KONTROL ZAMANI\n\n"
        "Yaklaşık 10 dakikalık pencere doldu. Yeni round'u kontrol et; yerleştirdikten sonra "
        "✅ Yerleştirdim'e basarsan sayaç o ana göre yeniden başlar.",
        buttons=[
            [{"text": "⚡ Power Blocks Aç", "url": "https://nmt.gg/power-blocks"}],
            [
                {"text": "✅ Yerleştirdim", "callback_data": "pb_placed"},
                {"text": "⏰ +5 dk", "callback_data": "pb_snooze5"},
            ],
            [{"text": "🧠 NMT Brain", "callback_data": "brain_menu"}],
        ],
    )
    state["power"]["last_sent_at"] = utcnow().isoformat()
    set_power_due(state)


def safe_answer_callback(qid, text=""):
    if not qid:
        return
    payload = {"callback_query_id": qid}
    if text:
        payload["text"] = text
    try:
        tg("answerCallbackQuery", payload)
    except Exception as exc:
        # Old Telegram callback queries can expire. The requested action must
        # still happen even when the small popup acknowledgement cannot.
        print(f"[BRAIN] callback ack skipped: {str(exc)[:140]}")


def strategy_areas(state):
    return [int(x) for x in state.get("strategy", {}).get("footprints", [])]


def strategy_profile_text(state, hours=12):
    areas = strategy_areas(state)
    if not areas:
        return (
            "🎯 POWER BLOCKS PROFİLİ AYARLANMADI\n\n"
            "Her kullandığın figürün footprint alanını yaz. Örnek:\n"
            "/pbset 1 1 1 1 1\n"
            "veya\n"
            "/pbset 5x5 2x2 1x2\n\n"
            "Bot bundan sonra gerçek hit ihtimalini ve yatırım geri dönüşünü bu profile göre hesaplayacak."
        )
    m = strat.profile_metrics(areas, hours)
    low, mid, high = m["min"], m["mid"], m["max"]
    return (
        "🎯 POWER BLOCKS PROFİLİ\n\n"
        f"Slot: {m['slots']}\n"
        f"Toplam footprint: {m['area']} kare\n"
        f"Settle başına Power maliyeti: {m['power_per_settle']}\n\n"
        f"Tek round en az 1 hit ihtimali:\n"
        f"• 100 winner: %{strat.fmt(low['hit_probability']*100, 2)}\n"
        f"• 145 winner: %{strat.fmt(mid['hit_probability']*100, 2)}\n"
        f"• 190 winner: %{strat.fmt(high['hit_probability']*100, 2)}\n\n"
        f"{strat.fmt(m['rounds'], 0)} round / {strat.fmt(hours, 1)} saat teorik EV:\n"
        f"{strat.fmt(low['ev_period'])}–{strat.fmt(high['ev_period'])} NMT "
        f"(orta {strat.fmt(mid['ev_period'])})\n\n"
        "Bu garanti kazanç değil; resmî round matematiğinin beklenen değeridir."
    )


def streak_text(state, rounds):
    areas = strategy_areas(state)
    if not areas:
        return "Önce /pbset ile footprint profilini ayarla."
    area = sum(areas)
    rounds = int(rounds)
    if rounds <= 0 or rounds > 100000:
        return "Round sayısı 1–100000 arasında olmalı."
    p = strat.zero_streak_probability(area, rounds)
    pct = p * Decimal("100")
    text = (
        f"🧪 SIFIR SERİSİ TESTİ\n\n"
        f"Alan: {area} kare\n"
        f"Round: {rounds}\n"
        f"145 winner varsayımıyla hiç hit almama olasılığı: %{strat.fmt(pct, 6)}\n"
    )
    if p < Decimal("0.001"):
        text += "\n🚨 Bu kadar uzun sıfır seri istatistiksel olarak çok sıra dışı. Placement/settle durumunu ve Explorer receipt/hash'i kontrol et."
    elif p < Decimal("0.01"):
        text += "\n⚠️ Bu seri düşük olasılıklı. Birkaç round daha kayıt al ve Explorer'da settle'ı doğrula."
    else:
        text += "\nBu seri hâlâ normal şans aralığında olabilir."
    return text


def target_text(state, target_usd, hours=12):
    rate = num(state.get("settings", {}).get("manual_usd_per_nmt", "0"))
    if rate <= 0:
        return "Önce güncel kuru /rate <USD/NMT> ile gir. Örnek: /rate 0.00408"
    area = strat.target_area_for_income(target_usd, rate, hours)
    current = sum(strategy_areas(state))
    gap = max(Decimal("0"), area - Decimal(current))
    return (
        f"🎯 GELİR HEDEFİ GERÇEKLİK TESTİ\n\n"
        f"Hedef: {show(num(target_usd), 2)} USD / {show(num(hours), 2)} saat\n"
        f"Kullanılan kur: 1 NMT = {show(rate, 6)} USD\n"
        f"145 winner ortalamasında gereken yaklaşık toplam footprint: {strat.fmt(area, 1)} kare\n"
        f"Senin kayıtlı alanın: {current} kare\n"
        f"Yaklaşık alan açığı: {strat.fmt(gap, 1)} kare\n\n"
        "Bu hesap her round aktif olduğun ve footprint'in aynı kaldığı teorik senaryodur; NFT fiyatı, Power tükenmesi ve piyasa riski dahil değildir."
    )


def upgrade_text(state, extra_area, cost_nmt):
    current = sum(strategy_areas(state))
    if current <= 0:
        return "Önce /pbset ile mevcut footprint profilini ayarla."
    r = strat.upgrade_break_even(current, int(extra_area), cost_nmt)
    return (
        "🧮 POWER BLOCKS UPGRADE TESTİ\n\n"
        f"Mevcut alan: {r['current_area']}\n"
        f"Ek alan: +{r['extra_area']}\n"
        f"Yeni toplam: {r['new_area']} kare\n"
        f"Maliyet: {strat.fmt(r['cost_nmt'])} NMT\n"
        f"145 winner varsayımıyla ek teorik günlük EV: {strat.fmt(r['extra_ev_day_nmt'])} NMT\n"
        f"Salt PB geliriyle kaba başabaş: {strat.fmt(r['naive_break_even_days'], 1)} gün\n\n"
        "⚠️ Bu kaba filtre; Power azalınca footprint düşebilir ve NFT'nin yeniden satış değeri değişebilir."
    )


def flip_text(buy_price, resale_price):
    r = strat.flip_profit(buy_price, resale_price)
    return (
        "🛒 MARKETPLACE FLIP TESTİ\n\n"
        f"Alış: {strat.fmt(r['buy'])} NMT\n"
        f"Hedef satış: {strat.fmt(r['resale'])} NMT\n"
        f"%10 fee sonrası net satış: {strat.fmt(r['net_sale'])} NMT\n"
        f"Teorik kâr: {strat.fmt(r['profit'])} NMT\n"
        f"ROI: %{strat.fmt(r['roi']*100, 2)}\n\n"
        "Likidite ve gerçekten o fiyattan alıcı bulunması bu hesaba dahil değildir."
    )


def merge_text(cost_a, cost_b, next_value):
    r = strat.merge_profit(cost_a, cost_b, next_value)
    return (
        "🔀 MERGE TESTİ\n\n"
        f"İki input toplam maliyet/değer: {strat.fmt(r['input_cost'])} NMT\n"
        f"Üst level tahmini satış değeri: {strat.fmt(num(next_value))} NMT\n"
        f"%10 fee sonrası net: {strat.fmt(r['net_sale'])} NMT\n"
        f"Teorik fark: {strat.fmt(r['profit'])} NMT\n"
        f"ROI: %{strat.fmt(r['roi']*100, 2)}\n\n"
        "Sadece aynı figür + aynı level merge kuralına uygun çiftlerde kullan."
    )


def collection_roi_text(cost_nmt, daily_nmt):
    days = strat.collection_break_even(cost_nmt, daily_nmt)
    return (
        "🧩 COLLECTION BAŞABAŞ\n\n"
        f"Tamamlama maliyeti: {strat.fmt(cost_nmt)} NMT\n"
        f"Günlük accrual: {strat.fmt(daily_nmt)} NMT\n"
        f"Kaba başabaş: {strat.fmt(days, 2)} gün\n\n"
        "Bu hesap sadece verdiğin günlük accrual sabit kalırsa geçerli; opportunity cost ve figürlerin alternatif satış değeri ayrıca düşünülmeli."
    )


def brain_menu_text(state):
    p = state.get("power", {})
    raw = p.get("next_at") or ""
    due_text = "hazırlanıyor"
    if raw:
        try:
            due = datetime.fromisoformat(raw).astimezone(IST)
            due_text = due.strftime("%H:%M:%S")
        except Exception:
            pass
    return (
        "🧠 NMT BRAIN\n\n"
        "Bu, NMT hesabına giriş yapmayan kişisel yardımcı panelin. "
        "Kazanç/gider kayıtlarını, Power Blocks verimini ve teorik hesapları burada görürsün.\n\n"
        f"⚡ Sonraki PB kontrolü: {due_text}\n"
        f"📒 Kayıtlı round: {len(state.get('power_rounds', []))}\n"
        f"💰 Muhasebe kaydı: {len(state.get('ledger', []))}"
    )


def send_brain_menu(chat_id, state):
    send(
        chat_id,
        brain_menu_text(state),
        buttons=[
            [
                {"text": "🎯 PB Profil", "callback_data": "brain_profile"},
                {"text": "📊 Durum", "callback_data": "brain_status"},
            ],
            [
                {"text": "🧪 Şans Testi", "callback_data": "brain_streak72"},
                {"text": "⚡ EV 25", "callback_data": "brain_ev25"},
            ],
            [
                {"text": "🛒 Kazanç Araçları", "callback_data": "brain_tools"},
                {"text": "📒 Komutlar", "callback_data": "brain_help"},
            ],
            [
                {"text": "⏱ Sayaç", "callback_data": "brain_timer"},
                {"text": "⚡ Power Blocks Aç", "url": "https://nmt.gg/power-blocks"}
            ],
        ],
    )


def record_round_result(state, event_id, reward, spent, note=""):
    reward = num(reward)
    spent = num(spent)
    if reward < 0 or spent <= 0:
        raise ValueError("Round değerleri geçersiz.")
    rid = str(event_id)
    if any(str(x.get("id")) == rid for x in state.get("power_rounds", [])):
        return False
    state.setdefault("power_rounds", []).append({
        "id": rid,
        "at": utcnow().isoformat(),
        "reward_nmt": str(reward),
        "power_spent": str(spent),
        "note": note[:160],
    })
    if reward > 0:
        add_entry(state, rid, "income", "power_blocks", reward, note)
    return True


def round_summary_text(state):
    rows = state.get("power_rounds", [])
    if not rows:
        return "Henüz kayıtlı Power Blocks round'u yok."
    recent = rows[-100:]
    rewards = [num(x.get("reward_nmt", "0")) for x in recent]
    powers = [num(x.get("power_spent", "0")) for x in recent]
    total_reward = sum(rewards, Decimal("0"))
    total_power = sum(powers, Decimal("0"))
    zero_count = sum(1 for x in rewards if x == 0)
    eff = total_reward / total_power if total_power > 0 else Decimal("0")
    grade = strat.evidence_grade(len(recent))
    return (
        f"📈 PB GERÇEK VERİN\n\n"
        f"Round: {len(recent)} ({grade})\n"
        f"Sıfır ödül: {zero_count}\n"
        f"Toplam ödül: {show(total_reward)} NMT\n"
        f"Harcanan Power: {show(total_power)}\n"
        f"Gerçek verim: {show(eff)} NMT/Power\n\n"
        "30 round altındayken sonuçları strateji kanıtı sayma."
    )


def handle_callback(state, chat_id, query):
    data = query.get("data") or ""
    qid = query.get("id") or ""
    if data == "pb_placed":
        state["power"]["last_placed_at"] = utcnow().isoformat()
        set_power_due(state)
        safe_answer_callback(qid, "✅ Sayaç 10 dakika için yenilendi.")
        if not strategy_areas(state):
            send(chat_id, "🎯 Bir kere /pbset ile footprint'lerini girersen bundan sonra kazanç matematiğini otomatik tutacağım.")
        return True
    if data == "pb_snooze5":
        set_power_due(state, 5)
        safe_answer_callback(qid, "⏰ 5 dakika erteledim.")
        return True
    if data.startswith("pb_reward_"):
        raw = data.removeprefix("pb_reward_")
        if raw == "other":
            safe_answer_callback(qid)
            send(chat_id, "✍️ Diğer ödül için: /round <NMT> <harcanan_Power>\nÖrnek: /round 120 5")
            return True
        areas = strategy_areas(state)
        if not areas:
            safe_answer_callback(qid)
            send(chat_id, "Önce /pbset ile footprint profilini ayarla. Örnek: /pbset 1 1 1 1 1")
            return True
        reward = num(raw)
        spent = sum(areas)
        added = record_round_result(state, "cb-" + qid, reward, spent, "Telegram hızlı sonuç")
        safe_answer_callback(qid, "✅ Round kaydedildi." if added else "Bu round zaten kayıtlı.")
        send(chat_id, round_summary_text(state))
        return True
    if data == "brain_menu":
        safe_answer_callback(qid)
        send_brain_menu(chat_id, state)
        return True
    if data == "brain_profile":
        safe_answer_callback(qid)
        send(chat_id, strategy_profile_text(state))
        return True
    if data == "brain_streak72":
        safe_answer_callback(qid)
        send(chat_id, streak_text(state, 72))
        return True
    if data == "brain_tools":
        safe_answer_callback(qid)
        send(
            chat_id,
            "🛠 KAZANÇ ARAÇLARI\n\n"
            "/upgrade <ek_kare> <maliyet_NMT>\n"
            "/flip <alış> <hedef_satış>\n"
            "/mergecalc <fig1_değer> <fig2_değer> <üst_level_değer>\n"
            "/collectionroi <tamamlama_maliyeti> <günlük_NMT>\n"
            "/target <USD_hedef> <saat>\n\n"
            "Bunlar işlem yapmaz; kötü yatırımı daha para harcamadan elemek için hesap yapar."
        )
        return True
    if data == "brain_status":
        safe_answer_callback(qid)
        send(chat_id, dashboard(state))
        return True
    if data == "brain_ev25":
        safe_answer_callback(qid)
        send(chat_id, ev_calc(["25"]))
        return True
    if data == "brain_help":
        safe_answer_callback(qid)
        send(chat_id, help_text())
        return True
    if data == "brain_timer":
        safe_answer_callback(qid)
        raw = state.get("power", {}).get("next_at") or ""
        if raw:
            try:
                due = datetime.fromisoformat(raw).astimezone(IST).strftime("%H:%M:%S")
                send(chat_id, f"⏱ Power Blocks sonraki kontrol: {due} (Türkiye saati)")
            except Exception:
                send(chat_id, "⏱ Power Blocks sayacı aktif.")
        else:
            send(chat_id, "⏱ Power Blocks sayacı aktif.")
        return True
    safe_answer_callback(qid)
    return False

def add_entry(state, update_id, kind, category, amount, note=""):
    amount = num(amount)
    if amount <= 0:
        raise ValueError("Tutar 0'dan büyük olmalı.")
    eid = f"tg-{update_id}"
    if any(x.get("id") == eid for x in state["ledger"]):
        return
    state["ledger"].append({
        "id": eid,
        "at": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "category": category,
        "amount_nmt": str(amount),
        "note": note[:160],
    })


def amount(row):
    return num(row.get("amount_nmt", "0"))


def pnl(rows):
    inc = sum((amount(x) for x in rows if x.get("kind") == "income"), Decimal("0"))
    out = sum((amount(x) for x in rows if x.get("kind") == "expense"), Decimal("0"))
    return inc, out, inc - out


def dashboard(state):
    now = datetime.now(IST)
    week = now - timedelta(days=7)
    today_rows, week_rows = [], []
    for row in state["ledger"]:
        try:
            at = datetime.fromisoformat(row["at"]).astimezone(IST)
        except Exception:
            continue
        if at.date() == now.date():
            today_rows.append(row)
        if at >= week:
            week_rows.append(row)
    ti, to, tn = pnl(today_rows)
    wi, wo, wn = pnl(week_rows)
    ai, ao, an = pnl(state["ledger"])
    limit = num(state["settings"].get("daily_outflow_limit_nmt", "0"))
    rate = num(state["settings"].get("manual_usd_per_nmt", "0"))
    lines = [
        "🧠 NMT BRAIN — DURUM", "",
        f"Bugün: +{show(ti)} / -{show(to)} = {show(tn)} NMT",
        f"7 gün: +{show(wi)} / -{show(wo)} = {show(wn)} NMT",
        f"Tüm kayıtlar net nakit akışı: {show(an)} NMT",
    ]
    rounds = state.get("power_rounds", [])
    if rounds:
        recent = rounds[-20:]
        total_reward = sum((num(x.get("reward_nmt", "0")) for x in recent), Decimal("0"))
        total_power = sum((num(x.get("power_spent", "0")) for x in recent), Decimal("0"))
        efficiency = total_reward / total_power if total_power > 0 else Decimal("0")
        lines.append(f"PB son {len(recent)} round: {show(total_reward)} NMT / {show(total_power)} Power = {show(efficiency)} NMT/Power")
        if len(recent) < 30:
            lines.append("PB örneklem: henüz küçük; edge sonucu çıkarma.")

    if limit > 0:
        lines.append(f"Günlük gider limiti: {show(to)}/{show(limit)} NMT · kalan {show(max(Decimal('0'), limit-to))}")
    if rate > 0:
        lines.append(f"Manuel kur karşılığı: yaklaşık {show(an*rate, 6)} USD")
    lines += ["", "Marketplace alımları gider sayılır; eldeki figürlerin piyasa değeri bu sürümde hesaba katılmaz.", "", "Komutlar için /help"]
    return "\n".join(lines)


def help_text():
    return (
        "🧠 NMT BRAIN KOMUTLARI\n\n"
        "🎯 STRATEJİ\n"
        "/pbset 1 1 1 1 1 — kullandığın footprint alanlarını kaydet\n"
        "/profile — gerçek PB profilini ve 12 saatlik EV'yi göster\n"
        "/streak 72 — 72 round sıfır ödül olasılığını hesapla\n"
        "/target 21 12 — 12 saatte 21 USD için gereken alanı hesapla\n"
        "/upgrade 20 1000 — +20 kare için 1000 NMT maliyetin kaba geri dönüşü\n"
        "/flip 1000 1500 — marketplace alış/satış sonrası %10 fee hesabı\n"
        "/mergecalc 400 400 1200 — iki figürü merge edip satma hesabı\n"
        "/collectionroi 5000 100 — collection maliyeti/günlük NMT başabaş\n\n"
        "⚡ POWER BLOCKS / KAYIT\n"
        "/nmt — durum\n"
        "/round 120 25 — round ödülü + harcanan Power kaydı\n"
        "/pb 120 — PB geliri kaydı\n"
        "/ev 25 — PB EV aralığı\n\n"
        "💰 MUHASEBE\n"
        "/col 80 — Collection geliri\n"
        "/sell 2200 Ducko — satış geliri\n"
        "/buy 1500 Ducko — marketplace alımı\n"
        "/fee 25 çekim — komisyon/gider\n"
        "/deposit 5000 — eklenen sermaye\n"
        "/withdraw 1000 — çekilen sermaye\n"
        "/limit 3000 — günlük gider limiti\n"
        "/rate 0.00408 — manuel USD/NMT kuru\n"
        "/colcalc 100 7 — collection accrual hesabı\n"
        "/lucky 1000 100 — Lucky Buy risk hesabı\n"
        "/undo — son muhasebe kaydını sil\n\n"
        "NMT Brain NMT hesabına giriş yapmaz ve işlem göndermez."
    )


def ev_calc(args):
    if not args:
        return "Kullanım: /ev <footprint hücresi> [kazanan hücre]"
    cells = num(args[0])
    if cells <= 0 or cells > BOARD:
        return "Footprint alanı 1–10000 arasında olmalı."
    if len(args) >= 2:
        wins = num(args[1])
        if wins <= 0 or wins > BOARD:
            return "Kazanan hücre sayısı geçersiz."
        hits = cells * wins / BOARD
        reward = hits * HIT_PAY
        return (
            f"⚡ POWER BLOCKS EV\nAlan: {show(cells)} hücre\nKazanan hücre: {show(wins)}\n"
            f"Beklenen hit: {show(hits)}\nBeklenen ödeme: {show(reward)} NMT\n"
            f"Settle güç maliyeti: {show(cells)} Power\n\n"
            "Bu kâr değildir; Power'ın fırsat maliyeti ayrıca vardır."
        )
    low_hits = cells * Decimal("100") / BOARD
    high_hits = cells * Decimal("190") / BOARD
    return (
        f"⚡ POWER BLOCKS EV ARALIĞI\nAlan: {show(cells)} hücre\n"
        f"Beklenen hit: {show(low_hits)}–{show(high_hits)}\n"
        f"Beklenen ödeme: {show(low_hits*HIT_PAY)}–{show(high_hits*HIT_PAY)} NMT\n"
        f"Settle güç maliyeti: {show(cells)} Power\n\n"
        "100–190 kazanan hücre ve 15 NMT/hit resmî mekaniklerine göre teorik beklentidir."
    )


def collection_calc(args):
    if len(args) < 2:
        return "Kullanım: /colcalc <günlük_NMT> <gün>"
    daily, days = num(args[0]), num(args[1])
    if daily < 0 or days < 0:
        return "Değerler negatif olamaz."
    pending = daily * days
    return (
        f"🧩 COLLECTION\nGünlük: {show(daily)} NMT\nSüre: {show(days)} gün\n"
        f"Teorik accrual: {show(pending)} NMT\n\n"
        "Her claim dört figürün her birinden 1 charge tüketir; tam set en fazla 10 claim destekler."
    )


def lucky_calc(args):
    if len(args) < 2:
        return "Kullanım: /lucky <listing_fiyatı> <stake>"
    price, stake = num(args[0]), num(args[1])
    if price <= 0 or stake <= 0 or stake > price:
        return "Fiyat > 0 olmalı ve stake fiyatı aşmamalı."
    display = min(Decimal("0.90"), max(Decimal("0.001"), stake / price))
    actual = display * Decimal("0.90")
    ev = actual * price - stake
    return (
        f"🎲 LUCKY BUY RİSK\nListing: {show(price)} NMT\nStake: {show(stake)} NMT\n"
        f"Yaklaşık gösterilen şans: %{show(display*100)}\n"
        f"%10 house-edge sonrası yaklaşık şans: %{show(actual*100)}\n"
        f"Figür değeri listing fiyatına eşitse teorik EV: {show(ev)} NMT\n\n"
        "Negatif beklenti riski vardır; bu hesap garanti değildir."
    )


def handle(state, uid, text):
    try:
        p = shlex.split(text.strip())
    except ValueError:
        p = text.strip().split()
    if not p:
        return None
    cmd = p[0].split("@", 1)[0].lower()
    args = p[1:]
    if cmd in ("/nmt", "/durum"):
        return dashboard(state)
    if cmd in ("/help", "/yardim", "/yardım"):
        return help_text()
    if cmd == "/round":
        if len(args) < 2:
            return "Kullanım: /round <ödül_NMT> <harcanan_Power> [not]"
        reward, spent = num(args[0]), num(args[1])
        if reward < 0 or spent <= 0:
            return "Ödül 0 veya üstü; Power 0'dan büyük olmalı."
        rid = f"tg-{uid}"
        if not any(x.get("id") == rid for x in state.get("power_rounds", [])):
            state.setdefault("power_rounds", []).append({
                "id": rid,
                "at": utcnow().isoformat(),
                "reward_nmt": str(reward),
                "power_spent": str(spent),
                "note": " ".join(args[2:])[:160],
            })
            if reward > 0:
                add_entry(state, uid, "income", "power_blocks", reward, " ".join(args[2:]))
        efficiency = reward / spent
        return (
            f"✅ Round kaydedildi: {show(reward)} NMT / {show(spent)} Power = "
            f"{show(efficiency)} NMT/Power. Toplam round verisi büyüdükçe Brain bunu karşılaştıracak."
        )
    specs = {
        "/pb": ("income", "power_blocks", "⚡ Power Blocks"),
        "/col": ("income", "collections", "🧩 Collection"),
        "/sell": ("income", "marketplace_sell", "🛒 Satış"),
        "/buy": ("expense", "marketplace_buy", "🛒 Alım"),
        "/fee": ("expense", "fee", "💸 Gider"),
        "/deposit": ("capital_in", "capital", "💰 Sermaye girişi"),
        "/withdraw": ("capital_out", "capital", "🏦 Sermaye çıkışı"),
    }
    if cmd in specs:
        if not args:
            return f"Kullanım: {cmd} <NMT> [not]"
        kind, cat, label = specs[cmd]
        add_entry(state, uid, kind, cat, args[0], " ".join(args[1:]))
        return f"✅ {label}: {show(num(args[0]))} NMT kaydedildi."
    if cmd == "/limit":
        if not args:
            return "Kullanım: /limit <NMT>"
        v = num(args[0])
        if v < 0:
            return "Limit negatif olamaz."
        state["settings"]["daily_outflow_limit_nmt"] = str(v)
        return f"🛡️ Günlük gider limiti: {show(v)} NMT" if v else "🛡️ Günlük limit kapatıldı."
    if cmd == "/rate":
        if not args:
            return "Kullanım: /rate <USD/NMT>"
        v = num(args[0])
        if v < 0:
            return "Kur negatif olamaz."
        state["settings"]["manual_usd_per_nmt"] = str(v)
        return f"💱 Manuel kur: 1 NMT = {show(v,6)} USD"
    if cmd == "/ev":
        return ev_calc(args)
    if cmd == "/colcalc":
        return collection_calc(args)
    if cmd == "/lucky":
        return lucky_calc(args)
    if cmd == "/undo":
        if not state["ledger"]:
            return "Silinecek kayıt yok."
        row = state["ledger"].pop()
        return f"↩️ Silindi: {row.get('category')} {row.get('amount_nmt')} NMT"
    if cmd.startswith("/"):
        return "Bu komutu tanımıyorum. /help yaz."
    return None


def main():
    state = load_state()
    bootstrap_changed = False
    if not state.get("power", {}).get("next_at"):
        set_power_due(state)
        bootstrap_changed = True
    chat_id = CHAT_ID or saved_chat_id()
    if not chat_id:
        raise RuntimeError("Telegram private chat ID not available")
    try:
        batch = get_updates(state.get("last_update_id", 0))
    except Exception as exc:
        print(f"[BRAIN] Telegram temporarily unavailable: {str(exc)[:180]}")
        return
    if not state.get("initialized"):
        if batch:
            state["last_update_id"] = max(int(x["update_id"]) for x in batch)
        state["initialized"] = True
        set_power_due(state)
        save_state(state)
        send(chat_id, "🧠 NMT Brain v1 aktif.\n\nMuhasebe + Power Blocks EV + Collection + Lucky Buy risk hesapları hazır. Akıllı Power Blocks sayacı da başladı. Eski mesajlar işlenmedi. /help yaz.")
        print("[BRAIN] initialized")
        return
    changed, replies = bootstrap_changed, 0
    for u in batch:
        uid = int(u["update_id"])
        state["last_update_id"] = max(int(state.get("last_update_id", 0)), uid)
        changed = True
        query = u.get("callback_query") or {}
        if query:
            qchat = ((query.get("message") or {}).get("chat") or {}).get("id", "")
            if str(qchat) == str(chat_id):
                try:
                    if handle_callback(state, chat_id, query):
                        changed = True
                except Exception as exc:
                    print(f"[BRAIN] callback error: {str(exc)[:160]}")
            continue
        m = u.get("message") or {}
        if str((m.get("chat") or {}).get("id", "")) != str(chat_id):
            continue
        text = m.get("text") or ""
        if not text.startswith("/"):
            continue
        try:
            reply = handle(state, uid, text)
        except ValueError as exc:
            reply = f"⚠️ {exc}"
        except Exception as exc:
            reply = f"⚠️ Komut işlenemedi: {str(exc)[:120]}"
        if reply:
            send(chat_id, reply)
            replies += 1
            changed = True
    if power_due(state):
        try:
            send_power_reminder(chat_id, state)
            changed = True
            print("[BRAIN] smart Power Blocks reminder delivered")
        except Exception as exc:
            print(f"[BRAIN] reminder delivery deferred: {str(exc)[:180]}")
    if changed:
        save_state(state)
    print(f"[BRAIN] updates={len(batch)} replies={replies} ledger={len(state['ledger'])}")


if __name__ == "__main__":
    main()
