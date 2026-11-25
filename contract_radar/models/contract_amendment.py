# -*- coding: utf-8 -*-
from markupsafe import Markup

from odoo import _, fields, models
from odoo.exceptions import UserError


class ContractAmendment(models.Model):
    _name = 'contract.radar.amendment'
    _description = 'Contract Amendment'
    _order = 'date desc, id desc'

    contract_id = fields.Many2one('contract.radar', string='Contract', required=True, ondelete='cascade', index=True)
    currency_id = fields.Many2one(related='contract_id.currency_id', string='Currency')
    name = fields.Char(string='Amendment Title', required=True)
    date = fields.Date(string='Amendment Date', default=fields.Date.context_today)
    summary = fields.Text(string='What Changed')
    new_date_end = fields.Date(string='New End Date')
    new_amount = fields.Monetary(string='New Recurring Amount', currency_field='currency_id')
    applied = fields.Boolean(string='Applied', readonly=True, copy=False)
    attachment_ids = fields.Many2many(
        'ir.attachment', 'contract_radar_amendment_attachment_rel', 'amendment_id', 'attachment_id',
        string='Documents')

    def action_apply(self):
        for rec in self:
            if rec.applied:
                raise UserError(_("Amendment '%s' has already been applied.", rec.name))
            vals = {}
            lines = []
            if rec.new_date_end:
                vals['date_end'] = rec.new_date_end
                lines.append(_("End date -> %s", rec.new_date_end))
            if rec.new_amount:
                vals['amount'] = rec.new_amount
                lines.append(_("Recurring amount -> %s", rec.new_amount))
            if vals:
                rec.contract_id.write(vals)
            rec.contract_id.message_post(
                body=Markup("<p>{} <b>{}</b></p><p>{}</p>").format(
                    _("Amendment applied:"), rec.name, "; ".join(lines) or (rec.summary or "")))
            rec.applied = True
        return True
