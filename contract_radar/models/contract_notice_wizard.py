# -*- coding: utf-8 -*-
import logging
import re

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.misc import format_date

_logger = logging.getLogger(__name__)

NOTICE_REPORT = 'contract_radar.report_cancellation_notice'


class ContractNoticeWizard(models.TransientModel):
    """Serve notice: stamp the date, build the letter as a PDF, attach it to the contract and email it."""
    _name = 'contract.radar.notice.wizard'
    _description = 'Serve Contract Notice'

    contract_id = fields.Many2one('contract.radar', string='Contract', required=True, readonly=True,
                                  ondelete='cascade')
    generate_letter = fields.Boolean(
        string='Generate Notice Letter (PDF)', default=True,
        help="Untick to only record that notice was served, without producing a letter.")
    send_email = fields.Boolean(string='Email the Counterparty', default=True)
    partner_ids = fields.Many2many('res.partner', string='Recipients')
    subject = fields.Char(string='Subject')
    body = fields.Html(string='Letter Text', sanitize=True)

    @api.model
    def _default_body(self, contract):
        company = contract.company_id.name or ''
        party = contract.partner_id.name or ''
        reference = contract.external_ref
        agreement = contract.name + (_(" (your reference %s)", reference) if reference else '')
        if contract.date_end:
            ending = _("which expires on %s", format_date(self.env, contract.date_end))
        else:
            ending = _("at the end of its current term")
        first = Markup("<p>{}</p>").format(
            _("We refer to the agreement \"%(agreement)s\" between %(company)s and %(party)s.",
              agreement=agreement, company=company, party=party))
        second = Markup("<p>{}</p>").format(
            _("Please accept this letter as formal notice that %(company)s does not wish to renew this agreement, "
              "%(ending)s. The agreement must therefore not renew automatically, and no charges should be "
              "raised for any renewal term.", company=company, ending=ending))
        third = Markup("<p>{}</p>").format(
            _("Kindly acknowledge receipt of this notice in writing and confirm the end date of the agreement."))
        return first + second + third

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        contract = self.env['contract.radar'].browse(res.get('contract_id')).exists()
        if contract:
            res.setdefault('subject', _("Notice of non-renewal: %s", contract.name))
            res.setdefault('body', self._default_body(contract))
            partner = contract.partner_id
            if partner.email:
                res.setdefault('partner_ids', [(6, 0, partner.ids)])
            else:
                res.setdefault('send_email', False)
        return res

    def _render_letter_pdf(self, contract):
        """The letter as PDF bytes, or None when it cannot be produced (for example wkhtmltopdf is missing)."""
        try:
            content, _fmt = self.env['ir.actions.report']._render_qweb_pdf(NOTICE_REPORT, contract.ids)
            return content
        except Exception:
            _logger.exception("Contract Radar: could not render the notice letter for contract %s", contract.id)
            return None

    def action_confirm(self):
        self.ensure_one()
        contract = self.contract_id
        if contract.state not in ('active', 'expiring'):
            raise UserError(_("Notice can only be served on an active or expiring contract."))
        if contract.notice_sent_date:
            raise UserError(_("Notice was already served on this contract."))
        recipients = self.partner_ids.filtered('email') if (self.generate_letter and self.send_email) \
            else self.env['res.partner']
        if self.generate_letter and self.send_email and not recipients:
            raise UserError(_("Add a recipient who has an email address, or untick \"Email the Counterparty\"."))

        contract.action_serve_notice()
        if not self.generate_letter:
            return {'type': 'ir.actions.act_window_close'}

        contract.write({'notice_body': self.body})
        pdf = self._render_letter_pdf(contract)
        attachment_ids = []
        if pdf:
            safe_ref = re.sub(r'[^A-Za-z0-9._-]+', '-', contract.reference or contract.name or 'contract')
            attachment = self.env['ir.attachment'].create({
                'name': _("Notice of non-renewal - %s.pdf", safe_ref),
                'raw': pdf,
                'mimetype': 'application/pdf',
                'res_model': contract._name,
                'res_id': contract.id,
            })
            attachment_ids = attachment.ids
        body = self.body or Markup('')
        if not pdf:
            body = body + Markup("<p><i>{}</i></p>").format(
                _("The PDF letter could not be generated, so the text above was used instead."))
        post_kwargs = dict(
            body=body,
            subject=self.subject or _("Notice of non-renewal: %s", contract.name),
            attachment_ids=attachment_ids,
            message_type='comment',
        )
        if recipients:
            contract.message_post(
                partner_ids=recipients.ids, subtype_xmlid='mail.mt_comment',
                email_layout_xmlid='mail.mail_notification_light', **post_kwargs)
        else:
            contract.message_post(subtype_xmlid='mail.mt_note', **post_kwargs)
        return {'type': 'ir.actions.act_window_close'}
