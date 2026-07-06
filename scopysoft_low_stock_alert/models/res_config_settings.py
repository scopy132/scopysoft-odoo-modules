# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    low_stock_digest_enabled = fields.Boolean(
        string='Enable Low Stock Digest',
        config_parameter='scopysoft_low_stock_alert.digest_enabled',
    )
    low_stock_digest_recipients = fields.Char(
        string='Digest Recipients',
        config_parameter='scopysoft_low_stock_alert.digest_recipients',
    )
