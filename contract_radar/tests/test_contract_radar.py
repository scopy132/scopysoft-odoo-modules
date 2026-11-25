# -*- coding: utf-8 -*-
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests.common import TransactionCase


class TestContractRadar(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env.company.partner_id
        cls.today = fields.Date.context_today(cls.env['contract.radar'])

    def _make(self, **kw):
        vals = {
            'name': 'Test contract',
            'partner_id': self.partner.id,
            'date_start': self.today,
            'date_end': self.today + timedelta(days=100),
            'amount': 1000.0,
            'billing_period': 'yearly',
            'notice_days': 30,
            'cancel_by_business_day': False,  # the weekend / holiday logic has its own tests
        }
        vals.update(kw)
        return self.env['contract.radar'].create(vals)

    def test_cancel_by_is_end_minus_notice(self):
        rec = self._make()
        self.assertEqual(rec.date_cancel_by, rec.date_end - timedelta(days=30))

    def test_next_action_date_follows_auto_renew(self):
        rec = self._make(auto_renew=True)
        self.assertEqual(rec.next_action_date, rec.date_cancel_by)
        rec.action_serve_notice()
        self.assertFalse(rec.will_renew)
        self.assertEqual(rec.next_action_date, rec.date_end)

    def test_fixed_percent_escalation(self):
        rec = self._make(escalation_mode='fixed_percent', escalation_percent=10, escalation_every_months=12)
        at = rec.date_start + relativedelta(months=13)
        self.assertAlmostEqual(rec._period_amount_at(at), 1100.0, places=2)

    def test_cost_lines_monthly(self):
        rec = self._make(billing_period='monthly', amount=100.0, state='active',
                         date_end=self.today + relativedelta(months=3) - timedelta(days=1))
        self.assertEqual(len(rec.cost_line_ids), 3)
        self.assertAlmostEqual(rec.projected_total, 300.0, places=2)

    def test_cost_line_readable_name_and_form(self):
        rec = self._make(billing_period='monthly', amount=100.0, state='active',
                         date_end=self.today + relativedelta(months=3) - timedelta(days=1))
        line = rec.cost_line_ids[0]
        self.assertIn(rec.display_name, line.display_name)
        self.assertNotIn('contract.radar.cost.line', line.display_name)
        res = self.env['contract.radar.cost.line'].get_views([(False, 'form')])
        self.assertIn('form', res['views'])

    def test_renew_creates_chain_and_applies_escalation(self):
        start = self.today - relativedelta(months=12) + timedelta(days=5)
        rec = self._make(date_start=start, date_end=start + relativedelta(months=12) - timedelta(days=1),
                         state='active', escalation_mode='fixed_percent', escalation_percent=10,
                         escalation_every_months=12)
        new = rec._do_renew()
        self.assertEqual(rec.state, 'renewed')
        self.assertEqual(new.renewed_from_id, rec)
        self.assertEqual(new.date_start, rec.date_end + timedelta(days=1))
        self.assertAlmostEqual(new.amount, 1100.0, places=2)

    def test_reminder_not_sent_twice(self):
        rec = self._make(state='active', auto_renew=True, date_end=self.today + timedelta(days=40))
        rec._process_reminder(self.today)
        count = self.env['contract.radar.reminder.log'].search_count([('contract_id', '=', rec.id)])
        self.assertTrue(count)
        rec._process_reminder(self.today)
        self.assertEqual(
            self.env['contract.radar.reminder.log'].search_count([('contract_id', '=', rec.id)]), count)

    def test_dashboard_runs(self):
        self._make(state='active', auto_renew=True)
        data = self.env['contract.radar'].get_dashboard_data()
        self.assertIn('commitment', data)
        self.assertEqual(len(data['commitment']['months']), 12)
