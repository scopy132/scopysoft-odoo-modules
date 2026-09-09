from odoo import fields, models


class StockMove(models.Model):
    _inherit = 'stock.move'

    multi_product_qty = fields.Float(
        'Qty From Additional Products', default=0.0, copy=True,
        help="Portion of this move's quantity contributed by the "
             "manufacturing order's additional product lines, as opposed "
             "to the primary product's Bill of Materials. Used internally "
             "to safely recompute combined component quantities whenever "
             "product lines are added, edited, or removed. Must be copy=True: "
             "duplicating an MO copies move.product_uom_qty (already inflated "
             "with the additional product's share) verbatim, so this tracking "
             "value has to travel with it or _add_multi_product_components "
             "can't tell how much of that copied quantity to unwind before "
             "recomputing — losing it silently double-counts the additional "
             "product's components on every duplicate.")
