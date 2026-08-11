# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.tools import float_compare, float_round


class ProductCostTracking(models.Model):
    _name = 'product.cost.tracking'
    _description = 'Product Cost Tracking History'
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
    
    old_cost = fields.Float(
        string='Old Cost',
        digits='Product Price',
        required=True
    )
    new_cost = fields.Float(
        string='New Cost',
        digits='Product Price',
        required=True
    )
    cost_difference = fields.Float(
        string='Difference',
        compute='_compute_cost_difference',
        store=True,
        digits='Product Price'
    )
    cost_difference_percent = fields.Float(
        string='Difference (%)',
        compute='_compute_cost_difference',
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
    
    @api.depends('old_cost', 'new_cost')
    def _compute_cost_difference(self):
        for record in self:
            record.cost_difference = record.new_cost - record.old_cost
            if record.old_cost and record.old_cost != 0:
                record.cost_difference_percent = (
                    (record.new_cost - record.old_cost) / record.old_cost
                ) * 100
            else:
                record.cost_difference_percent = 0.0

    def name_get(self):
        result = []
        for record in self:
            name = f'{record.product_name} - {record.create_date.strftime("%Y-%m-%d %H:%M:%S")}'
            result.append((record.id, name))
        return result