# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.tools.misc import format_date


class ContractCostLine(models.Model):
    """One projected payment of a contract. Rebuilt by the contract itself; read-only for users.
    Powers the 12-month commitment forecast (pivot / graph) and the dashboard."""
    _name = 'contract.radar.cost.line'
    _description = 'Contract Projected Payment'
    _order = 'date, id'

    contract_id = fields.Many2one('contract.radar', string='Contract', required=True, ondelete='cascade', index=True)
    date = fields.Date(string='Payment Date', required=True, index=True)
    amount = fields.Monetary(string='Amount', currency_field='currency_id')
    amount_company = fields.Monetary(string='Amount (Company Currency)', currency_field='company_currency_id')
    currency_id = fields.Many2one(related='contract_id.currency_id', store=True, string='Currency')
    company_id = fields.Many2one(related='contract_id.company_id', store=True, index=True, string='Company')
    company_currency_id = fields.Many2one(related='company_id.currency_id', store=True, string='Company Currency')
    contract_type = fields.Selection(related='contract_id.contract_type', store=True, string='Type')
    flow = fields.Selection(related='contract_id.flow', store=True, string='Flow')
    state = fields.Selection(related='contract_id.state', store=True, string='Contract Status')
    partner_id = fields.Many2one(related='contract_id.partner_id', store=True, string='Counterparty')
    user_id = fields.Many2one(related='contract_id.user_id', store=True, string='Responsible')

    @api.depends('contract_id.display_name', 'date')
    def _compute_display_name(self):
        for line in self:
            base = line.contract_id.display_name or ''
            line.display_name = '%s (%s)' % (base, format_date(self.env, line.date)) if line.date else base
