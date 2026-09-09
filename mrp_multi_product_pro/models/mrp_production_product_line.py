from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Command


class MrpProductionProductLine(models.Model):
    _name = 'mrp.production.product.line'
    _description = 'Manufacturing Order Product Line'
    _order = 'sequence, id'

    production_id = fields.Many2one(
        'mrp.production', 'Manufacturing Order',
        required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    product_id = fields.Many2one(
        'product.product', 'Product',
        required=True, check_company=True,
        domain="[('type', '=', 'consu')]")
    product_tmpl_id = fields.Many2one(
        'product.template', related='product_id.product_tmpl_id', readonly=True)
    product_qty = fields.Float(
        'Quantity', required=True, default=1.0,
        digits='Product Unit of Measure')
    product_uom_id = fields.Many2one(
        'uom.uom', 'Unit of Measure',
        compute='_compute_product_uom_id', store=True, readonly=False,
        domain="[('category_id', '=', product_uom_category_id)]")
    product_uom_category_id = fields.Many2one(
        related='product_id.uom_id.category_id', readonly=True)
    bom_id = fields.Many2one(
        'mrp.bom', 'Bill of Materials',
        domain="""[
            ('type', '=', 'normal'),
            '|', ('product_id', '=', product_id),
                 '&', ('product_tmpl_id', '=', product_tmpl_id), ('product_id', '=', False)
        ]""",
        check_company=True)
    move_id = fields.Many2one(
        'stock.move', 'Finished Stock Move',
        readonly=True, copy=False, ondelete='set null')
    qty_done = fields.Float(
        'Quantity Done', related='move_id.quantity', readonly=True)
    company_id = fields.Many2one(related='production_id.company_id', store=True)
    state = fields.Selection(related='production_id.state', store=True)
    product_tracking = fields.Selection(related='product_id.tracking', readonly=True)
    lot_producing_ids = fields.Many2many(
        'stock.lot', string='Lot/Serial Numbers',
        domain="[('product_id', '=', product_id)]",
        check_company=True, copy=False)
    component_status = fields.Selection([
        ('available', 'Available'),
        ('partial', 'Partial'),
        ('unavailable', 'Not Available'),
    ], string='Component Status', compute='_compute_component_status')
    sale_line_id = fields.Many2one(
        'sale.order.line', 'Source Sale Line', copy=False, readonly=True,
        help="Set when this line was created via 'Pull from Sales Order'.")

    @api.depends('product_id')
    def _compute_product_uom_id(self):
        for line in self:
            if line.product_id:
                line.product_uom_id = line.product_id.uom_id
            else:
                line.product_uom_id = False

    def _compute_component_status(self):
        for line in self:
            if not line.bom_id:
                line.component_status = 'unavailable'
                continue
            _boms, bom_lines = line.bom_id.explode(line.product_id, line.product_qty)
            statuses = set()
            for bom_line, line_data in bom_lines:
                component = bom_line.product_id
                if component.type != 'consu':
                    continue
                available = component.with_company(line.company_id).qty_available
                needed = line_data['qty']
                if available >= needed:
                    statuses.add('available')
                elif available > 0:
                    statuses.add('partial')
                else:
                    statuses.add('unavailable')
            if not statuses or statuses == {'available'}:
                line.component_status = 'available'
            elif 'unavailable' in statuses and 'available' not in statuses and 'partial' not in statuses:
                line.component_status = 'unavailable'
            else:
                line.component_status = 'partial'

    @api.onchange('product_id')
    def _onchange_product_id(self):
        if self.product_id:
            domain = [
                ('type', '=', 'normal'),
                '|',
                ('product_id', '=', self.product_id.id),
                '&',
                ('product_tmpl_id', '=', self.product_id.product_tmpl_id.id),
                ('product_id', '=', False),
            ]
            boms = self.env['mrp.bom'].search(domain, limit=1)
            self.bom_id = boms[:1]

    @api.constrains('lot_producing_ids', 'product_tracking')
    def _check_lot_producing_ids(self):
        for line in self:
            if line.product_tracking == 'lot' and len(line.lot_producing_ids) > 1:
                raise ValidationError(_(
                    'You cannot assign more than one lot to product "%s".',
                    line.product_id.display_name,
                ))

    @api.constrains('product_id', 'production_id')
    def _check_product_not_same_as_main(self):
        for line in self:
            if line.product_id == line.production_id.product_id:
                raise ValidationError(_(
                    'Product "%s" is already the primary product of this '
                    'Manufacturing Order — pick a different one.',
                    line.product_id.display_name,
                ))

    @api.constrains('product_id', 'production_id')
    def _check_no_duplicate_product(self):
        for line in self:
            duplicates = line.production_id.multi_product_line_ids.filtered(
                lambda l: l.product_id == line.product_id)
            if len(duplicates) > 1:
                raise ValidationError(_(
                    'Product "%s" is already on this Manufacturing Order. '
                    'Edit its existing quantity instead of adding it twice.',
                    line.product_id.display_name,
                ))

    def _prepare_stock_lot_values(self):
        self.ensure_one()
        name = self.env['ir.sequence'].next_by_code('stock.lot.serial')
        exist_lot = not name or self.env['stock.lot'].search([
            ('product_id', '=', self.product_id.id),
            '|', ('company_id', '=', False), ('company_id', '=', self.company_id.id),
            ('name', '=', name),
        ], limit=1)
        if exist_lot:
            name = self.env['stock.lot']._get_next_serial(self.company_id, self.product_id)
        if not name:
            raise UserError(_("Please set the first Serial Number or a default sequence"))
        return {'product_id': self.product_id.id, 'name': name}

    def unlink(self):
        for line in self:
            if line.production_id.state not in ('draft', 'confirmed'):
                raise UserError(_(
                    'You can\'t remove product "%s" — this Manufacturing '
                    'Order has already started production. Products can '
                    'only be added or removed while the order is in Draft '
                    'or Confirmed state.',
                    line.product_id.display_name,
                ))
        return super().unlink()

    def action_generate_serial(self):
        self.ensure_one()
        if self.product_tracking == 'none':
            return
        if self.product_tracking == 'lot' and self.lot_producing_ids:
            raise UserError(_('You cannot set more than 1 lot per product'))
        self.lot_producing_ids = [Command.create(self._prepare_stock_lot_values())]
