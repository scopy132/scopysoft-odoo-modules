# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    low_stock_threshold = fields.Float(
        string='Low Stock Threshold',
        default=0.0,
        help='When stock on hand falls at or below this quantity, a warning is shown on the product form. Set to 0 to disable the alert for this product.',
    )
    is_low_stock = fields.Boolean(
        string='Low Stock',
        compute='_compute_is_low_stock',
        search='_search_is_low_stock',
        store=False,
    )
    stock_alert_message = fields.Char(
        string='Stock Alert',
        compute='_compute_is_low_stock',
        store=False,
    )

    @api.depends('qty_available', 'low_stock_threshold', 'type')
    def _compute_is_low_stock(self):
        for product in self:
            if (
                product.type in ('product', 'consu')
                and product.low_stock_threshold > 0
                and product.qty_available <= product.low_stock_threshold
            ):
                product.is_low_stock = True
                product.stock_alert_message = (
                    '⚠ Low Stock: %.2f %s on hand — threshold is %.2f %s'
                ) % (
                    product.qty_available,
                    product.uom_id.name,
                    product.low_stock_threshold,
                    product.uom_id.name,
                )
            else:
                product.is_low_stock = False
                product.stock_alert_message = ''

    def _search_is_low_stock(self, operator, value):
        """Allow is_low_stock to be used in domains (e.g. the Low Stock
        Products list) even though it's a non-stored computed field.
        qty_available itself is non-stored, so we can't push this down
        to SQL — we resolve it in Python against the monitored products.
        """
        want_low_stock = (operator == '=' and value) or (operator == '!=' and not value)
        candidate_ids = self.search([
            ('type', 'in', ['product', 'consu']),
            ('low_stock_threshold', '>', 0),
        ]).ids
        matching_ids = [
            p.id for p in self.browse(candidate_ids) if p.is_low_stock == want_low_stock
        ]
        return [('id', 'in', matching_ids)]
