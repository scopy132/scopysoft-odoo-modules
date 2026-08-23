# -*- coding: utf-8 -*-
from odoo import api, fields, models


class VendorPriceHistory(models.Model):
    _name = 'vendor.price.history'
    _description = 'Vendor Price History'
    _order = 'create_date desc, id desc'
    _rec_name = 'product_tmpl_id'

    product_tmpl_id = fields.Many2one(
        'product.template',
        string='Product',
        required=True,
        ondelete='cascade',
        index=True
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Vendor',
        required=True,
        ondelete='cascade',
        index=True
    )
    old_price = fields.Float(
        string='Old Price',
        digits='Product Price',
        required=True
    )
    new_price = fields.Float(
        string='New Price',
        digits='Product Price',
        required=True
    )
    price_difference = fields.Float(
        string='Difference',
        compute='_compute_difference',
        store=True,
        digits='Product Price'
    )
    price_difference_percent = fields.Float(
        string='Difference (%)',
        compute='_compute_difference',
        store=True,
        digits=(16, 2)
    )
    currency_id = fields.Many2one(
        'res.currency',
        string='Currency',
        related='company_id.currency_id',
        store=True
    )
    user_id = fields.Many2one(
        'res.users',
        string='User',
        required=True,
        default=lambda self: self.env.user,
        index=True
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company,
        index=True
    )
    notes = fields.Text(string='Notes')
    activity_id = fields.Many2one(
        'mail.activity',
        string='Linked Alert Activity',
        copy=False,
        ondelete='set null',
        help='The scheduled alert activity created for this price change, '
             'if it crossed the significant-change threshold. Automatically '
             'cleared once that activity is completed or removed, so it '
             'never points at a stale record.'
    )
    reason = fields.Selection(
        [
            ('vendor_increase', 'Vendor Increase'),
            ('vendor_decrease', 'Vendor Decrease'),
            ('negotiation', 'Negotiation'),
            ('correction', 'Correction'),
            ('promotion', 'Promotion'),
            ('contract', 'Contract Renewal'),
            ('other', 'Other'),
        ],
        string='Reason',
        help='Optional reason for the price change, set by the user on the vendor pricelist line.'
    )
    change_source = fields.Selection(
        [
            ('manual', 'Manual Edit'),
            ('purchase_order', 'Purchase Order'),
            ('import', 'Import'),
            ('other', 'Other/API'),
        ],
        string='Source',
        default='manual',
        required=True,
        index=True,
        help='How this price change was triggered.'
    )
    is_significant = fields.Boolean(
        string='Significant Change',
        compute='_compute_difference',
        store=True,
        help='Flagged when the absolute percentage change exceeds the configured alert threshold.'
    )

    @api.depends('old_price', 'new_price')
    def _compute_difference(self):
        threshold = float(
            self.env['ir.config_parameter'].sudo().get_param(
                'scopysoft_vendor_price_history.alert_threshold', default=15.0
            )
        )
        for rec in self:
            rec.price_difference = rec.new_price - rec.old_price
            if rec.old_price and rec.old_price != 0:
                rec.price_difference_percent = (
                    (rec.new_price - rec.old_price) / rec.old_price
                ) * 100
            else:
                rec.price_difference_percent = 0.0
            rec.is_significant = abs(rec.price_difference_percent) >= threshold

    def name_get(self):
        result = []
        for rec in self:
            name = f'{rec.product_tmpl_id.name} - {rec.partner_id.name}'
            result.append((rec.id, name))
        return result

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._notify_if_significant()
        return records

    def _notify_if_significant(self):
        """Post an activity to whoever made the price change, when it
        crosses the configured alert threshold. Any previous open alert
        this feature created on the same product is looked up via the
        explicit activity_id link (not text matching) and cleared first,
        so alerts don't stack into a growing pile of stale to-dos."""
        for rec in self:
            if not rec.is_significant:
                continue
            direction = 'increased' if rec.price_difference > 0 else 'decreased'
            summary = (
                f'Vendor price {direction} by {abs(rec.price_difference_percent):.1f}% '
                f'for {rec.product_tmpl_id.name} ({rec.partner_id.name})'
            )
            note_user_id = rec.env.user.id
            try:
                prior = self.search([
                    ('product_tmpl_id', '=', rec.product_tmpl_id.id),
                    ('activity_id', '!=', False),
                    ('id', '!=', rec.id),
                ])
                stale_activities = prior.mapped('activity_id')
                if stale_activities:
                    stale_activities.unlink()
                activity = rec.product_tmpl_id.activity_schedule(
                    'mail.mail_activity_data_todo',
                    summary=summary,
                    note=(
                        f'Old price: {rec.old_price} → New price: {rec.new_price} '
                        f'({rec.price_difference_percent:.1f}%). Source: {rec.change_source}.'
                    ),
                    user_id=note_user_id,
                )
                rec.activity_id = activity.id
            except Exception:
                # Never block price tracking because activity scheduling failed
                # (e.g. mail module quirks or missing responsible field).
                continue
