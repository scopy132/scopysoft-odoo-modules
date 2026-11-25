# -*- coding: utf-8 -*-
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import Form
from odoo.tests.common import TransactionCase
from odoo.tools import file_path


class TestContractRadarSmoke(TransactionCase):
    """Views, import, crons, amendments, templates and security. Uses existing records only
    (no new partners/users) so it does not depend on the state of the host database."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Contract = cls.env['contract.radar']
        cls.partner = cls.env.company.partner_id
        cls.admin = cls.env.ref('base.user_admin')
        cls.demo = cls.env.ref('base.user_demo', raise_if_not_found=False)
        cls.today = fields.Date.context_today(cls.Contract)
        cls.g_viewer = cls.env.ref('contract_radar.group_contract_viewer')
        cls.g_finance = cls.env.ref('contract_radar.group_contract_finance')
        cls.g_conf = cls.env.ref('contract_radar.group_contract_confidential')

    def _make(self, **kw):
        vals = {
            'name': 'Smoke contract',
            'partner_id': self.partner.id,
            'user_id': self.admin.id,
            'date_start': self.today,
            'date_end': self.today + timedelta(days=100),
            'amount': 1000.0,
            'billing_period': 'yearly',
            'notice_days': 30,
        }
        vals.update(kw)
        return self.Contract.create(vals)

    # ---------------------------------------------------------------- views
    def test_all_views_load(self):
        res = self.Contract.get_views([(False, 'list'), (False, 'form'), (False, 'kanban'), (False, 'search')])
        self.assertEqual(set(res['views']), {'list', 'form', 'kanban', 'search'})
        Line = self.env['contract.radar.cost.line']
        res = Line.get_views([(False, 'pivot'), (False, 'graph'), (False, 'list'), (False, 'search')])
        self.assertEqual(set(res['views']), {'pivot', 'graph', 'list', 'search'})
        self.env['contract.radar.reminder.log'].get_views([(False, 'list')])
        self.env['contract.radar.amendment'].get_views([(False, 'list'), (False, 'form')])

    def test_pdf_report_template_renders(self):
        rec = self._make(state='active')
        html, _fmt = self.env['ir.actions.report']._render_qweb_html(
            'contract_radar.report_contract_summary', rec.ids)
        self.assertIn(b'Contract Summary', html)

    # ---------------------------------------------------------------- import
    def test_import_template_file_exists_and_loads(self):
        templates = self.Contract.get_import_templates()
        self.assertTrue(templates)
        self.assertTrue(file_path('contract_radar/static/xls/contract_import_template.csv'))
        res = self.Contract.load(
            ['name', 'contract_type', 'partner_id', 'state', 'date_start', 'date_end', 'amount',
             'billing_period', 'auto_renew', 'notice_days'],
            [['Imported lease', 'Lease', self.partner.name, 'Active',
              str(self.today), str(self.today + timedelta(days=200)), '5000', 'Yearly', 'True', '60']])
        self.assertFalse(res.get('messages'), res.get('messages'))
        self.assertTrue(res['ids'])

    # ---------------------------------------------------------------- logic
    def test_scheduled_escalation(self):
        rec = self._make(escalation_mode='scheduled')
        self.env['contract.radar.escalation'].create({
            'contract_id': rec.id, 'date': self.today + relativedelta(months=6),
            'step_type': 'percent', 'value': 10})
        self.assertAlmostEqual(rec._period_amount_at(self.today + relativedelta(months=7)), 1100.0, places=2)
        self.assertAlmostEqual(rec._period_amount_at(self.today + relativedelta(months=2)), 1000.0, places=2)
        self.env['contract.radar.escalation'].create({
            'contract_id': rec.id, 'date': self.today + relativedelta(months=9),
            'step_type': 'amount', 'value': 2000})
        self.assertAlmostEqual(rec._period_amount_at(self.today + relativedelta(months=10)), 2000.0, places=2)

    def test_amendment_apply(self):
        rec = self._make()
        new_end = rec.date_end + timedelta(days=90)
        amend = self.env['contract.radar.amendment'].create({
            'contract_id': rec.id, 'name': 'Extension', 'new_date_end': new_end, 'new_amount': 1500.0})
        amend.action_apply()
        self.assertEqual(rec.date_end, new_end)
        self.assertAlmostEqual(rec.amount, 1500.0, places=2)
        self.assertTrue(amend.applied)
        with self.assertRaises(UserError):
            amend.action_apply()

    def test_template_prefills_contract(self):
        tpl = self.Contract.create({
            'name': 'Template', 'is_template': True, 'notice_days': 45, 'billing_period': 'monthly',
            'amount': 500.0, 'auto_renew': True, 'term_months': 6})
        self.assertTrue(tpl.reference.startswith('TPL/'))
        form = Form(self.Contract)  # not saved: we only check the onchange
        form.template_id = tpl
        self.assertEqual(form.notice_days, 45)
        self.assertEqual(form.billing_period, 'monthly')
        self.assertTrue(form.auto_renew)

    def test_cron_auto_renews_and_expires(self):
        past_start = self.today - timedelta(days=400)
        auto = self._make(state='active', auto_renew=True, date_start=past_start,
                          date_end=self.today - timedelta(days=10))
        plain = self._make(state='active', auto_renew=False, date_start=past_start,
                           date_end=self.today - timedelta(days=10))
        self.Contract._cron_daily_update()
        self.assertEqual(auto.state, 'renewed')
        self.assertTrue(auto.renewed_to_ids)
        self.assertGreaterEqual(auto.renewed_to_ids[0].date_end, self.today)
        self.assertEqual(plain.state, 'expired')

    def test_cron_marks_expiring(self):
        rec = self._make(state='active', date_end=self.today + timedelta(days=40))
        self.Contract._cron_daily_update()
        self.assertEqual(rec.state, 'expiring')

    def test_weekly_digest_runs(self):
        self._make(state='active', auto_renew=True, date_end=self.today + timedelta(days=20))
        self.Contract._cron_weekly_digest()

    def test_no_owner_and_no_end_date_filters(self):
        rec = self._make(state='active', user_id=False, date_end=False)
        found = self.Contract.search([('id', '=', rec.id), ('user_id', '=', False), ('date_end', '=', False)])
        self.assertEqual(found, rec)

    # ---------------------------------------------------------------- security
    def test_viewer_cannot_write_and_confidential_hidden(self):
        if not self.demo:
            self.skipTest("demo user not available")
        self.demo.write({'group_ids': [(4, self.g_viewer.id)]})
        open_c = self._make(name='Open', state='active')
        secret = self._make(name='Secret', state='active', visibility='followers')
        ids = [open_c.id, secret.id]
        seen = self.Contract.with_user(self.demo).search([('id', 'in', ids)])
        self.assertEqual(seen, open_c)
        lines = self.env['contract.radar.cost.line'].with_user(self.demo).search([('contract_id', '=', secret.id)])
        self.assertFalse(lines)
        with self.assertRaises(AccessError):
            open_c.with_user(self.demo).write({'name': 'Hacked'})

    def test_confidential_group_and_finance_rights(self):
        if not self.demo:
            self.skipTest("demo user not available")
        self.demo.write({'group_ids': [(4, self.g_finance.id), (4, self.g_conf.id)]})
        secret = self._make(name='Secret', state='active', visibility='followers')
        seen = self.Contract.with_user(self.demo).search([('id', '=', secret.id)])
        self.assertEqual(seen, secret)
        secret.with_user(self.demo).write({'name': 'Renamed by finance'})
        self.assertEqual(secret.name, 'Renamed by finance')
        with self.assertRaises(AccessError):
            secret.with_user(self.demo).unlink()

    def test_responsible_user_sees_own_confidential(self):
        if not self.demo:
            self.skipTest("demo user not available")
        self.demo.write({'group_ids': [(4, self.g_viewer.id)]})
        mine = self._make(name='Mine', state='active', visibility='followers', user_id=self.demo.id)
        seen = self.Contract.with_user(self.demo).search([('id', '=', mine.id)])
        self.assertEqual(seen, mine)
