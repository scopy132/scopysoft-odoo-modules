# -*- coding: utf-8 -*-
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

WEBHOOK_POST = 'odoo.addons.contract_radar.models.contract_webhook.requests.post'


@tagged('post_install', '-at_install')
class TestContractRadarV11(TransactionCase):
    """Business-day deadlines, renewal decisions and savings, notice wizard, webhooks, lean cron."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = cls.env.ref('base.user_admin')
        cls.env = cls.env(user=cls.admin.id)  # the admin is a contract manager; the default test user is not
        cls.Contract = cls.env['contract.radar']
        cls.partner = cls.env.company.partner_id
        cls.today = fields.Date.context_today(cls.Contract)
        # A Monday-to-Friday calendar so the weekend tests do not depend on the host database.
        cls.calendar = cls.env['resource.calendar'].create({
            'name': 'Radar test week',
            'attendance_ids': [(0, 0, {
                'name': 'Day %s' % d, 'dayofweek': str(d), 'hour_from': 8.0, 'hour_to': 16.0,
            }) for d in range(5)],
        })
        cls.env.company.resource_calendar_id = cls.calendar
        # Other webhooks on the host database must not be counted by the webhook tests.
        cls.env['contract.radar.webhook'].sudo().with_context(active_test=False).search([]).write({'active': False})

    def _make(self, **kw):
        vals = {
            'name': 'V11 contract',
            'partner_id': self.partner.id,
            'user_id': self.admin.id,
            'date_start': self.today,
            'date_end': self.today + timedelta(days=100),
            'amount': 1000.0,
            'billing_period': 'yearly',
            'notice_days': 30,
            'cancel_by_business_day': False,
        }
        vals.update(kw)
        return self.Contract.create(vals)

    # ------------------------------------------------------------ business days
    def test_saturday_deadline_moves_to_friday(self):
        # 2026-12-05 is a Saturday: end date 2027-01-04 minus 30 days.
        rec = self._make(date_start=date(2026, 1, 5), date_end=date(2027, 1, 4), cancel_by_business_day=True)
        self.assertEqual(rec.date_cancel_by, date(2026, 12, 4))
        self.assertTrue(rec.cancel_by_note)

    def test_working_day_deadline_is_untouched(self):
        # 2026-12-02 is a Wednesday.
        rec = self._make(date_start=date(2026, 1, 5), date_end=date(2027, 1, 1), cancel_by_business_day=True)
        self.assertEqual(rec.date_cancel_by, date(2026, 12, 2))
        self.assertFalse(rec.cancel_by_note)

    def test_public_holiday_moves_deadline_earlier(self):
        self.env['resource.calendar.leaves'].create({
            'name': 'Test holiday',
            'calendar_id': self.calendar.id,
            'date_from': datetime(2026, 12, 3, 0, 0, 0),
            'date_to': datetime(2026, 12, 3, 23, 59, 59),
        })
        # End date 2027-01-02 minus 30 days is Thursday 2026-12-03, the holiday.
        rec = self._make(date_start=date(2026, 1, 5), date_end=date(2027, 1, 2), cancel_by_business_day=True)
        self.assertEqual(rec.date_cancel_by, date(2026, 12, 2))

    def test_switch_off_keeps_plain_arithmetic(self):
        rec = self._make(date_start=date(2026, 1, 5), date_end=date(2027, 1, 4), cancel_by_business_day=False)
        self.assertEqual(rec.date_cancel_by, date(2026, 12, 5))

    # ------------------------------------------------------------ decisions and savings
    def _ready_decision(self, **kw):
        vals = {'state': 'active', 'auto_renew': True}
        vals.update(kw)
        return self._make(**vals)

    def test_decision_needs_reason(self):
        rec = self._ready_decision()
        rec.decision = 'cancel'
        with self.assertRaises(UserError):
            rec.action_submit_decision()

    def test_renegotiate_needs_agreed_amount(self):
        rec = self._ready_decision()
        rec.write({'decision': 'renegotiate', 'decision_reason': 'Too expensive'})
        with self.assertRaises(UserError):
            rec.action_submit_decision()

    def test_cancel_saving_counts_after_notice(self):
        rec = self._ready_decision()
        rec.write({'decision': 'cancel', 'decision_reason': 'Not used any more'})
        rec.action_submit_decision()
        self.assertEqual(rec.decision_state, 'pending')
        self.assertEqual(rec.decision_baseline_amount, rec.renewal_amount)
        rec.action_approve_decision()
        self.assertEqual(rec.decision_state, 'approved')
        self.assertEqual(rec.saving_amount, 0.0, "No saving until the notice is actually served")
        rec.action_serve_notice()
        self.assertAlmostEqual(rec.saving_amount, rec.decision_baseline_amount, places=2)
        self.assertGreater(rec.saving_amount, 0.0)

    def test_renegotiation_saving_and_new_price(self):
        rec = self._ready_decision(date_end=self.today - timedelta(days=1), date_start=self.today - timedelta(days=366))
        baseline = rec.renewal_amount
        rec.write({'decision': 'renegotiate', 'decision_reason': 'Competitor quote',
                   'decision_new_amount': baseline - 200.0})
        rec.action_submit_decision()
        rec.action_approve_decision()
        self.assertAlmostEqual(rec.saving_amount, 200.0, places=2)
        new = rec._do_renew()
        self.assertAlmostEqual(new.amount, baseline - 200.0, places=2)
        self.assertEqual(new.decision_state, 'none', "A renewal starts with a clean decision")
        self.assertEqual(rec.decision_state, 'approved', "The old term keeps its decision and saving")

    def test_reject_then_resubmit(self):
        rec = self._ready_decision()
        rec.write({'decision': 'renew', 'decision_reason': 'Good value'})
        rec.action_submit_decision()
        rec.action_reject_decision()
        self.assertEqual(rec.decision_state, 'rejected')
        rec.action_submit_decision()
        self.assertEqual(rec.decision_state, 'pending')

    def test_only_managers_approve(self):
        rec = self._ready_decision()
        rec.write({'decision': 'renew', 'decision_reason': 'Fine'})
        rec.action_submit_decision()
        user = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Radar finance user', 'login': 'radar_finance_v11',
            'email': 'radar_finance_v11@example.com',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                  self.env.ref('contract_radar.group_contract_finance').id])],
        })
        with self.assertRaises(AccessError):
            rec.with_user(user).action_approve_decision()

    def test_dashboard_has_savings(self):
        rec = self._ready_decision()
        rec.write({'decision': 'cancel', 'decision_reason': 'Unused'})
        rec.action_submit_decision()
        rec.action_approve_decision()
        rec.action_serve_notice()
        data = self.Contract.get_dashboard_data()
        self.assertGreaterEqual(data['savings']['count'], 1)
        self.assertIn('pending_decisions', data['flags'])

    # ------------------------------------------------------------ notice wizard
    def test_notice_wizard_serves_and_files_the_letter(self):
        rec = self._ready_decision()
        wizard = self.env['contract.radar.notice.wizard'].with_context(default_contract_id=rec.id).create({
            'send_email': False,
        })
        self.assertIn('does not wish to renew', wizard.body)
        wizard.action_confirm()
        self.assertEqual(rec.notice_sent_date, self.today)
        self.assertTrue(rec.notice_body)
        self.assertTrue(self.env['ir.attachment'].search_count([
            ('res_model', '=', 'contract.radar'), ('res_id', '=', rec.id)]))
        with self.assertRaises(UserError):
            wizard.action_confirm()

    def test_notice_wizard_stamp_only(self):
        rec = self._ready_decision()
        wizard = self.env['contract.radar.notice.wizard'].with_context(default_contract_id=rec.id).create({
            'generate_letter': False,
        })
        wizard.action_confirm()
        self.assertTrue(rec.notice_sent_date)
        self.assertFalse(rec.notice_body)

    def test_notice_wizard_email_needs_recipient(self):
        rec = self._ready_decision()
        wizard = self.env['contract.radar.notice.wizard'].with_context(default_contract_id=rec.id).create({
            'send_email': True, 'partner_ids': [(5, 0, 0)],
        })
        with self.assertRaises(UserError):
            wizard.action_confirm()
        self.assertFalse(rec.notice_sent_date)

    def test_notice_report_renders(self):
        rec = self._ready_decision()
        rec.write({'notice_body': '<p>Hello</p>', 'notice_sent_date': self.today})
        html, _fmt = self.env['ir.actions.report']._render_qweb_html('contract_radar.report_cancellation_notice', rec.ids)
        self.assertIn(b'Notice of non-renewal', html)

    # ------------------------------------------------------------ webhooks
    def test_webhook_url_must_be_public_https(self):
        Hook = self.env['contract.radar.webhook']
        for bad in ('http://example.com/x', 'https://localhost/x', 'https://127.0.0.1/x', 'https://10.0.0.5/x', 'x'):
            with self.assertRaises(ValidationError, msg=bad):
                Hook.create({'name': 'bad', 'url': bad})
        Hook.create({'name': 'ok', 'url': 'https://hooks.example.com/abc'})

    def test_webhook_sent_on_notice(self):
        hook = self.env['contract.radar.webhook'].create({
            'name': 'Chat', 'url': 'https://hooks.example.com/abc', 'company_id': False})
        rec = self._ready_decision()
        with patch(WEBHOOK_POST, return_value=MagicMock(ok=True, status_code=200)) as post:
            rec.action_serve_notice()
        self.assertEqual(post.call_count, 1)
        self.assertIn('text', post.call_args.kwargs['json'])
        self.assertEqual(hook.last_status, '200')

    def test_webhook_skips_confidential_and_survives_errors(self):
        import requests
        self.env['contract.radar.webhook'].create({
            'name': 'Chat', 'url': 'https://hooks.example.com/abc', 'company_id': False})
        secret = self._ready_decision(visibility='followers')
        with patch(WEBHOOK_POST) as post:
            secret.action_serve_notice()
        self.assertEqual(post.call_count, 0)
        rec = self._ready_decision()
        with patch(WEBHOOK_POST, side_effect=requests.exceptions.ConnectionError()):
            rec.action_serve_notice()  # must not raise
        self.assertTrue(rec.notice_sent_date)

    # ------------------------------------------------------------ lean cron
    def test_daily_rebuild_only_for_rolling_or_foreign(self):
        fixed = self._ready_decision(auto_renew=False)
        rolling = self._ready_decision(auto_renew=True)
        self.assertFalse(fixed._needs_daily_rebuild())
        self.assertTrue(rolling._needs_daily_rebuild())

    def test_cron_still_runs(self):
        self._ready_decision()
        self.Contract._cron_daily_update()
