"""NMT Brain v1: Telegram ledger + read-only decision calculators."""
import base64, hashlib, json, os, shlex
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

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


def send(chat_id, text):
    tg("sendMessage", {"chat_id": chat_id, "text": text, "disable_web_page_preview": True})


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
        "version": 1, "initialized": False, "last_update_id": 0, "ledger": [],
        "settings": {"daily_outflow_limit_nmt": "0", "manual_usd_per_nmt": "0"}
    }
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    for k, v in default.items():
        data.setdefault(k, v)
    data.setdefault("settings", {})
    for k, v in default["settings"].items():
        data["settings"].setdefault(k, v)
    return data


def save_state(state):
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def get_updates(after):
    return tg("getUpdates", params={
        "offset": int(after) + 1,
        "timeout": 0,
        "allowed_updates": json.dumps(["message"]),
    }) or []


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
    if limit > 0:
        lines.append(f"Günlük gider limiti: {show(to)}/{show(limit)} NMT · kalan {show(max(Decimal('0'), limit-to))}")
    if rate > 0:
        lines.append(f"Manuel kur karşılığı: yaklaşık {show(an*rate, 6)} USD")
    lines += ["", "Marketplace alımları gider sayılır; eldeki figürlerin piyasa değeri bu sürümde hesaba katılmaz.", "", "Komutlar için /help"]
    return "\n".join(lines)


def help_text():
    return (
        "🧠 NMT BRAIN KOMUTLARI\n\n"
        "/nmt — durum\n"
        "/pb 120 — Power Blocks geliri\n"
        "/col 80 — Collection geliri\n"
        "/sell 2200 Ducko — satış geliri\n"
        "/buy 1500 Ducko — marketplace alımı\n"
        "/fee 25 çekim — komisyon/gider\n"
        "/deposit 5000 — eklenen sermaye\n"
        "/withdraw 1000 — çekilen sermaye\n"
        "/limit 3000 — günlük gider limiti\n"
        "/rate 0.00408 — manuel USD/NMT kuru\n"
        "/ev 25 — Power Blocks EV aralığı\n"
        "/ev 25 150 — 150 kazanan hücre varsayımı\n"
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
        save_state(state)
        send(chat_id, "🧠 NMT Brain v1 aktif.\n\nMuhasebe + Power Blocks EV + Collection + Lucky Buy risk hesapları hazır. Eski mesajlar işlenmedi. /help yaz.")
        print("[BRAIN] initialized")
        return
    changed, replies = False, 0
    for u in batch:
        uid = int(u["update_id"])
        state["last_update_id"] = max(int(state.get("last_update_id", 0)), uid)
        changed = True
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
    if changed:
        save_state(state)
    print(f"[BRAIN] updates={len(batch)} replies={replies} ledger={len(state['ledger'])}")


if __name__ == "__main__":
    main()
