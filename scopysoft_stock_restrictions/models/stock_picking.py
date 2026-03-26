# -*- coding: utf-8 -*-
import logging
from datetime import date

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    # ── HELPERS ──────────────────────────────────────────────────────────────

    def _sr_get(self, key, default=False):
        """Read a restriction config parameter."""
        val = self.env['ir.config_parameter'].sudo().get_param(
            'scopysoft_stock_restrictions.%s' % key, default
        )
        if isinstance(default, bool):
            return str(val).lower() in ('true', '1', 'yes')
        if isinstance(default, (int, float)):
            try:
                return float(val)
            except (TypeError, ValueError):
                return default
        return val

    def _sr_is_admin(self):
        """Administrators bypass ALL restrictions."""
        return self.env.su or self.env.user.has_group('base.group_system')

    def _sr_is_manager(self):
        """Inventory Managers and Administrators can perform manager-only actions."""
        return (
            self._sr_is_admin()
            or self.env.user.has_group('stock.group_stock_manager')
        )

    def _sr_log(self, restriction, details='', picking=None, bypassed=False):
        """Write audit log using a new cursor that commits before the UserError rollback."""
        if not self._sr_get('enable_audit_log', False):
            return
        user_id = self.env.user.id
        # Only pass picking_id if picking is a real saved record
        picking_id = False
        if picking and picking.id:
            picking_id = picking.id
        elif picking is None and self and self[:1].id:
            # self exists and is saved — safe to reference
            picking_id = self[:1].id
        company_id = self.env.company.id
        try:
            new_cr = self.env.registry.cursor()
            try:
                new_env = api.Environment(new_cr, user_id, {})
                new_env['stock.restriction.log'].sudo().create({
                    'user_id': user_id,
                    'picking_id': picking_id,
                    'restriction': restriction,
                    'details': details,
                    'bypassed_by_admin': bypassed,
                    'company_id': company_id,
                })
                new_cr.commit()
                _logger.info('Stock Restrictions: logged "%s" for user %s', restriction, user_id)
            finally:
                new_cr.close()
        except Exception as e:
            _logger.error('Stock Restrictions: audit log failed: %s', str(e))

    # ── CREATE — block manual receipts/deliveries ─────────────────────────────

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if 'picking_type_id' not in vals:
                continue

            picking_type = self.env['stock.picking.type'].browse(vals['picking_type_id'])
            is_admin = self._sr_is_admin()
            has_source = (
                vals.get('origin')
                or vals.get('purchase_id')
                or vals.get('sale_id')
                or vals.get('backorder_id')
                or vals.get('group_id')
            )

            # Block manual receipt without PO
            if (
                picking_type.code == 'incoming'
                and self._sr_get('block_manual_receipt', False)
                and not has_source
            ):
                restriction = _('Block Manual Receipt')
                details = _('Attempted to create a receipt without a source Purchase Order.')
                if is_admin:
                    self._sr_log(restriction, details, picking=None, bypassed=True)
                else:
                    self._sr_log(restriction, details, picking=None)
                    raise UserError(_(
                        'Manual receipts are not allowed.\n\n'
                        'Receipts must be created from a Purchase Order.\n'
                        'Go to: Purchase → Orders → Purchase Orders → select order → Receive Products.'
                    ))

            # Block manual delivery without SO
            if (
                picking_type.code == 'outgoing'
                and self._sr_get('block_manual_delivery', False)
                and not has_source
            ):
                restriction = _('Block Manual Delivery')
                details = _('Attempted to create a delivery without a source Sales Order.')
                if is_admin:
                    self._sr_log(restriction, details, picking=None, bypassed=True)
                else:
                    self._sr_log(restriction, details, picking=None)
                    raise UserError(_(
                        'Manual deliveries are not allowed.\n\n'
                        'Deliveries must be created from a Sales Order.\n'
                        'Go to: Sales → Orders → Sales Orders → select order → Deliver.'
                    ))

        return super().create(vals_list)

    # ── WRITE — block backdating ──────────────────────────────────────────────

    def write(self, vals):
        if 'scheduled_date' in vals and self._sr_get('block_backdate', False):
            if not self._sr_is_admin():
                try:
                    new_date = fields.Datetime.from_string(vals['scheduled_date'])
                    if new_date and new_date.date() < date.today():
                        restriction = _('Block Backdating')
                        details = _('Attempted to set scheduled date to %s.') % vals['scheduled_date']
                        self._sr_log(restriction, details)
                        raise UserError(_(
                            'Backdating stock operations is not allowed.\n\n'
                            'You cannot set a scheduled date in the past.\n'
                            'Please use today\'s date or a future date.'
                        ))
                except (ValueError, TypeError):
                    pass
        return super().write(vals)

    # ── UNLINK — block non-managers deleting drafts ───────────────────────────

    def unlink(self):
        if self._sr_get('managers_only_delete', False) and not self._sr_is_manager():
            for picking in self:
                if picking.state == 'draft':
                    restriction = _('Manager Only: Delete Draft')
                    details = _('User "%s" tried to delete draft operation "%s".') % (
                        self.env.user.name, picking.name
                    )
                    self._sr_log(restriction, details, picking=picking)
                    raise UserError(_(
                        'Only Inventory Managers can delete draft stock operations.\n\n'
                        'Operation: %s\n\n'
                        'Please contact your Inventory Manager.'
                    ) % picking.name)
        return super().unlink()

    # ── ACTION CANCEL — block non-managers cancelling done pickings ───────────

    def action_cancel(self):
        if self._sr_get('managers_only_cancel', False) and not self._sr_is_manager():
            for picking in self:
                if picking.state == 'done':
                    restriction = _('Manager Only: Cancel Validated Operation')
                    details = _('User "%s" tried to cancel validated operation "%s".') % (
                        self.env.user.name, picking.name
                    )
                    self._sr_log(restriction, details, picking=picking)
                    raise UserError(_(
                        'Only Inventory Managers can cancel validated stock operations.\n\n'
                        'Operation: %s\n\n'
                        'Please contact your Inventory Manager.'
                    ) % picking.name)
        return super().action_cancel()

    # ── BUTTON VALIDATE — block non-managers validating ───────────────────────

    def button_validate(self):
        if self._sr_get('managers_only_validate', False) and not self._sr_is_manager():
            restriction = _('Manager Only: Validate Operation')
            details = _('User "%s" tried to validate operation "%s".') % (
                self.env.user.name, self[:1].name
            )
            self._sr_log(restriction, details)
            raise UserError(_(
                'Only Inventory Managers can validate stock operations.\n\n'
                'Please contact your Inventory Manager to validate this operation.'
            ))
        return super().button_validate()

    # ── _ACTION_DONE — quantity + stock checks ────────────────────────────────

    def _action_done(self):
        tolerance = self._sr_get('validation_tolerance', 0.0)
        is_admin = self._sr_is_admin()

        for picking in self:
            for move in picking.move_ids.filtered(lambda m: m.state not in ('done', 'cancel')):

                # Get done quantity (Odoo 16 compatible)
                if move.move_line_ids:
                    total_done = sum(ml.qty_done for ml in move.move_line_ids)
                else:
                    total_done = move.quantity_done

                demanded = move.product_uom_qty
                product_name = move.product_id.name

                # ── Block zero quantity ──────────────────────────────────────
                if self._sr_get('block_zero_qty', False) and not is_admin:
                    if total_done == 0:
                        restriction = _('Block Zero Quantity')
                        details = _('Product "%s": done qty is 0.') % product_name
                        self._sr_log(restriction, details, picking=picking)
                        raise UserError(_(
                            'Cannot validate with zero quantity.\n\n'
                            'Product: %s\n'
                            'Done: 0\n\n'
                            'Please enter the done quantity before validating.'
                        ) % product_name)

                # ── Block over-validation ────────────────────────────────────
                if self._sr_get('block_over_validation', False) and not is_admin:
                    max_allowed = demanded * (1 + tolerance / 100) if tolerance else demanded
                    if total_done > max_allowed:
                        restriction = _('Block Over-Validation')
                        details = _('Product "%s": demanded %s, done %s (max allowed: %s).') % (
                            product_name, demanded, total_done, max_allowed
                        )
                        self._sr_log(restriction, details, picking=picking)
                        raise UserError(_(
                            'Over-validation is not allowed.\n\n'
                            'Product: %s\n'
                            'Demanded: %s\n'
                            'Done: %s\n'
                            'Maximum allowed: %s%s\n\n'
                            'Please correct the done quantity.'
                        ) % (
                            product_name, demanded, total_done, max_allowed,
                            _(' (with %s%% tolerance)') % tolerance if tolerance else ''
                        ))

                # ── Block under-validation ───────────────────────────────────
                if self._sr_get('block_under_validation', False) and not is_admin:
                    min_allowed = demanded * (1 - tolerance / 100) if tolerance else demanded
                    if 0 < total_done < min_allowed:
                        restriction = _('Block Under-Validation')
                        details = _('Product "%s": demanded %s, done %s (min required: %s).') % (
                            product_name, demanded, total_done, min_allowed
                        )
                        self._sr_log(restriction, details, picking=picking)
                        raise UserError(_(
                            'Under-validation is not allowed.\n\n'
                            'Product: %s\n'
                            'Demanded: %s\n'
                            'Done: %s\n'
                            'Minimum required: %s%s\n\n'
                            'Please process the full quantity or contact your manager.'
                        ) % (
                            product_name, demanded, total_done, min_allowed,
                            _(' (with %s%% tolerance)') % tolerance if tolerance else ''
                        ))

                # ── Block negative stock ─────────────────────────────────────
                if (
                    self._sr_get('block_negative_stock', False)
                    and not is_admin
                    and picking.picking_type_code == 'outgoing'
                ):
                    quants = self.env['stock.quant'].search([
                        ('product_id', '=', move.product_id.id),
                        ('location_id', '=', move.location_id.id),
                    ])
                    current_stock = sum(quants.mapped('quantity'))
                    if total_done > current_stock:
                        restriction = _('Block Negative Stock')
                        details = _('Product "%s": stock %s, trying to deliver %s.') % (
                            product_name, current_stock, total_done
                        )
                        self._sr_log(restriction, details, picking=picking)
                        raise UserError(_(
                            'Negative stock is not allowed.\n\n'
                            'Product: %s\n'
                            'Current stock: %s\n'
                            'Trying to deliver: %s\n\n'
                            'You cannot deliver more than what is available in stock.'
                        ) % (product_name, current_stock, total_done))

                # ── Block unregistered vendor ────────────────────────────────
                if (
                    self._sr_get('block_unregistered_vendor', False)
                    and not is_admin
                    and picking.picking_type_code == 'incoming'
                    and picking.partner_id
                ):
                    vendor = self.env['product.supplierinfo'].search([
                        ('product_tmpl_id', '=', move.product_id.product_tmpl_id.id),
                        ('partner_id', 'child_of', picking.partner_id.id),
                    ], limit=1)
                    if not vendor:
                        restriction = _('Block Unregistered Vendor')
                        details = _('Vendor "%s" is not on the approved list for product "%s".') % (
                            picking.partner_id.name, product_name
                        )
                        self._sr_log(restriction, details, picking=picking)
                        raise UserError(_(
                            'Receiving from an unregistered vendor is not allowed.\n\n'
                            'Product: %s\n'
                            'Vendor: %s\n\n'
                            'This vendor is not on the approved vendor list for this product.\n'
                            'Go to the product form → Purchase tab → add the vendor first.'
                        ) % (product_name, picking.partner_id.name))

        return super()._action_done()
