"""Read-only opportunity screening. No guessed prices, trading or notifications."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import argparse
import json

SECTIONS = ('inventory', 'marketplace', 'collections', 'power_blocks',
            'merge', 'token', 'promos', 'events', 'rules')
LABELS = {'missing': 'eksik', 'stale': 'eski', 'blocked': 'erişilemiyor',
          'conflict': 'çelişkili', 'unverified': 'doğrulanmamış', 'fresh': 'güncel'}


def number(value):
    try:
        n = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError('Geçerli bir sayı gerekli.')
    if not n.is_finite() or n < 0:
        raise ValueError('Sayı sonlu ve negatif olmayan bir değer olmalı.')
    return n


def stamp(value):
    d = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if d.tzinfo is None:
        raise ValueError('Zaman damgasında saat dilimi gerekli.')
    return d.astimezone(timezone.utc)


def evidence_status(evidence, now=None):
    now = now or datetime.now(timezone.utc)
    if not evidence:
        return 'missing'
    if evidence.get('status') in ('blocked', 'conflict', 'missing'):
        return evidence['status']
    try:
        age = (now - stamp(evidence['observed_at'])).total_seconds()
        ttl = number(evidence['ttl_seconds'])
        if age < 0 or ttl <= 0:
            return 'unverified'
        if age > ttl:
            return 'stale'
    except (KeyError, TypeError, ValueError):
        return 'unverified'
    if (evidence.get('status') != 'ok' or not evidence.get('source')
            or evidence.get('verification') != 'observed'):
        return 'unverified'
    return 'fresh'


def coverage(snapshot, now=None):
    return {name: evidence_status(snapshot.get('sources', {}).get(name), now)
            for name in SECTIONS}


def ledger_summary(rows):
    """Legacy user-entered ledger is not independently verified bank activity."""
    totals = {k: Decimal(0) for k in ('income', 'expense', 'capital_in', 'capital_out')}
    seen, duplicates, invalid = {}, 0, 0
    for row in rows:
        try:
            key = row['id']
            if not isinstance(key, str) or not key or row['kind'] not in totals:
                raise ValueError('Geçersiz kayıt')
            value = number(row['amount_nmt'])
            signature = (row['kind'], value, row.get('category'))
            if key in seen:
                if seen[key] != signature:
                    raise ValueError('Çelişkili kayıt kimliği')
                duplicates += 1
                continue
            seen[key] = signature
            totals[row['kind']] += value
        except (KeyError, TypeError, ValueError):
            invalid += 1
    return {**totals, 'operating_cashflow_nmt': totals['income'] - totals['expense'],
            'recorded_balance_change_nmt': (totals['capital_in'] + totals['income']
                - totals['capital_out'] - totals['expense']),
            'duplicates': duplicates, 'invalid': invalid,
            'complete': invalid == 0, 'realized_profit_nmt': None}


def screen(candidate, sources, now=None):
    """Compare alternatives over an explicit horizon, denominated only in NMT.

    Even eligible candidates are scenarios, not guaranteed or executable trades.
    Exit price must be backed by comparable sales, not merely an asking price.
    """
    result = {'id': candidate.get('id', '?'), 'eligible': False, 'reasons': []}
    try:
        kind = candidate['kind']
        if kind not in ('flip', 'collection'):
            raise ValueError('Bu sürüm flip ve collection senaryolarını destekliyor.')
        required = ['marketplace', 'rules', 'inventory']
        if kind == 'collection':
            required.append('collections')
        for name in required:
            status = evidence_status(sources.get(name), now)
            if status != 'fresh':
                result['reasons'].append(f'{name}: {LABELS[status]}')
        if evidence_status(candidate.get('evidence'), now) != 'fresh':
            result['reasons'].append('Aday fiyatı/koşulları güncel ve doğrulanmış değil.')
        buy = number(candidate['buy_nmt'])
        owned_value = number(candidate['owned_opportunity_cost_nmt'])
        exit_value = number(candidate['exit_value_nmt'])
        fee = number(candidate['fee_fraction'])
        costs = number(candidate['other_costs_nmt'])
        haircut = number(candidate['stress_haircut_fraction'])
        target = number(candidate['target_profit_nmt'])
        days = number(candidate['horizon_days'])
        if fee >= 1 or haircut > 1 or days <= 0:
            raise ValueError('Komisyon, stres oranı veya süre geçersiz.')
        assets = candidate['asset_ids']
        if (not isinstance(assets, list) or not assets
                or any(not isinstance(x, str) or not x for x in assets)
                or len(set(assets)) != len(assets)):
            raise ValueError('Benzersiz figür/ilan kimlikleri gerekli.')
        if candidate.get('exit_basis') != 'completed_sales':
            result['reasons'].append('Çıkış fiyatı gerçekleşmiş satışlarla desteklenmiyor.')
        rewards = Decimal(0)
        if kind == 'collection':
            if candidate.get('exact_slots_verified') is not True:
                result['reasons'].append('Model, nadirlik, seviye ve Idle durumu doğrulanmadı.')
            charges = number(candidate['minimum_claim_charges'])
            claims = number(candidate['planned_claims'])
            if charges != charges.to_integral_value() or claims != claims.to_integral_value():
                raise ValueError('Tahsilat hakları tam sayı olmalı.')
            if claims < 1 or claims > charges:
                result['reasons'].append('Plan için yeterli tahsilat hakkı yok.')
            if candidate.get('claim_before_unpack') is not True:
                result['reasons'].append('Açmadan önce biriken ödülü tahsil etme planı eksik.')
            rewards = number(candidate['daily_nmt']) * days
        sale = exit_value * (1 - fee)
        total_cost = buy + owned_value + costs
        base = sale + rewards - total_cost
        stressed = (sale + rewards) * (1 - haircut) - total_cost
        max_buy = (sale + rewards) * (1 - haircut) - owned_value - costs - target
        result.update(kind=kind, buy_nmt=buy, cash_required_nmt=buy + costs,
                      asset_ids=assets, horizon_days=days, base_profit_nmt=base,
                      stressed_profit_nmt=stressed, max_buy_nmt=max_buy)
        if stressed < target or stressed <= 0:
            result['reasons'].append('Stres senaryosu pozitif hedef kazancı karşılamıyor.')
        result['eligible'] = not result['reasons']
    except (KeyError, TypeError, ValueError) as exc:
        result['reasons'].append(f'Eksik/geçersiz veri: {exc}')
    return result


def select_portfolio(screened, budget):
    """Exact selection up to 18 alternatives, same horizon, no repeated assets."""
    budget = number(budget)
    items = [x for x in screened if x['eligible']]
    if len(items) > 18:
        raise ValueError('En fazla 18 uygun aday karşılaştırılabilir; aday listesini daralt.')
    if len({x['horizon_days'] for x in items}) > 1:
        raise ValueError('Portföy karşılaştırması aynı süreyi kullanan adaylar gerektirir.')
    best = {'ids': [], 'cash_required_nmt': Decimal(0), 'stressed_profit_nmt': Decimal(0)}

    def visit(i, used, cash, profit, ids):
        nonlocal best
        if profit > best['stressed_profit_nmt']:
            best = dict(ids=ids, cash_required_nmt=cash, stressed_profit_nmt=profit)
        if i == len(items):
            return
        visit(i + 1, used, cash, profit, ids)
        x = items[i]
        if not used.intersection(x['asset_ids']) and cash + x['cash_required_nmt'] <= budget:
            visit(i + 1, used.union(x['asset_ids']), cash + x['cash_required_nmt'],
                  profit + x['stressed_profit_nmt'], ids + [x['id']])

    visit(0, set(), Decimal(0), Decimal(0), [])
    return best


def report(snapshot, state, now=None):
    if snapshot.get('schema_version') != 1:
        raise ValueError('Desteklenmeyen snapshot sürümü.')
    candidates = snapshot.get('candidates', [])
    ids = [x.get('id') for x in candidates]
    if any(not isinstance(x, str) or not x for x in ids) or len(set(ids)) != len(ids):
        raise ValueError('Aday kimlikleri eksik veya tekrar ediyor.')
    screened = [screen(x, snapshot.get('sources', {}), now) for x in candidates]
    result = {'coverage': coverage(snapshot, now),
              'ledger': ledger_summary(state.get('ledger', [])), 'candidates': screened,
              'note': 'Senaryo hesabı; kazanç garantisi veya canlı işlem talimatı değildir.'}
    try:
        result['portfolio'] = select_portfolio(screened, snapshot.get('budget_nmt', 0))
    except ValueError as exc:
        result['portfolio'] = None
        result['portfolio_error'] = str(exc)
    return result


def render(result):
    lines = ['NMT BRAIN — VERİ VE FIRSAT RAPORU', result['note'], '', 'Kapsama:']
    lines += [f'{k}: {LABELS[v]}' for k, v in result['coverage'].items()]
    ledger = result['ledger']
    lines += ['', 'Kullanıcı kayıtlarına göre (bağımsız doğrulanmadı):',
              f"Gelir: {ledger['income']} NMT · Gider: {ledger['expense']} NMT",
              f"Sermaye girişi: {ledger['capital_in']} · Çekim: {ledger['capital_out']} NMT",
              f"Faaliyet nakit akışı: {ledger['operating_cashflow_nmt']} NMT",
              'Gerçekleşmiş kâr: hesaplanamadı; varlık maliyet eşleştirmesi gerekli.',
              f"Tekrar kayıt: {ledger['duplicates']} · Geçersiz kayıt: {ledger['invalid']}"]
    if not ledger['complete']:
        lines.append('MUHASEBE EKSİK: yukarıdaki toplamlar yalnızca geçerli satırları içerir.')
    lines += ['', 'Fırsatlar:']
    if not result['candidates']:
        lines.append('Henüz aday verisi yok; bu, piyasada fırsat olmadığı anlamına gelmez.')
    for item in result['candidates']:
        if item['eligible']:
            lines.append(f"{item['id']}: incelemeye uygun senaryo; stres neti "
                         f"{item['stressed_profit_nmt']} NMT; azami alış {item['max_buy_nmt']} NMT")
        else:
            lines.append(f"{item['id']}: bekle — " + '; '.join(item['reasons']))
    portfolio = result.get('portfolio')
    if portfolio is not None:
        lines.append('Bütçe ve figür çakışması kontrolüyle seçilen: ' +
                     (', '.join(portfolio['ids']) or 'yok'))
    else:
        lines.append('Seçim yapılamadı: ' + result['portfolio_error'])
    return '\n'.join(lines)


def load_snapshot(path='nmt_opportunities.json'):
    p = Path(path)
    if not p.exists():
        return {'schema_version': 1, 'sources': {}, 'candidates': [], 'budget_nmt': '0'}
    return json.loads(p.read_text(encoding='utf-8'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', default='nmt_opportunities.json')
    parser.add_argument('--state', default='nmt_brain_state.json')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    try:
        state_path = Path(args.state)
        if not state_path.exists():
            raise ValueError('Muhasebe state dosyası bulunamadı.')
        result = report(load_snapshot(args.snapshot), json.loads(state_path.read_text()))
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str) if args.json else render(result))
    except (ValueError, OSError, TypeError) as exc:
        parser.exit(2, f'Rapor oluşturulamadı: {exc}\n')


if __name__ == '__main__':
    main()
