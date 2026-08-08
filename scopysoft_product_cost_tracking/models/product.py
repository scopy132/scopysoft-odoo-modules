# -*- coding: utf-8 -*-
import inspect
import logging

from odoo import api, fields, models, _
from odoo.tools import float_compare

_logger = logging.getLogger(__name__)


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    cost_tracking_ids = fields.One2many(
        'product.cost.tracking',
        'product_tmpl_id',
        string='Cost History'
    )
    cost_tracking_count = fields.Integer(
        string='Cost Changes',
        compute='_compute_cost_tracking_count'
    )

    def _compute_cost_tracking_count(self):
        for template in self:
            template.cost_tracking_count = len(template.cost_tracking_ids)

    def action_view_cost_tracking(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id(
            'scopysoft_product_cost_tracking.action_product_cost_tracking'
        )
        action['domain'] = [('product_tmpl_id', '=', self.id)]
        action['context'] = {
            'default_product_tmpl_id': self.id,
            'search_default_product_tmpl_id': self.id,
        }
        return action


class ProductProduct(models.Model):
    _inherit = 'product.product'

    cost_tracking_ids = fields.One2many(
        'product.cost.tracking',
        'product_id',
        string='Cost History'
    )
    cost_tracking_count = fields.Integer(
        string='Cost Changes',
        compute='_compute_cost_tracking_count'
    )

    def _compute_cost_tracking_count(self):
        for product in self:
            product.cost_tracking_count = len(product.cost_tracking_ids)

    def write(self, vals):
        """Override write to track cost changes"""
        # Track cost changes for standard_price field
        if 'standard_price' in vals:
            # Check if we should skip tracking (to avoid duplicates in same transaction)
            if not self.env.context.get('_cost_tracking_done'):
                origin = self._detect_cost_change_origin()

                for product in self:
                    old_cost = product.standard_price
                    new_cost = vals['standard_price']

                    # Only track if there's an actual change
                    precision = self.env['decimal.precision'].precision_get('Product Price')
                    if float_compare(old_cost, new_cost, precision_digits=precision) != 0:
                        _logger.info(
                            'Cost change detected: Product=%s, Old=%s, New=%s, Origin=%s, Context=%s',
                            product.display_name, old_cost, new_cost, origin,
                            {k: v for k, v in self.env.context.items() if not k.startswith('_')}
                        )

                        # Create tracking record
                        self.env['product.cost.tracking'].sudo().create({
                            'product_id': product.id,
                            'product_tmpl_id': product.product_tmpl_id.id,
                            'old_cost': old_cost,
                            'new_cost': new_cost,
                            'user_id': self.env.user.id,
                            'origin': origin,
                            'company_id': product.company_id.id if product.company_id else self.env.company.id,
                        })

        # Mark that we've tracked this to avoid duplicates
        result = super(ProductProduct, self.with_context(_cost_tracking_done=True)).write(vals)
        return result

    def _detect_cost_change_origin(self):
        """Detect the origin of cost change with comprehensive checks"""
        context = self.env.context

        # Priority 1: Explicit origin in context
        if context.get('cost_tracking_origin'):
            return context['cost_tracking_origin']

        # Priority 2: Import detection
        if context.get('import_file') or context.get('import_current_module'):
            return 'Import'

        # Priority 3: Scheduled action detection - multiple indicators
        if any([
            context.get('from_cron'),
            context.get('cron_name'),
            context.get('ir_cron_id'),
            context.get('scheduled_action_id'),
        ]):
            return 'Scheduled Action'

        # Priority 4: Check for specific Odoo actions
        active_model = context.get('active_model', '')
        if 'ir.cron' in active_model or 'ir.actions.server' in active_model:
            return 'Scheduled Action'

        # Priority 5: Mass update
        if len(self) > 1:
            return 'Mass Update'

        # Priority 6: Detailed stack inspection
        origin = self._inspect_call_stack()
        if origin:
            return origin

        # Priority 7: Check if there's a web request
        try:
            from odoo import http
            if http.request and hasattr(http.request, 'httprequest'):
                # It's a web request - likely manual or button click
                path = http.request.httprequest.path
                if '/web/dataset/call_button' in path:
                    return 'Button Action'
                return 'Manual'
        except (AttributeError, RuntimeError):
            # No web context - automated
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

                # Log for debugging
                _logger.debug(
                    'Stack frame %d: file=%s, function=%s',
                    depth, filename.split('/')[-1], func_name
                )

                # Check for BoM cost computation
                if func_name in ('button_bom_cost', 'compute_bom_cost', '_compute_bom_price'):
                    return 'Compute from BoM'

                # Check for scheduled action/cron
                if 'ir_cron.py' in filename and func_name in ('_callback', 'method_direct_trigger'):
                    return 'Scheduled Action'

                if 'ir_actions_server.py' in filename and func_name == 'run':
                    return 'Scheduled Action'

                # Check for specific modules
                if depth < 10:  # Only check close stack frames for modules
                    if '/purchase/' in filename and 'purchase.py' in filename:
                        return 'Purchase Module'
                    elif '/sale/' in filename and 'sale.py' in filename:
                        return 'Sales Module'
                    elif '/mrp/' in filename and 'production' in filename:
                        return 'Manufacturing Module'
                    elif '/stock/' in filename and ('picking' in filename or 'move' in filename):
                        return 'Inventory Module'

                frame = frame.f_back
                depth += 1

        except Exception as e:
            _logger.warning('Error inspecting call stack: %s', str(e))

        return None

    @api.model_create_multi
    def create(self, vals_list):
        """Override create to track initial cost"""
        products = super(ProductProduct, self).create(vals_list)

        # Track initial cost only if not already tracked
        if not self.env.context.get('_cost_tracking_done'):
            origin = self._detect_cost_change_origin() or 'Initial Cost'

            for product, vals in zip(products, vals_list):
                if 'standard_price' in vals and vals['standard_price']:
                    self.env['product.cost.tracking'].sudo().create({
                        'product_id': product.id,
                        'product_tmpl_id': product.product_tmpl_id.id,
                        'old_cost': 0.0,
                        'new_cost': vals['standard_price'],
                        'user_id': self.env.user.id,
                        'origin': origin,
                        'company_id': product.company_id.id if product.company_id else self.env.company.id,
                    })

        return products

    def action_view_cost_tracking(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id(
            'scopysoft_product_cost_tracking.action_product_cost_tracking'
        )
        action['domain'] = [('product_id', '=', self.id)]
        action['context'] = {
            'default_product_id': self.id,
            'default_product_tmpl_id': self.product_tmpl_id.id,
            'search_default_product_id': self.id,
        }
        return action

    # Helper methods for explicit origin setting
    def update_cost_with_origin(self, new_cost, origin):
        """
        Update product cost with explicit origin tracking

        :param new_cost: float - new cost value
        :param origin: str - origin description (e.g., 'Purchase Order', 'Inventory Adjustment')
        """
        return self.with_context(cost_tracking_origin=origin).write({
            'standard_price': new_cost
        })

    def update_cost_from_purchase(self, new_cost):
        """Helper method for purchase module"""
        return self.update_cost_with_origin(new_cost, 'Purchase Order')

    def update_cost_from_inventory(self, new_cost):
        """Helper method for inventory adjustments"""
        return self.update_cost_with_origin(new_cost, 'Inventory Valuation')

    def update_cost_from_manufacturing(self, new_cost):
        """Helper method for manufacturing"""
        return self.update_cost_with_origin(new_cost, 'Manufacturing Order')
