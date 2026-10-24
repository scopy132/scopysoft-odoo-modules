# -*- coding: utf-8 -*-
import logging

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class PaymentReconConfig(models.Model):
    _name = 'payment.recon.config'
    _description = 'Smart Reconcile Settings (one per company)'
    _rec_name = 'company_id'

    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company)
    combo_enabled = fields.Boolean(
        string='Suggest Payment Combinations by Amount', default=True,
        help="When payments have no reference, look for 2 or 3 payments of the same partner that add up exactly "
             "to one invoice. Suggestions are always unticked and marked low confidence.")
    combo_window_days = fields.Integer(string='Combination Window (days)', default=90,
                                       help="Only payments dated within this many days of the invoice are combined.")
    auto_run = fields.Boolean(
        string='Nightly Auto-Run', default=False,
        help="Every night, reconcile the high-confidence matches automatically and send a summary. "
             "Split, combination and adjustment matches are never reconciled automatically.")
    auto_include_amount = fields.Boolean(
        string='Also Auto-Reconcile Amount-Only Matches', default=False,
        help="By default only matches with amount AND reference are reconciled overnight.")
    notify_user_ids = fields.Many2many('res.users', 'payment_recon_config_user_rel', 'config_id', 'user_id',
                                       string='Send Summary To')
    last_run = fields.Datetime(readonly=True)
    last_summary = fields.Text(readonly=True)

    @api.constrains('company_id')
    def _check_unique_company(self):
        for rec in self:
            if self.search_count([('company_id', '=', rec.company_id.id)]) > 1:
                raise ValidationError(_("There is already a settings record for this company."))

    @api.model
    def _get(self, company=None):
        company = company or self.env.company
        rec = self.sudo().search([('company_id', '=', company.id)], limit=1)
        return rec or self.sudo().create({'company_id': company.id})

    @api.model
    def _action_open(self):
        rec = self._get(self.env.company)
        return {
            'type': 'ir.actions.act_window',
            'name': _("Smart Reconcile Settings"),
            'res_model': self._name,
            'res_id': rec.id,
            'views': [[False, 'form']],
            'view_mode': 'form',
            'target': 'current',
        }

    def action_run_now(self):
        """Run the nightly job right now for this company (same code as the schedule), whatever the switch says.
        Useful to check the result and the summary email before relying on the schedule."""
        self.ensure_one()
        done = self._run_auto()
        emails = [e for e in self.notify_user_ids.mapped('email') if e]
        msg = _("%d reconciled.") % done + " " + (
            _("Summary queued for %s.") % ", ".join(emails) if emails else
            _("No summary email sent: choose who receives it under 'Send Summary To'."))
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': _("Nightly run finished"), 'message': msg, 'type': 'success', 'sticky': False}}

    # ---------- nightly run ----------
    @api.model
    def _cron_auto_reconcile(self):
        for cfg in self.sudo().search([('auto_run', '=', True)]):
            try:
                with self.env.cr.savepoint():
                    cfg._run_auto()
            except Exception:
                _logger.exception("Smart Reconcile nightly run failed for company %s", cfg.company_id.display_name)

    def _run_auto(self):
        self.ensure_one()
        company = self.company_id
        Wizard = self.env['account.payment.reconciliation.wizard'].with_company(company).with_context(
            allowed_company_ids=[company.id])
        wizard = Wizard.create({})
        wizard.action_find_matches()
        allowed = ['exact_ref'] + (['exact_amount'] if self.auto_include_amount else [])
        lines = wizard.match_line_ids.filtered(
            lambda l: l.confidence in allowed and l.to_reconcile and l.payment_id and l.invoice_id
            and not l.payment_ids and l.invoice_id.company_id == company)
        # never redo what a person has undone: skip pairs that already have an "undone" log entry
        undone = self.env['payment.recon.log'].search([
            ('state', '=', 'undone'), ('invoice_id', 'in', lines.invoice_id.ids)])
        blocked = {(lg.invoice_id.id, pay.id) for lg in undone for pay in lg.payment_ids}
        lines = lines.filtered(lambda l: (l.invoice_id.id, l.payment_id.id) not in blocked)
        done, failed = wizard._reconcile_lines(lines, source='auto')
        review = len(wizard.match_line_ids) - len(lines)
        summary = _("%(done)d reconciled automatically, %(failed)d failed, %(review)d left for your review.") % {
            'done': done, 'failed': len(failed), 'review': max(review, 0)}
        if failed:
            summary += "\n" + "\n".join(failed[:10])
        self.sudo().write({'last_run': fields.Datetime.now(), 'last_summary': summary})
        emails = [e for e in self.notify_user_ids.mapped('email') if e]
        if emails:
            try:
                self.env['mail.mail'].sudo().create({
                    'subject': _("Smart Reconcile summary - %s") % company.name,
                    'body_html': "<p>%s</p>" % summary.replace("\n", "<br/>"),
                    'email_to': ','.join(emails),
                    'auto_delete': True,
                }).send()
            except Exception:
                _logger.exception("Smart Reconcile summary mail could not be sent")
        return done
