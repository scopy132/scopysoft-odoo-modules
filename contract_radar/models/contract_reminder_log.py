# -*- coding: utf-8 -*-
from odoo import fields, models


class ContractReminderLog(models.Model):
    """Prevents the same reminder stage from being sent twice for the same deadline."""
    _name = 'contract.radar.reminder.log'
    _description = 'Contract Reminder Log'
    _order = 'sent_on desc, id desc'

    contract_id = fields.Many2one('contract.radar', string='Contract', required=True, ondelete='cascade', index=True)
    stage_days = fields.Integer(string='Stage (days before)')
    anchor_date = fields.Date(string='Deadline')
    notified = fields.Boolean(string='Notification Sent', help="False when a larger stage was caught up silently.")
    sent_on = fields.Datetime(string='Processed On', default=fields.Datetime.now)

    _uniq_stage = models.Constraint(
        'UNIQUE(contract_id, stage_days, anchor_date)',
        'This reminder stage was already processed for this deadline.',
    )
