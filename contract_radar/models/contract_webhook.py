# -*- coding: utf-8 -*-
import ipaddress
import logging
from urllib.parse import urlparse

import requests

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

WEBHOOK_TIMEOUT = 5  # seconds; a slow endpoint must never hold up a user or the daily cron


class ContractWebhook(models.Model):
    """An outgoing notification endpoint (Slack, Mattermost, Google Chat, Discord, or anything taking JSON)."""
    _name = 'contract.radar.webhook'
    _description = 'Contract Notification Webhook'
    _order = 'name, id'

    name = fields.Char(string='Name', required=True, help="For example: Finance channel on Slack.")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company,
                                 help="Leave empty to notify for the contracts of every company.")
    url = fields.Char(string='Webhook URL', required=True,
                      help="The https:// address given to you by Slack, Google Chat, Discord, Power Automate, "
                           "Zapier or n8n. Only managers can see it.")
    payload_format = fields.Selection(
        [('text', 'Slack / Mattermost / Google Chat'),
         ('discord', 'Discord'),
         ('json', 'Generic JSON (Teams Workflows, Zapier, n8n, ...)')],
        string='Format', required=True, default='text')
    on_reminder = fields.Boolean(string='Deadline Reminders', default=True)
    on_notice = fields.Boolean(string='Notice Served', default=True)
    on_decision = fields.Boolean(string='Decisions Approved', default=True)
    last_sent = fields.Datetime(string='Last Sent', readonly=True)
    last_status = fields.Char(string='Last Result', readonly=True)

    @api.constrains('url')
    def _check_url(self):
        for rec in self:
            parsed = urlparse((rec.url or '').strip())
            host = (parsed.hostname or '').lower()
            if parsed.scheme != 'https' or not host:
                raise ValidationError(_("The webhook URL must start with https://"))
            if host == 'localhost' or host.endswith(('.local', '.internal', '.localhost')):
                raise ValidationError(_("The webhook URL must point to a public address."))
            try:
                ip = ipaddress.ip_address(host)
            except ValueError:
                continue
            if not ip.is_global:
                raise ValidationError(_("The webhook URL must point to a public address."))

    def _build_payload(self, event, text, extra=None):
        self.ensure_one()
        if self.payload_format == 'discord':
            return {'content': text}
        if self.payload_format == 'json':
            payload = {'event': event, 'text': text}
            payload.update(extra or {})
            return payload
        return {'text': text}

    def _send(self, event, text, extra=None):
        """POST the message. Returns True on a 2xx answer. Errors are stored on the record, never raised."""
        self.ensure_one()
        status = ''
        ok = False
        try:
            response = requests.post(
                self.url.strip(), json=self._build_payload(event, text, extra), timeout=WEBHOOK_TIMEOUT)
            ok = bool(response.ok)
            status = str(response.status_code)
        except requests.exceptions.RequestException as exc:
            status = type(exc).__name__
            _logger.warning("Contract Radar: webhook %s (%s) failed: %s", self.id, self.name, status)
        self.sudo().write({'last_sent': fields.Datetime.now(), 'last_status': status})
        return ok

    def action_send_test(self):
        self.ensure_one()
        ok = self._send('test', _("Test message from Contract Radar."), {'contract': '', 'contract_id': 0,
                                                                          'counterparty': ''})
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Webhook test"),
                'message': _("Delivered (%s).", self.last_status) if ok
                else _("Not delivered (%s). Check the URL.", self.last_status or _("no answer")),
                'type': 'success' if ok else 'warning',
                'sticky': False,
            },
        }
