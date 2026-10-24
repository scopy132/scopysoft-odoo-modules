# -*- coding: utf-8 -*-
import json

from odoo import models, fields, api, _
from odoo.exceptions import UserError

CONFIDENCE_SELECTION = [
    ('exact_ref', 'Exact (amount + reference)'),
    ('exact_amount', 'Amount only'),
    ('reference_only', 'Reference only'),
    ('split_exact', 'Split payments'),
    ('split_partial', 'Split - partial'),
    ('split_over', 'Split - over'),
    ('combo_amount', 'Combination (low confidence)'),
    ('combo_ambiguous', 'Combination (ambiguous)'),
    ('adjusted', 'With adjustment'),
    ('multiple_matches', 'Multiple matches'),
    ('no_close_match', 'No close match'),
    ('no_open_invoice', 'No candidate'),
    ('claimed_elsewhere', 'Claimed elsewhere'),
    ('manual', 'Manual'),
]


class PaymentReconLog(models.Model):
    _name = 'payment.recon.log'
    _description = 'Smart Reconcile Audit Log'
    _order = 'id desc'
    _rec_name = 'invoice_id'

    date = fields.Datetime(default=fields.Datetime.now, readonly=True)
    user_id = fields.Many2one('res.users', string='Done By', default=lambda s: s.env.user, readonly=True)
    company_id = fields.Many2one('res.company', required=True, readonly=True, default=lambda s: s.env.company)
    currency_id = fields.Many2one('res.currency', related='company_id.currency_id', readonly=True)
    partner_id = fields.Many2one('res.partner', readonly=True)
    invoice_id = fields.Many2one('account.move', string='Invoice/Bill', readonly=True)
    payment_ids = fields.Many2many('account.payment', 'payment_recon_log_payment_rel', 'log_id', 'payment_id',
                                   string='Payments', readonly=True)
    payment_names = fields.Char(string='Payment Numbers', compute='_compute_payment_names', store=True)
    amount = fields.Monetary(string='Reconciled', readonly=True,
                             help="Reconciled amount in company currency (adjustment excluded).")
    adjustment_amount = fields.Monetary(string='Adjustment', readonly=True)
    rule_id = fields.Many2one('payment.recon.rule', string='Adjustment Rule', readonly=True, ondelete='set null')
    adjustment_move_id = fields.Many2one('account.move', string='Adjustment Entry', readonly=True)
    confidence = fields.Selection(CONFIDENCE_SELECTION, string='Match Type', readonly=True)
    source = fields.Selection([('manual', 'Manual (wizard)'), ('auto', 'Nightly run')], readonly=True, default='manual')
    explanation = fields.Char(string='Why It Matched', readonly=True)
    breakdown = fields.Char(readonly=True)
    partial_ids_json = fields.Text(readonly=True)
    state = fields.Selection([('done', 'Reconciled'), ('undone', 'Undone')], default='done', readonly=True)
    undone_date = fields.Datetime(readonly=True)
    undone_by = fields.Many2one('res.users', readonly=True)

    @api.depends('payment_ids.name')
    def _compute_payment_names(self):
        for log in self:
            log.payment_names = ", ".join(n for n in log.payment_ids.mapped('name') if n)

    def action_undo(self):
        """Undo ONLY what this run reconciled: delete the partial reconciliations it created
        (Odoo restores the invoice, the payments and any exchange difference itself), and reverse
        the adjustment entry if there was one. Reconciliations the user made by hand are untouched."""
        Partial = self.env['account.partial.reconcile']
        for log in self:
            if log.state != 'done':
                raise UserError(_("This reconciliation was already undone."))
            ids = json.loads(log.partial_ids_json or '[]')
            partials = Partial.browse(ids).exists()
            adj = log.adjustment_move_id
            partials.unlink()
            if adj and adj.state == 'posted':
                still = adj.line_ids.filtered(lambda l: l.account_id.reconcile and l.reconciled)
                if still:
                    raise UserError(_("The adjustment entry %s is reconciled with something else and cannot be reversed "
                                      "automatically. Unreconcile it manually first.") % adj.name)
                adj._reverse_moves([{
                    'ref': _("Reversal of: %s", adj.name),
                    'date': fields.Date.context_today(log),
                }], cancel=True)
            log.write({'state': 'undone', 'undone_date': fields.Datetime.now(), 'undone_by': self.env.user.id})
        return True

    def unlink(self):
        if not self.env.context.get('recon_log_force'):
            raise UserError(_("Audit log entries cannot be deleted. Use Undo to reverse a reconciliation."))
        return super().unlink()
