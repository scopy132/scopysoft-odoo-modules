# -*- coding: utf-8 -*-
from datetime import date, timedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged

from ..models.bill_matching import classify, plan_bill_matching, window_days


@tagged('post_install', '-at_install')
class TestBillMatchingLogic(TransactionCase):
    """The matching rules, on plain numbers (no accounting data needed)."""

    def test_classify_tolerance(self):
        self.assertEqual(classify(1000.0, 1000.0, 2.0), 'ok')
        self.assertEqual(classify(1000.0, 1019.0, 2.0), 'ok')
        self.assertEqual(classify(1000.0, 1021.0, 2.0), 'over')
        self.assertEqual(classify(1000.0, 900.0, 2.0), 'under')
        self.assertEqual(classify(0.0, 5.0, 2.0), 'over')

    def test_window_days(self):
        self.assertEqual(window_days(1), 15)
        self.assertEqual(window_days(3), 45)
        self.assertEqual(window_days(12), 45)
        self.assertEqual(window_days(0), 45)

    def _lines(self, start, count, step_days=30, expected=100.0):
        return [{'id': i + 1, 'date': start + timedelta(days=step_days * i), 'expected': expected}
                for i in range(count)]

    def test_overcharge_is_flagged(self):
        today = date(2026, 6, 30)
        lines = self._lines(date(2026, 4, 1), 3)
        bills = [
            {'id': 10, 'date': date(2026, 4, 2), 'amount': 100.0},
            {'id': 11, 'date': date(2026, 5, 3), 'amount': 130.0},   # silent price rise
            {'id': 12, 'date': date(2026, 6, 1), 'amount': 100.0},
        ]
        plan = plan_bill_matching(lines, bills, today, 2.0, 15)
        self.assertEqual([plan[i]['state'] for i in (1, 2, 3)], ['ok', 'over', 'ok'])
        self.assertAlmostEqual(plan[2]['diff'], 30.0)
        self.assertEqual(plan[2]['bill_ids'], [11])

    def test_missing_bill_only_after_grace_and_when_vendor_bills_in_odoo(self):
        today = date(2026, 6, 5)
        lines = self._lines(date(2026, 4, 1), 3)
        bills = [{'id': 10, 'date': date(2026, 4, 2), 'amount': 100.0}]
        plan = plan_bill_matching(lines, bills, today, 2.0, 15)
        self.assertEqual(plan[2]['state'], 'missing')
        self.assertEqual(plan[3]['state'], 'pending', "Recent payment: the bill may still be on its way")
        # A vendor that never bills through Odoo is not flagged at all
        plan = plan_bill_matching(lines, [], today, 2.0, 15, partner_has_bills=False)
        self.assertEqual({p['state'] for p in plan.values()}, {'pending'})

    def test_future_payments_are_not_judged(self):
        today = date(2026, 6, 30)
        lines = self._lines(date(2026, 8, 1), 2)
        plan = plan_bill_matching(lines, [], today, 2.0, 15)
        self.assertEqual({p['state'] for p in plan.values()}, {'na'})

    def test_a_bill_is_never_counted_twice(self):
        today = date(2026, 6, 30)
        lines = [{'id': 1, 'date': date(2026, 4, 1), 'expected': 100.0},
                 {'id': 2, 'date': date(2026, 4, 25), 'expected': 100.0}]
        # Dated 12 April: inside the window of both payments, closer to the first.
        bills = [{'id': 10, 'date': date(2026, 4, 12), 'amount': 100.0}]
        plan = plan_bill_matching(lines, bills, today, 2.0, 15)
        self.assertEqual(plan[1]['bill_ids'], [10])
        self.assertEqual(plan[2]['bill_ids'], [])

    def test_two_bills_for_one_payment_are_summed(self):
        today = date(2026, 6, 30)
        lines = [{'id': 1, 'date': date(2026, 4, 1), 'expected': 100.0}]
        bills = [{'id': 10, 'date': date(2026, 4, 1), 'amount': 60.0},
                 {'id': 11, 'date': date(2026, 4, 3), 'amount': 40.0}]
        plan = plan_bill_matching(lines, bills, today, 2.0, 15)
        self.assertEqual(plan[1]['state'], 'ok')
        self.assertAlmostEqual(plan[1]['billed'], 100.0)


@tagged('post_install', '-at_install')
class TestBillMatchingContract(TransactionCase):
    """Wiring: the bridge loads, fields exist, and checking without accounting data is harmless."""

    def test_views_and_dashboard_load(self):
        Contract = self.env['contract.radar']
        res = Contract.get_views([(False, 'form'), (False, 'list'), (False, 'search')])
        self.assertIn('form', res['views'])
        self.env['contract.radar.cost.line'].get_views([(False, 'list'), (False, 'search')])
        data = Contract.get_dashboard_data()
        self.assertIn('bill_flags', data)

    def test_payment_form_shows_unsigned_bill_amounts(self):
        res = self.env['contract.radar.cost.line'].get_views([(False, 'form')])
        arch = res['views']['form']['arch']
        self.assertIn('amount_untaxed', arch)
        self.assertIn('amount_total', arch)
        self.assertNotIn('_signed', arch)

    def test_match_without_bills_is_safe(self):
        today = fields.Date.context_today(self.env['contract.radar'])
        rec = self.env['contract.radar'].create({
            'name': 'Bridge contract', 'partner_id': self.env.company.partner_id.id,
            'date_start': today - timedelta(days=90), 'date_end': today + timedelta(days=270),
            'amount': 100.0, 'billing_period': 'monthly', 'state': 'active',
        })
        rec.action_match_bills() if self.env.user.has_group('contract_radar.group_contract_finance') \
            else rec._match_bills()
        self.assertTrue(rec.cost_line_ids)
        self.assertEqual(rec.bill_issue_count, 0, "No vendor bills in Odoo for this partner: nothing to flag")
        rec.check_bills = False
        self.assertEqual({l.bill_state for l in rec.cost_line_ids}, {'na'})
