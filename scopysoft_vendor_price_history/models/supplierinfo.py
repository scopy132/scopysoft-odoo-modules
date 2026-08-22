# -*- coding: utf-8 -*-
import logging
from odoo import api, fields, models
from odoo.tools import float_compare

_logger = logging.getLogger(__name__)


class ProductSupplierInfo(models.Model):
    _inherit = 'product.supplierinfo'

    vendor_price_history_ids = fields.One2many(
        'vendor.price.history',
        'product_tmpl_id',
        string='Price History',
        related=False
    )
    change_reason = fields.Selection(
        [
            ('vendor_increase', 'Vendor Increase'),
            ('vendor_decrease', 'Vendor Decrease'),
            ('negotiation', 'Negotiation'),
            ('correction', 'Correction'),
            ('promotion', 'Promotion'),
            ('contract', 'Contract Renewal'),
            ('other', 'Other'),
        ],
        string='Reason for Change',
        help='Optional. If set before saving a price edit, this reason is stored '
             'alongside the change in Vendor Price History. It is cleared after '
             'each save so it never carries over to the next edit.'
    )

    last_price_change_date = fields.Datetime(
        string='Last Changed',
        compute='_compute_price_comparison',
        help='Date of the most recent recorded price change for this vendor '
             'on this product. Falls back to the line\'s creation date if no '
             'change has been logged yet.'
    )
    is_cheapest_vendor = fields.Boolean(
        string='Cheapest',
        compute='_compute_price_comparison',
        store=True,
        help='True when this vendor currently has the lowest price for this '
             'product among all its vendor pricelist lines.'
    )
    price_diff_from_cheapest = fields.Float(
        string='Diff from Cheapest',
        compute='_compute_price_comparison',
        digits='Product Price',
    )
    price_diff_from_cheapest_percent = fields.Float(
        string='Diff from Cheapest (%)',
        compute='_compute_price_comparison',
        store=True,
        digits=(16, 2),
        help='How much more this vendor charges versus the cheapest vendor '
             'for this product, as a percentage of the cheapest price.'
    )

    @api.depends('price', 'product_tmpl_id.seller_ids.price', 'vendor_price_history_ids.create_date')
    def _compute_price_comparison(self):
        precision = self.env['decimal.precision'].precision_get('Product Price')
        by_product = {}
        for rec in self:
            by_product.setdefault(rec.product_tmpl_id.id, self.browse())
            by_product[rec.product_tmpl_id.id] |= rec

        for product_tmpl_id, records in by_product.items():
            # Compare against ALL vendor lines for this product, not just the
            # ones currently loaded in self, so "cheapest" is always accurate
            # even when viewing a filtered subset.
            all_lines = self.search([('product_tmpl_id', '=', product_tmpl_id)])
            priced_lines = all_lines.filtered(lambda l: l.price)
            cheapest = min(priced_lines.mapped('price')) if priced_lines else 0.0

            for rec in records:
                if cheapest and rec.price:
                    rec.is_cheapest_vendor = float_compare(
                        rec.price, cheapest, precision_digits=precision
                    ) == 0
                    rec.price_diff_from_cheapest = rec.price - cheapest
                    rec.price_diff_from_cheapest_percent = (
                        (rec.price - cheapest) / cheapest
                    ) * 100
                else:
                    rec.is_cheapest_vendor = False
                    rec.price_diff_from_cheapest = 0.0
                    rec.price_diff_from_cheapest_percent = 0.0

                last_hist = rec.vendor_price_history_ids.filtered(
                    lambda h, rec=rec: h.partner_id == rec.partner_id
                ).sorted('create_date', reverse=True)
                rec.last_price_change_date = last_hist[:1].create_date if last_hist else rec.create_date

    def _get_change_source(self):
        """Determine what triggered this write: PO confirmation, import, or manual."""
        if self.env.context.get('_vendor_price_source'):
            return self.env.context['_vendor_price_source']
        if self.env.context.get('import_file'):
            return 'import'
        return 'manual'

    def write(self, vals):
        """Track vendor price changes"""
        if 'price' in vals and not self.env.context.get('_vendor_price_tracking_done'):
            source = self._get_change_source()
            for rec in self:
                old_price = rec.price
                new_price = vals['price']

                precision = self.env['decimal.precision'].precision_get('Product Price')
                if float_compare(old_price, new_price, precision_digits=precision) != 0:
                    _logger.info(
                        'Vendor price change: Product=%s, Vendor=%s, Old=%s, New=%s',
                        rec.product_tmpl_id.name, rec.partner_id.name, old_price, new_price
                    )
                    reason = vals.get('change_reason', rec.change_reason)
                    self.env['vendor.price.history'].sudo().create({
                        'product_tmpl_id': rec.product_tmpl_id.id,
                        'partner_id': rec.partner_id.id,
                        'old_price': old_price,
                        'new_price': new_price,
                        'user_id': self.env.user.id,
                        'company_id': rec.company_id.id if rec.company_id else self.env.company.id,
                        'reason': reason or False,
                        'change_source': source,
                    })

        # Clear the reason after it's been captured so it doesn't silently
        # apply to a future, unrelated price edit.
        if 'change_reason' not in vals and vals.get('price') is not None:
            vals = dict(vals, change_reason=False)

        return super(ProductSupplierInfo, self.with_context(_vendor_price_tracking_done=True)).write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        """Track initial vendor price on creation"""
        records = super(ProductSupplierInfo, self).create(vals_list)

        if not self.env.context.get('_vendor_price_tracking_done'):
            source = self._get_change_source()
            for rec, vals in zip(records, vals_list):
                if vals.get('price') and vals['price'] > 0:
                    self.env['vendor.price.history'].sudo().create({
                        'product_tmpl_id': rec.product_tmpl_id.id,
                        'partner_id': rec.partner_id.id,
                        'old_price': 0.0,
                        'new_price': vals['price'],
                        'user_id': self.env.user.id,
                        'company_id': rec.company_id.id if rec.company_id else self.env.company.id,
                        'reason': vals.get('change_reason', False),
                        'change_source': source,
                    })

        return records


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    vendor_price_history_ids = fields.One2many(
        'vendor.price.history',
        'product_tmpl_id',
        string='Vendor Price History'
    )
    vendor_price_history_count = fields.Integer(
        string='Vendor Price Changes',
        compute='_compute_vendor_price_history_count'
    )
    last_purchase_price = fields.Float(
        string='Last Purchased Price',
        compute='_compute_last_purchase_price',
        digits='Product Price',
        help='Unit price from the most recently confirmed purchase order line for this product.'
    )
    last_purchase_vendor_id = fields.Many2one(
        'res.partner',
        string='Last Purchase Vendor',
        compute='_compute_last_purchase_price'
    )
    last_purchase_date = fields.Datetime(
        string='Last Purchase Date',
        compute='_compute_last_purchase_price'
    )

    def _compute_vendor_price_history_count(self):
        for template in self:
            template.vendor_price_history_count = len(template.vendor_price_history_ids)

    def _compute_last_purchase_price(self):
        PurchaseLine = self.env['purchase.order.line']
        for template in self:
            line = PurchaseLine.search([
                ('product_id.product_tmpl_id', '=', template.id),
                ('order_id.state', 'in', ('purchase', 'done')),
            ], order='date_order desc, id desc', limit=1)
            if line:
                template.last_purchase_price = line.price_unit
                template.last_purchase_vendor_id = line.order_id.partner_id
                template.last_purchase_date = line.order_id.date_order
            else:
                template.last_purchase_price = 0.0
                template.last_purchase_vendor_id = False
                template.last_purchase_date = False

    def action_view_vendor_price_history(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id(
            'scopysoft_vendor_price_history.action_vendor_price_history'
        )
        action['domain'] = [('product_tmpl_id', '=', self.id)]
        action['context'] = {
            'default_product_tmpl_id': self.id,
            'search_default_product_tmpl_id': self.id,
        }
        return action

    def action_view_vendor_price_comparison(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id(
            'scopysoft_vendor_price_history.action_vendor_price_comparison'
        )
        action['domain'] = [('product_tmpl_id', '=', self.id)]
        action['context'] = {}
        return action
