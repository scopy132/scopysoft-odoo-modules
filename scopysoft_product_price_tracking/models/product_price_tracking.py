# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ProductPriceTracking(models.Model):
    _name = 'product.price.tracking'
    _description = 'Product Price Tracking History'
    _order = 'create_date desc, id desc'
    _rec_name = 'product_id'

    product_id = fields.Many2one(
        'product.product',
        string='Product Variant',
        required=True,
        ondelete='cascade',
        index=True
    )
    product_tmpl_id = fields.Many2one(
        'product.template',
        string='Product Template',
        required=True,
        ondelete='cascade',
        index=True
    )
    product_name = fields.Char(
        string='Product Name',
        related='product_id.name',
        store=True
    )
    default_code = fields.Char(
        string='Internal Reference',
        related='product_id.default_code',
        store=True
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
        compute='_compute_price_difference',
        store=True,
        digits='Product Price'
    )
    price_difference_percent = fields.Float(
        string='Difference (%)',
        compute='_compute_price_difference',
        store=True,
        digits=(16, 2)
    )

    user_id = fields.Many2one(
        'res.users',
        string='User',
        required=True,
        default=lambda self: self.env.user,
        index=True
    )
    origin = fields.Char(
        string='Origin',
        help='Source of the change (e.g., Manual, Import, Scheduled Action, Module name)'
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company,
        index=True
    )
    currency_id = fields.Many2one(
        'res.currency',
        string='Currency',
        related='company_id.currency_id',
        store=True
    )

    notes = fields.Text(string='Notes')

    @api.depends('old_price', 'new_price')
    def _compute_price_difference(self):
        for record in self:
            record.price_difference = record.new_price - record.old_price
            if record.old_price and record.old_price != 0:
                record.price_difference_percent = (
                    (record.new_price - record.old_price) / record.old_price
                ) * 100
            else:
                record.price_difference_percent = 0.0

    def _compute_display_name(self):
        for record in self:
            record.display_name = f'{record.product_name} - {record.create_date.strftime("%Y-%m-%d %H:%M:%S")}'
