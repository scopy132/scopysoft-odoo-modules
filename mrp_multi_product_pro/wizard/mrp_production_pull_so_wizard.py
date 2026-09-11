from odoo import fields, models


class MrpProductionPullSoWizard(models.TransientModel):
    _name = 'mrp.production.pull.so.wizard'
    _description = 'Pick a Sales Order to Pull Products From'

    production_id = fields.Many2one('mrp.production', required=True)
    sale_order_id = fields.Many2one(
        'sale.order', 'Sales Order', required=True,
        domain="[('state', '=', 'sale')]")

    def action_confirm(self):
        self.ensure_one()
        return self.production_id._pull_products_from_sale_order(self.sale_order_id)
