# -*- coding: utf-8 -*-
from odoo import fields, models

from .bill_matching import BILL_STATES


class ContractCostLine(models.Model):
    _inherit = 'contract.radar.cost.line'

    bill_state = fields.Selection(BILL_STATES, string='Bill Check', default='na', index=True)
    bill_ids = fields.Many2many(
        'account.move', 'contract_radar_cost_line_bill_rel', 'cost_line_id', 'move_id', string='Matched Bills')
    billed_amount = fields.Monetary(string='Billed', currency_field='currency_id')
    bill_difference = fields.Monetary(string='Billed vs Agreed', currency_field='currency_id',
                                      help="Positive: the vendor billed more than the contract says.")
