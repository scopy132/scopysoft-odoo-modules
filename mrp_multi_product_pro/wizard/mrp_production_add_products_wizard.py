from odoo import api, fields, models, _
from odoo.exceptions import UserError


class MrpProductionAddProductsWizard(models.TransientModel):
    _name = 'mrp.production.add.products.wizard'
    _description = 'Add Multiple Products to Manufacture'

    production_id = fields.Many2one('mrp.production', required=True)
    pick_ids = fields.One2many(
        'mrp.production.add.products.wizard.line', 'wizard_id', 'Products')
    paste_text = fields.Text(
        string='Paste a list',
        help="One product per line, as 'reference or name, quantity'. "
             "Example:\nFG-Widget-A, 5\nFG-Widget-B, 3\n"
             "Quantity is optional and defaults to 1.")

    def action_parse_pasted_list(self):
        """Turn pasted lines into product pick lines instead of clicking
        'Add a line' one product at a time — built for building a batch of
        10+ products in one go."""
        self.ensure_one()
        if not self.paste_text or not self.paste_text.strip():
            raise UserError(_("Paste a list of products first, one per line."))
        Product = self.env['product.product']
        not_found = []
        new_lines = []
        for raw_line in self.paste_text.splitlines():
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            if ',' in raw_line:
                ref, _sep, qty_str = raw_line.rpartition(',')
                ref = ref.strip()
                try:
                    qty = float(qty_str.strip())
                except ValueError:
                    ref, qty = raw_line, 1.0
            else:
                ref, qty = raw_line, 1.0
            product = Product.search([
                '|', '|',
                ('default_code', '=', ref),
                ('barcode', '=', ref),
                ('name', '=ilike', ref),
                ('type', 'in', ('consu', 'product')),
            ], limit=1)
            if not product:
                not_found.append(ref)
                continue
            new_lines.append((0, 0, {
                'product_id': product.id,
                'product_qty': qty or 1.0,
                'product_uom_id': product.uom_id.id,
            }))
        if new_lines:
            self.pick_ids = new_lines
        self.paste_text = False
        if not_found:
            raise UserError(_(
                "These weren't found by reference, barcode, or name and "
                "were skipped: %s\nEverything else was added below — add "
                "the skipped ones manually.", ', '.join(not_found)))
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_confirm(self):
        self.ensure_one()
        if not self.pick_ids:
            raise UserError(_("Add at least one product."))
        for pick in self.pick_ids:
            if not pick.product_id:
                continue
            self.production_id._add_product(
                pick.product_id, pick.product_qty,
                bom=pick.bom_id, uom=pick.product_uom_id)
        return {'type': 'ir.actions.act_window_close'}


class MrpProductionAddProductsWizardLine(models.TransientModel):
    _name = 'mrp.production.add.products.wizard.line'
    _description = 'Product pick line for the Add Products wizard'

    wizard_id = fields.Many2one('mrp.production.add.products.wizard', ondelete='cascade')
    product_id = fields.Many2one(
        'product.product', 'Product', required=True,
        domain="[('type', 'in', ('consu', 'product'))]")
    product_qty = fields.Float('Quantity', default=1.0, required=True)
    product_uom_id = fields.Many2one(
        'uom.uom', 'UoM', compute='_compute_product_uom_id',
        store=True, readonly=False)
    bom_id = fields.Many2one(
        'mrp.bom', 'BOM (auto)', domain="""[
            ('type', '=', 'normal'),
            '|', ('product_id', '=', product_id),
                 '&', ('product_tmpl_id', '=', product_tmpl_id), ('product_id', '=', False)
        ]""")
    product_tmpl_id = fields.Many2one(related='product_id.product_tmpl_id')

    @api.depends('product_id')
    def _compute_product_uom_id(self):
        for line in self:
            line.product_uom_id = line.product_id.uom_id if line.product_id else False

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
            self.bom_id = self.env['mrp.bom'].search(domain, limit=1)[:1]
