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

    @api.depends('old_price', 'new_price')
    def _compute_difference(self):
        for rec in self:
            rec.price_difference = rec.new_price - rec.old_price
            if rec.old_price and rec.old_price != 0:
                rec.price_difference_percent = (
                    (rec.new_price - rec.old_price) / rec.old_price
                ) * 100
            else:
                rec.price_difference_percent = 0.0

    def name_get(self):
        result = []
        for rec in self:
            name = f'{rec.product_tmpl_id.name} - {rec.partner_id.name}'
            result.append((rec.id, name))
        return result
