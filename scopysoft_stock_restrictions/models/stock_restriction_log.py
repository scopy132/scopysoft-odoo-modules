# -*- coding: utf-8 -*-
from odoo import fields, models


class StockRestrictionLog(models.Model):
    _name = 'stock.restriction.log'
    _description = 'Stock Restriction Audit Log'
    _order = 'create_date desc, id desc'
    _rec_name = 'restriction'

    user_id = fields.Many2one(
        'res.users', string='User', required=True, ondelete='restrict', index=True,
    )
    picking_id = fields.Many2one(
        'stock.picking', string='Stock Operation', ondelete='set null',
    )
    picking_name = fields.Char(
        string='Operation Reference',
        related='picking_id.name', store=True,
    )
    restriction = fields.Char(string='Restriction Triggered', required=True)
    details = fields.Text(string='Details')
    bypassed_by_admin = fields.Boolean(
        string='Admin Bypass',
        default=False,
        help='True if an Administrator performed this action bypassing the restriction.',
    )
    company_id = fields.Many2one(
        'res.company', string='Company',
        default=lambda self: self.env.company, index=True,
    )
    create_date = fields.Datetime(string='Date & Time', readonly=True)
