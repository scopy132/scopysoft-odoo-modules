# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    vendor_price_alert_threshold = fields.Float(
        string='Price Change Alert Threshold (%)',
        config_parameter='scopysoft_vendor_price_history.alert_threshold',
        default=15.0,
        help='When a vendor price changes by at least this percentage, an activity '
             'is scheduled on the product and the change is flagged as significant '
             'in Vendor Price History.'
    )
