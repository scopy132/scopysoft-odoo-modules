# -*- coding: utf-8 -*-
from odoo import api, fields, models


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    low_stock_warning = fields.Char(
        string='Low Stock Warning',
        compute='_compute_low_stock_warning',
        store=False,
    )

    @api.depends('product_id', 'product_uom_qty')
    def _compute_low_stock_warning(self):
        for line in self:
            warning = ''
            product = line.product_id
            if (
                product
                and product.type in ('product', 'consu')
                and product.low_stock_threshold > 0
            ):
                remaining = product.qty_available - line.product_uom_qty
                if product.qty_available <= product.low_stock_threshold:
                    warning = (
                        '⚠ %s is already low on stock (%.2f on hand, threshold %.2f).'
                    ) % (product.name, product.qty_available, product.low_stock_threshold)
                elif remaining <= product.low_stock_threshold:
                    warning = (
                        '⚠ Selling this quantity will bring %s below its low stock '
                        'threshold (%.2f remaining after this order, threshold %.2f).'
                    ) % (product.name, remaining, product.low_stock_threshold)
            line.low_stock_warning = warning

    @api.onchange('product_id', 'product_uom_qty')
    def _onchange_low_stock_alert(self):
        """Show a dismissible on-screen warning when confirming/editing the
        line, in addition to the always-visible inline message field above.
        This does not block the sale — it's advisory only, since the person
        selling may have valid reasons to proceed (e.g. incoming PO)."""
        self._compute_low_stock_warning()
        if self.low_stock_warning:
            return {'warning': {
                'title': 'Low Stock Warning',
                'message': self.low_stock_warning,
            }}
