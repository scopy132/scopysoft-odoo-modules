# -*- coding: utf-8 -*-
"""Pure matching logic, kept free of the ORM so it can be tested with plain numbers."""
from datetime import timedelta

BILL_STATES = [
    ('na', 'Not Checked'),
    ('pending', 'Awaiting Bill'),
    ('ok', 'Matches'),
    ('over', 'Over-billed'),
    ('under', 'Under-billed'),
    ('missing', 'No Bill Found'),
]
ISSUE_STATES = ('over', 'missing')
MAX_WINDOW_DAYS = 45
MIN_WINDOW_DAYS = 5
MISSING_GRACE_DAYS = 10
CENT = 0.005


def window_days(period_months):
    """How far from the scheduled payment date a bill may be dated and still belong to that payment."""
    if not period_months:  # one-time payments: bills can arrive well before or after the date
        return MAX_WINDOW_DAYS
    return max(MIN_WINDOW_DAYS, min(MAX_WINDOW_DAYS, int(period_months * 15)))


def classify(expected, billed, tolerance_pct):
    """'ok', 'over' or 'under' for one payment given what was billed."""
    tolerance = abs(expected) * (tolerance_pct or 0.0) / 100.0
    diff = billed - expected
    if abs(diff) <= tolerance + CENT:
        return 'ok'
    return 'over' if diff > 0 else 'under'


def plan_bill_matching(lines, bills, today, tolerance_pct, window, partner_has_bills=True,
                       grace_days=MISSING_GRACE_DAYS):
    """Match bills to scheduled payments.

    :param lines: list of dicts ``{'id', 'date', 'expected'}``
    :param bills: list of dicts ``{'id', 'date', 'amount'}`` (amount already in the contract currency)
    :returns: ``{line_id: {'state', 'bill_ids', 'billed', 'diff'}}``

    Each bill goes to the nearest scheduled payment within ``window`` days (the earlier one on a tie),
    so overlapping windows never count a bill twice.
    """
    assigned = {line['id']: [] for line in lines}
    for bill in bills:
        best = None
        for line in lines:
            gap = abs((bill['date'] - line['date']).days)
            if gap > window:
                continue
            if best is None or gap < best[0] or (gap == best[0] and line['date'] < best[1]['date']):
                best = (gap, line)
        if best:
            assigned[best[1]['id']].append(bill)
    result = {}
    for line in lines:
        matched = assigned[line['id']]
        if matched:
            billed = sum(b['amount'] for b in matched)
            result[line['id']] = {
                'state': classify(line['expected'], billed, tolerance_pct),
                'bill_ids': [b['id'] for b in matched],
                'billed': billed,
                'diff': billed - line['expected'],
            }
        elif line['date'] + timedelta(days=grace_days) < today and partner_has_bills:
            result[line['id']] = {'state': 'missing', 'bill_ids': [], 'billed': 0.0, 'diff': 0.0}
        elif line['date'] <= today:
            result[line['id']] = {'state': 'pending', 'bill_ids': [], 'billed': 0.0, 'diff': 0.0}
        else:
            result[line['id']] = {'state': 'na', 'bill_ids': [], 'billed': 0.0, 'diff': 0.0}
    return result
