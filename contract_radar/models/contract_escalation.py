# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ContractEscalation(models.Model):
    _name = 'contract.radar.escalation'
    _description = 'Contract Price Step-up'
    _order = 'date, id'

    contract_id = fields.Many2one('contract.radar', string='Contract', required=True, ondelete='cascade', index=True)
    currency_id = fields.Many2one(related='contract_id.currency_id', string='Currency')
    date = fields.Date(string='Effective Date', required=True)
    step_type = fields.Selection(
        [('percent', 'Increase by %'), ('amount', 'New Amount')],
        string='Step Type', required=True, default='percent')
    value = fields.Float(string='Value', digits=(16, 2), help="Percentage increase, or the new recurring amount per billing period.")
    note = fields.Char(string='Clause / Note')
    resulting_amount = fields.Monetary(string='Resulting Amount', currency_field='currency_id',
                                       compute='_compute_resulting_amount')

    @api.depends('date', 'value', 'step_type', 'contract_id.amount', 'contract_id.escalation_ids.date',
                 'contract_id.escalation_ids.value', 'contract_id.escalation_ids.step_type')
    def _compute_resulting_amount(self):
        for rec in self:
            if rec.contract_id and rec.date:
                rec.resulting_amount = rec.contract_id._period_amount_at(rec.date, force_scheduled=True)
            else:
                rec.resulting_amount = 0.0

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records.mapped('contract_id')._rebuild_cost_lines()
        return records

    def write(self, vals):
        res = super().write(vals)
        if {'date', 'value', 'step_type'} & set(vals):
            self.mapped('contract_id')._rebuild_cost_lines()
        return res

    def unlink(self):
        contracts = self.mapped('contract_id')
        res = super().unlink()
        contracts.exists()._rebuild_cost_lines()
        return res
