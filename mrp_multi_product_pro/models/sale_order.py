from odoo import models, _
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_create_multi_product_mo(self):
        """Create one Manufacturing Order covering every manufacturable
        product on this order, in one click — no need to open a blank MO
        and pick a first product manually first."""
        self.ensure_one()
        lines = self.order_line.filtered(
            lambda l: l.product_id.type in ('consu', 'product') and not l.display_type
        )
        if not lines:
            raise UserError(_(
                "This order has no manufacturable products to create a "
                "Manufacturing Order from."))

        Bom = self.env['mrp.bom']

        def find_bom(product):
            domain = [
                ('type', '=', 'normal'),
                '|',
                ('product_id', '=', product.id),
                '&',
                ('product_tmpl_id', '=', product.product_tmpl_id.id),
                ('product_id', '=', False),
            ]
            return Bom.search(domain, limit=1)

        first = lines[0]
        production = self.env['mrp.production'].create({
            'product_id': first.product_id.id,
            'product_qty': first.product_uom_qty,
            'product_uom_id': first.product_uom.id,
            'bom_id': find_bom(first.product_id).id,
            'origin': self.name,
        })

        production._link_move_to_sale_line(production.move_finished_ids, first)

        for sol in lines[1:]:
            production._add_product(sol.product_id, sol.product_uom_qty, uom=sol.product_uom, sale_line=sol)

        return {
            'type': 'ir.actions.act_window',
            'name': _('Manufacturing Order'),
            'res_model': 'mrp.production',
            'view_mode': 'form',
            'res_id': production.id,
        }
