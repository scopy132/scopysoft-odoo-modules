# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class LowStockDigest(models.TransientModel):
    _name = 'low.stock.digest'
    _description = 'Low Stock Digest Sender'

    def _log(self, message, level='info'):
        """Write to both the python logger (server console/log file) and
        the ir.logging table so the message is also visible under
        Settings > Technical > Logging inside Odoo itself."""
        getattr(_logger, level)(message)
        try:
            self.env['ir.logging'].sudo().create({
                'name': 'scopysoft_low_stock_alert',
                'type': 'server',
                'dbname': self.env.cr.dbname,
                'level': level.upper(),
                'message': message,
                'path': 'low_stock_digest',
                'func': 'send_low_stock_digest',
                'line': '0',
            })
        except Exception:
            # Never let logging itself break the digest run.
            _logger.exception('Low Stock Digest: failed to write ir.logging entry.')

    @api.model
    def send_low_stock_digest(self):
        """Cron entry point. Sends a summary email of all currently
        low-stock products to the configured recipients.

        Designed to fail silently and safely if:
        - No outgoing mail server is configured
        - No recipients are configured
        - No products are currently low on stock

        This must never raise and break the scheduled action queue.
        """
        icp = self.env['ir.config_parameter'].sudo()
        enabled = icp.get_param('scopysoft_low_stock_alert.digest_enabled', 'False')
        if enabled not in ('True', 'true', '1'):
            self._log('Low Stock Digest: disabled, skipping.')
            return

        recipients = icp.get_param('scopysoft_low_stock_alert.digest_recipients', '')
        recipient_emails = [e.strip() for e in recipients.split(',') if e.strip()]
        if not recipient_emails:
            self._log('Low Stock Digest: no recipients configured, skipping.')
            return

        mail_server = self.env['ir.mail_server'].sudo().search([], limit=1)
        if not mail_server and not icp.get_param('mail.catchall.domain'):
            self._log(
                'Low Stock Digest: no outgoing mail server configured. '
                'Skipping digest — configure one under Settings > Technical > '
                'Outgoing Mail Servers to enable this feature.',
                level='warning',
            )
            return

        Product = self.env['product.template'].sudo()
        candidates = Product.search([
            ('type', 'in', ['product', 'consu']),
            ('low_stock_threshold', '>', 0),
        ])
        low_stock_products = candidates.filtered(lambda p: p.is_low_stock)

        if not low_stock_products:
            self._log('Low Stock Digest: no products currently low, skipping email.')
            return

        rows = ''.join(
            '<tr>'
            '<td style="padding:6px 10px;border-bottom:1px solid #eee;">%s</td>'
            '<td style="padding:6px 10px;border-bottom:1px solid #eee;">%.2f %s</td>'
            '<td style="padding:6px 10px;border-bottom:1px solid #eee;">%.2f</td>'
            '</tr>' % (p.name, p.qty_available, p.uom_id.name, p.low_stock_threshold)
            for p in low_stock_products
        )

        body_html = '''
            <div style="font-family:sans-serif;font-size:13px;color:#333;">
                <h3 style="color:#e05e1e;">Low Stock Digest</h3>
                <p>%d product(s) are currently at or below their stock threshold:</p>
                <table style="border-collapse:collapse;width:100%%;">
                    <tr style="background:#f5f5f5;">
                        <th style="padding:6px 10px;text-align:left;">Product</th>
                        <th style="padding:6px 10px;text-align:left;">On Hand</th>
                        <th style="padding:6px 10px;text-align:left;">Threshold</th>
                    </tr>
                    %s
                </table>
                <p style="color:#999;font-size:11px;margin-top:16px;">
                    Sent by Low Stock Alert (ScopySoft)
                </p>
            </div>
        ''' % (len(low_stock_products), rows)

        try:
            mail_values = {
                'subject': 'Low Stock Digest — %d product(s) need attention' % len(low_stock_products),
                'body_html': body_html,
                'email_to': ','.join(recipient_emails),
                'auto_delete': True,
            }
            self.env['mail.mail'].sudo().create(mail_values).send()
            self._log('Low Stock Digest: sent to %s' % recipient_emails)
        except Exception:
            self._log(
                'Low Stock Digest: failed to send email. This will not affect '
                'other module functionality — check your outgoing mail server settings.',
                level='warning',
            )
