# -*- coding: utf-8 -*-
import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError
from odoo.tools.misc import format_amount

from .bill_matching import ISSUE_STATES, MAX_WINDOW_DAYS, plan_bill_matching, window_days

_logger = logging.getLogger(__name__)

# Contracts in these states are expected to be billed. Draft and cancelled ones are not.
BILL_CHECK_STATES = ('active', 'expiring', 'renewed', 'expired')
LOOKBACK_DAYS = 365          # older payments are not judged: their bills may predate Odoo
CHUNK = 100
PERIOD_MONTHS = {'monthly': 1, 'quarterly': 3, 'semiannual': 6, 'yearly': 12, 'one_time': 0}
MATCH_FIELDS = {'check_bills', 'bill_tolerance_percent', 'bill_basis'}


class ContractRadar(models.Model):
    _inherit = 'contract.radar'

    check_bills = fields.Boolean(
        string='Check Vendor Bills', default=True,
        help="Compare posted vendor bills of the counterparty with the agreed payments of this contract. "
             "Untick it for contracts you do not pay through Odoo vendor bills.")
    bill_tolerance_percent = fields.Float(
        string='Tolerance %', default=2.0, digits=(16, 2),
        help="A bill within this percentage of the agreed amount counts as matching.")
    bill_basis = fields.Selection(
        [('untaxed', 'Amount before tax'), ('total', 'Total with tax')], string='Compare Bills On',
        default='untaxed', required=True,
        help="Which bill amount is compared with the contract amount. Use 'Amount before tax' when the "
             "contract price excludes VAT.")
    bill_last_check = fields.Datetime(string='Bills Last Checked', readonly=True, copy=False)
    bill_issue_count = fields.Integer(
        string='Bill Issues', compute='_compute_bill_issues', store=True)
    bill_overcharge = fields.Monetary(
        string='Overbilled Amount', currency_field='currency_id', compute='_compute_bill_issues', store=True,
        help="Total billed above the agreed amounts over the last 12 months.")

    @api.depends('cost_line_ids.bill_state', 'cost_line_ids.bill_difference')
    def _compute_bill_issues(self):
        for rec in self:
            lines = rec.cost_line_ids
            rec.bill_issue_count = len(lines.filtered(lambda l: l.bill_state in ISSUE_STATES))
            rec.bill_overcharge = sum(l.bill_difference for l in lines if l.bill_state == 'over')

    # ------------------------------------------------------------------
    # Hooks into the core module
    # ------------------------------------------------------------------
    def _rebuild_cost_lines(self):
        res = super()._rebuild_cost_lines()
        self._match_bills()
        return res

    def write(self, vals):
        res = super().write(vals)
        if MATCH_FIELDS & set(vals):
            self._match_bills()
        return res

    @api.model
    def _cron_daily_update(self):
        res = super()._cron_daily_update()
        ids = self.search([
            ('is_template', '=', False), ('check_bills', '=', True), ('flow', '=', 'cost'),
            ('state', 'in', list(BILL_CHECK_STATES)),
        ]).ids
        for start in range(0, len(ids), CHUNK):
            try:
                with self.env.cr.savepoint():
                    self.browse(ids[start:start + CHUNK])._match_bills()
            except Exception:
                _logger.exception("Contract Guard: bill matching failed for a batch of contracts")
        return res

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------
    def action_match_bills(self):
        if not self.env.user.has_group('contract_radar.group_contract_finance'):
            raise AccessError(_("Only finance users can run the bill check."))
        self._match_bills()
        return True

    def _match_bills(self):
        """Compare posted vendor bills with the scheduled payments. Reads accounting data, writes only
        to this module's own fields."""
        recs = self.exists().filtered(lambda r: not r.is_template and r.active and r.flow == 'cost')
        if not recs:
            return
        today = fields.Date.context_today(self)
        todo = recs.filtered(lambda r: r.check_bills and r.state in BILL_CHECK_STATES)

        # Contracts that are no longer checked: clear what was flagged before.
        stale = (recs - todo).sudo().cost_line_ids.filtered(lambda l: l.bill_state != 'na')
        if stale:
            stale.write({'bill_state': 'na', 'bill_ids': [(5, 0, 0)], 'billed_amount': 0.0, 'bill_difference': 0.0})
        if not todo:
            return

        commercial = todo.mapped('partner_id.commercial_partner_id')
        if not commercial:
            return
        first_day = today - timedelta(days=LOOKBACK_DAYS + MAX_WINDOW_DAYS)
        bills = self.env['account.move'].sudo().search([
            ('move_type', '=', 'in_invoice'),
            ('state', '=', 'posted'),
            ('partner_id', 'child_of', commercial.ids),
            ('company_id', 'in', todo.company_id.ids),
            ('invoice_date', '>=', first_day),
        ])
        by_key = {}
        for bill in bills:
            key = (bill.partner_id.commercial_partner_id.id, bill.company_id.id)
            by_key.setdefault(key, []).append(bill)

        for rec in todo:
            rec._apply_bill_plan(by_key.get((rec.partner_id.commercial_partner_id.id, rec.company_id.id), []), today)
        todo.write({'bill_last_check': fields.Datetime.now()})

    def _apply_bill_plan(self, partner_bills, today):
        self.ensure_one()
        lines = self.sudo().cost_line_ids.filtered(lambda l: l.date >= today - timedelta(days=LOOKBACK_DAYS))
        if not lines:
            return
        months = PERIOD_MONTHS.get(self.billing_period, 0)
        window = window_days(months)
        bill_dicts = []
        for bill in partner_bills:
            amount = bill.amount_untaxed if self.bill_basis == 'untaxed' else bill.amount_total
            if bill.currency_id != self.currency_id:
                amount = bill.currency_id._convert(
                    amount, self.currency_id, self.company_id, bill.invoice_date or today)
            bill_dicts.append({'id': bill.id, 'date': bill.invoice_date or today, 'amount': amount})
        plan = plan_bill_matching(
            [{'id': l.id, 'date': l.date, 'expected': l.amount} for l in lines],
            bill_dicts, today, self.bill_tolerance_percent, window, partner_has_bills=bool(partner_bills))
        rounding = self.currency_id
        for line in lines:
            result = plan[line.id]
            vals = {
                'bill_state': result['state'],
                'billed_amount': rounding.round(result['billed']),
                'bill_difference': rounding.round(result['diff']),
            }
            if set(result['bill_ids']) != set(line.bill_ids.ids):
                vals['bill_ids'] = [(6, 0, result['bill_ids'])]
            changed = {k: v for k, v in vals.items() if k == 'bill_ids' or line[k] != v}
            if changed:
                line.write(changed)

    # ------------------------------------------------------------------
    # Views and dashboard
    # ------------------------------------------------------------------
    def action_view_bill_issues(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Bill Check: %s", self.name),
            'res_model': 'contract.radar.cost.line',
            'view_mode': 'list,form',
            'domain': [('contract_id', '=', self.id), ('bill_state', 'not in', ('na', 'pending'))],
        }

    @api.model
    def get_dashboard_data(self):
        data = super().get_dashboard_data()
        today = fields.Date.context_today(self)
        company = self.env.company
        cur = company.currency_id
        dom = [('is_template', '=', False), ('state', 'in', ['active', 'expiring']),
               ('check_bills', '=', True), ('bill_issue_count', '>', 0)]
        recs = self.search(dom)
        total = sum(r.currency_id._convert(r.bill_overcharge, cur, r.company_id or company, today) for r in recs)
        data['bill_flags'] = {
            'count': len(recs),
            'value': format_amount(self.env, total, cur),
            'domain': dom,
        }
        return data
