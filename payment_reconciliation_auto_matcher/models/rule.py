# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

SETTLE_TYPES = ('out_invoice', 'out_receipt', 'in_invoice', 'in_receipt')
CUSTOMER_TYPES = ('out_invoice', 'out_receipt')


class PaymentReconRule(models.Model):
    _name = 'payment.recon.rule'
    _description = 'Smart Reconcile Adjustment Rule'
    _order = 'sequence, id'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company)
    rule_type = fields.Selection([
        ('wht', 'Withholding tax (customer paid net of WHT)'),
        ('bank_charge', 'Bank charge (payment short by the bank fee)'),
        ('tolerance', 'Small difference (write-off tolerance)'),
    ], string='Type', required=True, default='wht')
    applies_to = fields.Selection([
        ('customer', 'Customer invoices'),
        ('vendor', 'Vendor bills'),
        ('both', 'Both'),
    ], default='customer', required=True)
    amount_type = fields.Selection([('percent', 'Percentage'), ('fixed', 'Fixed amount')],
                                   default='percent', required=True)
    value = fields.Float(string='Rate / Amount', required=True,
                         help="Percentage (e.g. 5 for 5%) or fixed amount, depending on the type above.")
    max_amount = fields.Float(string='Maximum Amount',
                              help="Optional cap on the adjustment (0 = no cap).")
    wht_base = fields.Selection([('untaxed', 'Amount before taxes'), ('total', 'Total with taxes')],
                                string='WHT Calculated On', default='untaxed')
    account_id = fields.Many2one('account.account', string='Adjustment Account', required=True,
                                 help="Where the difference is booked (e.g. WHT receivable, bank charges, write-off).")
    journal_id = fields.Many2one('account.journal', string='Journal', required=True,
                                 domain="[('type', '=', 'general')]",
                                 help="Miscellaneous journal that receives the adjustment entry.")
    partner_ids = fields.Many2many('res.partner', string='Only These Partners',
                                   help="Leave empty to apply to every partner.")
    auto_tick = fields.Boolean(string='Tick by Default', default=False,
                               help="Tick the row by default when the invoice reference is also found in the payment memo. "
                                    "Leave off to review every adjustment before it is booked.")

    @api.constrains('value', 'amount_type', 'rule_type')
    def _check_value(self):
        for rule in self:
            if rule.value <= 0:
                raise ValidationError(_("The rate / amount must be greater than zero."))
            if rule.amount_type == 'percent' and rule.value > 100:
                raise ValidationError(_("A percentage cannot be above 100."))
            if rule.rule_type == 'wht' and rule.amount_type != 'percent':
                raise ValidationError(_("Withholding tax rules use a percentage."))

    def _label(self):
        self.ensure_one()
        if self.rule_type == 'wht':
            return _("WHT %s%%") % ('%g' % self.value)
        if self.rule_type == 'bank_charge':
            return _("bank charge")
        return _("write-off")

    def _adjustment_for(self, invoice, diff, ref_ok):
        """Amount this rule would book for 'invoice' if the payment is short by 'diff',
        or 0.0 when the rule does not explain the difference. Underpayments only."""
        self.ensure_one()
        cur = invoice.currency_id
        if invoice.move_type not in SETTLE_TYPES or cur != invoice.company_id.currency_id:
            return 0.0
        is_customer = invoice.move_type in CUSTOMER_TYPES
        if (self.applies_to == 'customer' and not is_customer) or (self.applies_to == 'vendor' and is_customer):
            return 0.0
        if self.partner_ids and (invoice.commercial_partner_id or invoice.partner_id) not in self.partner_ids:
            return 0.0
        if cur.compare_amounts(diff, 0.0) <= 0:
            return 0.0
        residual = abs(invoice.amount_residual)
        if self.rule_type == 'wht':
            if invoice.payment_state != 'not_paid':
                return 0.0
            base = invoice.amount_untaxed if self.wht_base == 'untaxed' else invoice.amount_total
            amount = cur.round(abs(base) * self.value / 100.0)
            if self.max_amount:
                amount = min(amount, self.max_amount)
            return amount if amount and cur.is_zero(diff - amount) else 0.0
        if self.rule_type == 'bank_charge':
            amount = self.value if self.amount_type == 'fixed' else residual * self.value / 100.0
            amount = cur.round(amount)
            if self.max_amount:
                amount = min(amount, self.max_amount)
            return amount if amount and cur.is_zero(diff - amount) else 0.0
        # tolerance: any small shortfall, only when the invoice reference was found in the memo
        if not ref_ok:
            return 0.0
        limit = self.value if self.amount_type == 'fixed' else residual * self.value / 100.0
        if self.max_amount:
            limit = min(limit, self.max_amount)
        return cur.round(diff) if cur.compare_amounts(diff, limit) <= 0 else 0.0
