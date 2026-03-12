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

    def write(self, vals):
        """Track vendor price changes"""
        if 'price' in vals and not self.env.context.get('_vendor_price_tracking_done'):
            for rec in self:
                old_price = rec.price
                new_price = vals['price']

                precision = self.env['decimal.precision'].precision_get('Product Price')
                if float_compare(old_price, new_price, precision_digits=precision) != 0:
                    _logger.info(
                        'Vendor price change: Product=%s, Vendor=%s, Old=%s, New=%s',
                        rec.product_tmpl_id.name, rec.partner_id.name, old_price, new_price
                    )
                    self.env['vendor.price.history'].sudo().create({
                        'product_tmpl_id': rec.product_tmpl_id.id,
                        'partner_id': rec.partner_id.id,
                        'old_price': old_price,
                        'new_price': new_price,
                        'user_id': self.env.user.id,
                        'company_id': rec.company_id.id if rec.company_id else self.env.company.id,
                    })

        return super(ProductSupplierInfo, self.with_context(_vendor_price_tracking_done=True)).write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        """Track initial vendor price on creation"""
        records = super(ProductSupplierInfo, self).create(vals_list)

        if not self.env.context.get('_vendor_price_tracking_done'):
            for rec, vals in zip(records, vals_list):
                if vals.get('price') and vals['price'] > 0:
                    self.env['vendor.price.history'].sudo().create({
                        'product_tmpl_id': rec.product_tmpl_id.id,
                        'partner_id': rec.partner_id.id,
                        'old_price': 0.0,
                        'new_price': vals['price'],
                        'user_id': self.env.user.id,
                        'company_id': rec.company_id.id if rec.company_id else self.env.company.id,
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

    def _compute_vendor_price_history_count(self):
        for template in self:
            template.vendor_price_history_count = len(template.vendor_price_history_ids)

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
