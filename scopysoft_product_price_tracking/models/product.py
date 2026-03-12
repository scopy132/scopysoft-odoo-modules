# -*- coding: utf-8 -*-
import inspect
import logging

from odoo import api, fields, models
from odoo.tools import float_compare

_logger = logging.getLogger(__name__)


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    price_tracking_ids = fields.One2many(
        'product.price.tracking',
        'product_tmpl_id',
        string='Price History'
    )
    price_tracking_count = fields.Integer(
        string='Price Changes',
        compute='_compute_price_tracking_count'
    )

    def _compute_price_tracking_count(self):
        for template in self:
            template.price_tracking_count = len(template.price_tracking_ids)

    def write(self, vals):
        """Override write to track sales price changes on product template"""
        if 'list_price' in vals:
            if not self.env.context.get('_price_tracking_done'):
                origin = self._detect_price_change_origin()

                for template in self:
                    old_price = template.list_price
                    new_price = vals['list_price']

                    precision = self.env['decimal.precision'].precision_get('Product Price')
                    if float_compare(old_price, new_price, precision_digits=precision) != 0:
                        _logger.info(
                            'Price change detected: Product=%s, Old=%s, New=%s, Origin=%s',
                            template.display_name, old_price, new_price, origin
                        )

                        # Get the first variant to link to
                        variant = template.product_variant_ids[:1]
                        if variant:
                            self.env['product.price.tracking'].sudo().create({
                                'product_id': variant.id,
                                'product_tmpl_id': template.id,
                                'old_price': old_price,
                                'new_price': new_price,
                                'user_id': self.env.user.id,
                                'origin': origin,
                                'company_id': template.company_id.id if template.company_id else self.env.company.id,
                            })

        result = super(ProductTemplate, self.with_context(_price_tracking_done=True)).write(vals)
        return result

    def _detect_price_change_origin(self):
        """Detect the origin of price change"""
        context = self.env.context

        if context.get('price_tracking_origin'):
            return context['price_tracking_origin']

        if context.get('import_file') or context.get('import_current_module'):
            return 'Import'

        if any([
            context.get('from_cron'),
            context.get('cron_name'),
            context.get('ir_cron_id'),
            context.get('scheduled_action_id'),
        ]):
            return 'Scheduled Action'

        active_model = context.get('active_model', '')
        if 'ir.cron' in active_model or 'ir.actions.server' in active_model:
            return 'Scheduled Action'

        if len(self) > 1:
            return 'Mass Update'

        origin = self._inspect_call_stack()
        if origin:
            return origin

        try:
            from odoo import http
            if http.request and hasattr(http.request, 'httprequest'):
                path = http.request.httprequest.path
                if '/web/dataset/call_button' in path:
                    return 'Button Action'
                return 'Manual'
        except (AttributeError, RuntimeError):
            return 'Automated Process'

        return 'Manual'

    def _inspect_call_stack(self):
        """Inspect the call stack to identify the origin"""
        try:
            frame = inspect.currentframe()
            depth = 0
            max_depth = 20

            while frame and depth < max_depth:
                frame_info = inspect.getframeinfo(frame)
                filename = frame_info.filename
                func_name = frame.f_code.co_name

                if 'ir_cron.py' in filename and func_name in ('_callback', 'method_direct_trigger'):
                    return 'Scheduled Action'

                if 'ir_actions_server.py' in filename and func_name == 'run':
                    return 'Scheduled Action'

                if depth < 10:
                    if '/purchase/' in filename and 'purchase.py' in filename:
                        return 'Purchase Module'
                    elif '/sale/' in filename and 'sale.py' in filename:
                        return 'Sales Module'
                    elif '/mrp/' in filename and 'production' in filename:
                        return 'Manufacturing Module'

                frame = frame.f_back
                depth += 1

        except Exception as e:
            _logger.warning('Error inspecting call stack: %s', str(e))

        return None

    def action_view_price_tracking(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id(
            'scopysoft_product_price_tracking.action_product_price_tracking'
        )
        action['domain'] = [('product_tmpl_id', '=', self.id)]
        action['context'] = {
            'default_product_tmpl_id': self.id,
            'search_default_product_tmpl_id': self.id,
        }
        return action

    def update_price_with_origin(self, new_price, origin):
        """Update product price with explicit origin tracking"""
        return self.with_context(price_tracking_origin=origin).write({
            'list_price': new_price
        })
