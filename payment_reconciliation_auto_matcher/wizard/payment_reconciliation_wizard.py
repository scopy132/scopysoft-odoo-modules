# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError

PAYABLE_RECEIVABLE_TYPES = ('asset_receivable', 'liability_payable')
AMOUNT_TOLERANCE_RATIO = 0.3

MAP_PARTNER_TYPE_MOVE_TYPES = {
    'customer': ('out_invoice', 'out_refund'),
    'supplier': ('in_invoice', 'in_refund'),
}


def _score_pair(payment, invoice, memo):
    """Shared scoring for a (payment, invoice) pair, used by both anchor directions."""
    residual = abs(invoice.amount_residual)
    amt_match = payment.currency_id.is_zero(residual - abs(payment.amount))
    tolerance = abs(payment.amount) * AMOUNT_TOLERANCE_RATIO
    amt_close = amt_match or abs(residual - abs(payment.amount)) <= tolerance
    ref_match = any(
        token and token in memo
        for token in (invoice.name, invoice.ref, invoice.payment_reference)
    )
    if amt_match and ref_match:
        score = 3
    elif amt_match:
        score = 2
    elif ref_match:
        score = 1
    else:
        score = 0
    return score, amt_match, ref_match, amt_close, residual


class AccountPaymentReconciliationWizard(models.TransientModel):
    _name = 'account.payment.reconciliation.wizard'
    _description = 'Payment Reconciliation Auto-Matcher'

    journal_id = fields.Many2one('account.journal', string='Journal', domain=[('type', 'in', ('bank', 'cash'))],
                                  help="Optional filter: only scan payments in this journal.")
    partner_id = fields.Many2one('res.partner', string='Partner', help="Optional filter: only scan payments for this partner.")
    date_from = fields.Date(string='Payment Date From')
    date_to = fields.Date(string='Payment Date To')
    match_line_ids = fields.One2many('account.payment.reconciliation.wizard.line', 'wizard_id', string='Suggested Matches')
    result_message = fields.Char(readonly=True)

    # Which side is the fixed "fact" and which side is being searched for:
    # - 'payment': you're holding loose payments and hunting for their invoice.
    # - 'invoice': you're holding unpaid invoices/bills and hunting for a payment.
    # Set automatically by how the wizard was launched — not something to toggle mid-review.
    anchor_mode = fields.Selection([
        ('payment', 'Payment is fixed — find its invoice/bill'),
        ('invoice', 'Invoice/Bill is fixed — find its payment'),
    ], default='payment', readonly=True)

    # Populated automatically when launched from a selection in the Invoices/Bills
    # or Payments list, so the scan only covers what the user actually picked.
    scope_invoice_ids = fields.Many2many('account.move', 'wizard_scope_invoice_rel', string='Scoped Invoices/Bills')
    scope_payment_ids = fields.Many2many('account.payment', 'wizard_scope_payment_rel', string='Scoped Payments')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        context = self._context or {}
        active_model = context.get('active_model')
        active_ids = context.get('active_ids') or []
        if active_model == 'account.move' and active_ids:
            res['scope_invoice_ids'] = [(6, 0, active_ids)]
            res['anchor_mode'] = 'invoice'
        elif active_model == 'account.payment' and active_ids:
            res['scope_payment_ids'] = [(6, 0, active_ids)]
            res['anchor_mode'] = 'payment'
        # NOTE: matches are no longer pre-computed here. Doing it on an in-memory,
        # not-yet-saved record (via self.new()) caused two separate bugs (infinite
        # recursion, then rows silently losing their data on first save). The two
        # server actions below create a REAL saved record and call the normal,
        # already-correct action_find_matches() on it instead.
        return res

    def _reopen_view(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    # ---------- candidate lookups, shared by both anchor directions ----------

    def _search_open_invoices(self, partner, move_types, currency):
        domain = [
            ('move_type', 'in', move_types),
            ('state', '=', 'posted'),
            ('payment_state', 'in', ('not_paid', 'partial')),
            ('currency_id', '=', currency.id),
            '|', ('partner_id', '=', partner.id), ('partner_id.commercial_partner_id', '=', partner.id),
        ]
        return self.env['account.move'].search(domain)

    def _search_open_payments(self, partner, partner_type, currency):
        domain = [
            ('is_reconciled', '=', False),
            ('state', 'not in', ('draft', 'cancelled', 'rejected')),
            ('partner_type', '=', partner_type),
            ('currency_id', '=', currency.id),
            '|', ('partner_id', '=', partner.id), ('partner_id.commercial_partner_id', '=', partner.id),
        ]
        return self.env['account.payment'].search(domain)

    def _get_scoped_payments(self):
        """All unreconciled payments within the wizard's filters/scope, regardless of anchor."""
        self.ensure_one()
        if self.scope_payment_ids:
            payments = self.scope_payment_ids
        else:
            domain = [('is_reconciled', '=', False), ('state', 'not in', ('draft', 'cancelled', 'rejected'))]
            if self.journal_id:
                domain.append(('journal_id', '=', self.journal_id.id))
            if self.partner_id:
                domain.append(('partner_id', '=', self.partner_id.id))
            if self.date_from:
                domain.append(('date', '>=', self.date_from))
            if self.date_to:
                domain.append(('date', '<=', self.date_to))
            if self.scope_invoice_ids:
                partner_ids = self.scope_invoice_ids.mapped('partner_id.commercial_partner_id').ids
                domain.append(('partner_id.commercial_partner_id', 'in', partner_ids))
            payments = self.env['account.payment'].search(domain)
        return payments.filtered(lambda p: not p.is_reconciled)

    def _get_scoped_invoices(self):
        """All open invoices/bills within the wizard's filters/scope, regardless of anchor."""
        self.ensure_one()
        if self.scope_invoice_ids:
            return self.scope_invoice_ids.filtered(lambda m: m.state == 'posted' and m.payment_state in ('not_paid', 'partial'))
        domain = [('state', '=', 'posted'), ('payment_state', 'in', ('not_paid', 'partial'))]
        if self.partner_id:
            partner = self.partner_id.commercial_partner_id or self.partner_id
            domain.append('|')
            domain.append(('partner_id', '=', partner.id))
            domain.append(('partner_id.commercial_partner_id', '=', partner.id))
        if self.date_from:
            domain.append(('invoice_date', '>=', self.date_from))
        if self.date_to:
            domain.append(('invoice_date', '<=', self.date_to))
        if self.scope_payment_ids:
            partner_ids = self.scope_payment_ids.mapped('partner_id.commercial_partner_id').ids
            domain.append(('partner_id.commercial_partner_id', 'in', partner_ids))
        return self.env['account.move'].search(domain)

    def _row_dict(self, payment, invoice, candidate_invoice_ids, candidate_payment_ids, confidence, to_reconcile, score, amount_diff):
        return {
            'payment_id': payment.id if payment else False,
            'invoice_id': invoice.id if invoice else False,
            'candidate_invoice_ids': candidate_invoice_ids,
            'candidate_payment_ids': candidate_payment_ids,
            'partner_name': (payment or invoice).partner_id.display_name,
            'payment_amount': payment.amount if payment else abs(invoice.amount_residual),
            'invoice_residual': abs(invoice.amount_residual) if invoice else 0.0,
            'confidence': confidence,
            'to_reconcile': to_reconcile,
            'score': score,
            'amount_diff': amount_diff,
        }

    def _compute_matches_payment_anchor(self):
        """Payment is the fixed fact; find which invoice/bill it should settle."""
        raw_rows = []
        for payment in self._get_scoped_payments():
            move_types = MAP_PARTNER_TYPE_MOVE_TYPES.get(payment.partner_type, ())
            if not move_types or not payment.partner_id:
                continue
            partner = payment.partner_id.commercial_partner_id or payment.partner_id
            if self.scope_invoice_ids:
                candidates = self.scope_invoice_ids.filtered(
                    lambda m: m.move_type in move_types and m.state == 'posted'
                    and m.payment_state in ('not_paid', 'partial') and m.currency_id == payment.currency_id
                    and (m.partner_id.commercial_partner_id or m.partner_id) == partner
                )
            else:
                candidates = self._search_open_invoices(partner, move_types, payment.currency_id)
            if not candidates:
                # No open invoice/bill anywhere for this partner+currency+type — still
                # surface the payment instead of silently dropping it from the results.
                raw_rows.append(self._row_dict(
                    payment, False, [], [payment.id],
                    'no_open_invoice', False, 0, None,
                ))
                continue
            memo = payment.memo or ''

            close_candidates = self.env['account.move']
            scored = []
            for inv in candidates:
                score, amt_match, ref_match, amt_close, residual = _score_pair(payment, inv, memo)
                if amt_close or ref_match:
                    close_candidates |= inv
                if score:
                    scored.append((score, inv, amt_match, ref_match, residual))

            if not scored:
                fallback_pool = close_candidates or candidates
                closest = min(fallback_pool, key=lambda m: abs(abs(m.amount_residual) - abs(payment.amount))) if fallback_pool else False
                raw_rows.append(self._row_dict(
                    payment, closest, (fallback_pool or candidates).ids, [payment.id],
                    'no_close_match', False, 0,
                    abs(abs(closest.amount_residual) - abs(payment.amount)) if closest else None,
                ))
                continue

            best_score = max(s[0] for s in scored)
            top = [s for s in scored if s[0] == best_score]
            candidate_invoice_ids = close_candidates.ids

            if len(top) > 1:
                default_inv = top[0][1]
                raw_rows.append(self._row_dict(
                    payment, default_inv, candidate_invoice_ids, [payment.id],
                    'multiple_matches', False, best_score,
                    abs(abs(default_inv.amount_residual) - abs(payment.amount)),
                ))
                continue

            score, inv, amt_match, ref_match, residual = top[0]
            confidence = 'exact_ref' if (amt_match and ref_match) else ('exact_amount' if amt_match else 'reference_only')
            raw_rows.append(self._row_dict(
                payment, inv, candidate_invoice_ids, [payment.id],
                confidence, confidence in ('exact_ref', 'exact_amount'), score,
                abs(residual - abs(payment.amount)),
            ))

        self._resolve_collisions(raw_rows, key='invoice_id')
        return raw_rows

    def _compute_matches_invoice_anchor(self):
        """Invoice/bill is the fixed fact; find which payment already covers it."""
        raw_rows = []
        for invoice in self._get_scoped_invoices():
            partner_type = 'customer' if invoice.move_type in ('out_invoice', 'out_refund') else 'supplier'
            partner = invoice.partner_id.commercial_partner_id or invoice.partner_id
            if self.scope_payment_ids:
                candidates = self.scope_payment_ids.filtered(
                    lambda p: not p.is_reconciled and p.partner_type == partner_type
                    and p.currency_id == invoice.currency_id
                    and (p.partner_id.commercial_partner_id or p.partner_id) == partner
                )
            else:
                candidates = self._search_open_payments(partner, partner_type, invoice.currency_id)
            if not candidates:
                # No unreconciled payment anywhere for this partner+currency+type — still
                # surface the invoice/bill instead of silently dropping it from the results.
                raw_rows.append(self._row_dict(
                    False, invoice, [invoice.id], [],
                    'no_open_invoice', False, 0, None,
                ))
                continue
            residual = abs(invoice.amount_residual)

            close_candidates = self.env['account.payment']
            scored = []
            for pay in candidates:
                memo = pay.memo or ''
                score, amt_match, ref_match, amt_close, _residual = _score_pair(pay, invoice, memo)
                if amt_close or ref_match:
                    close_candidates |= pay
                if score:
                    scored.append((score, pay, amt_match, ref_match))

            if not scored:
                fallback_pool = close_candidates or candidates
                closest = min(fallback_pool, key=lambda p: abs(abs(p.amount) - residual)) if fallback_pool else False
                raw_rows.append(self._row_dict(
                    closest, invoice, [invoice.id], (fallback_pool or candidates).ids,
                    'no_close_match', False, 0,
                    abs(abs(closest.amount) - residual) if closest else None,
                ))
                continue

            best_score = max(s[0] for s in scored)
            top = [s for s in scored if s[0] == best_score]
            candidate_payment_ids = close_candidates.ids

            if len(top) > 1:
                default_pay = top[0][1]
                raw_rows.append(self._row_dict(
                    default_pay, invoice, [invoice.id], candidate_payment_ids,
                    'multiple_matches', False, best_score,
                    abs(abs(default_pay.amount) - residual),
                ))
                continue

            score, pay, amt_match, ref_match = top[0]
            confidence = 'exact_ref' if (amt_match and ref_match) else ('exact_amount' if amt_match else 'reference_only')
            raw_rows.append(self._row_dict(
                pay, invoice, [invoice.id], candidate_payment_ids,
                confidence, confidence in ('exact_ref', 'exact_amount'), score,
                abs(abs(pay.amount) - residual),
            ))

        self._resolve_collisions(raw_rows, key='payment_id')
        return raw_rows

    def _resolve_collisions(self, raw_rows, key):
        """Several rows can independently land on the same invoice (or the same payment,
        in invoice-anchor mode) within one batch. Only one can actually be reconciled —
        keep the best-scored/closest row ticked and flag the rest for review instead of
        letting them fail silently during confirmation."""
        grouped = {}
        for row in raw_rows:
            if row['to_reconcile'] and row[key]:
                grouped.setdefault(row[key], []).append(row)
        for _shared_id, rows in grouped.items():
            if len(rows) <= 1:
                continue
            rows.sort(key=lambda r: (-r['score'], r['amount_diff'] if r['amount_diff'] is not None else 0))
            for loser in rows[1:]:
                loser['confidence'] = 'claimed_elsewhere'
                loser['to_reconcile'] = False

    def _compute_matches(self):
        raw_rows = self._compute_matches_invoice_anchor() if self.anchor_mode == 'invoice' else self._compute_matches_payment_anchor()
        return [(0, 0, {
            **{k: v for k, v in row.items() if k not in ('score', 'amount_diff', 'candidate_invoice_ids', 'candidate_payment_ids')},
            'candidate_invoice_ids': [(6, 0, row['candidate_invoice_ids'])],
            'candidate_payment_ids': [(6, 0, row['candidate_payment_ids'])],
        }) for row in raw_rows]

    REVIEW_CONFIDENCES = ('multiple_matches', 'no_close_match', 'no_open_invoice', 'claimed_elsewhere')

    def _skipped_scope_note(self):
        """Selections that were dropped before scanning even started (already reconciled,
        already settled, or no longer posted) so the count discrepancy isn't a silent
        mystery — e.g. 'picked 3 payments, only 1 row appears'."""
        self.ensure_one()
        if self.anchor_mode == 'payment' and self.scope_payment_ids:
            already_done = self.scope_payment_ids.filtered(lambda p: p.is_reconciled)
            if already_done:
                return " " + _("%d of your %d selected payment(s) are already reconciled and were skipped.") % (
                    len(already_done), len(self.scope_payment_ids))
        elif self.anchor_mode == 'invoice' and self.scope_invoice_ids:
            not_open = self.scope_invoice_ids.filtered(
                lambda m: not (m.state == 'posted' and m.payment_state in ('not_paid', 'partial')))
            if not_open:
                return " " + _("%d of your %d selected invoice/bill(s) are already settled or not posted, and were skipped.") % (
                    len(not_open), len(self.scope_invoice_ids))
        return ""

    def action_find_matches(self):
        self.ensure_one()
        self.match_line_ids = [(5, 0, 0)]
        skipped_note = self._skipped_scope_note()
        lines = self._compute_matches()
        self.match_line_ids = lines
        if not lines:
            self.result_message = _("Nothing found for the current scope/filters.") + skipped_note
        else:
            needs_review = len([1 for l in lines if l[2].get('confidence') in self.REVIEW_CONFIDENCES])
            msg = _("%d row(s) found.") % len(lines)
            if needs_review:
                msg += " " + _("%d need your review — pick the match yourself.") % needs_review
            self.result_message = msg + skipped_note
        return self._reopen_view()

    def action_reconcile_confirmed(self):
        self.ensure_one()
        selected = self.match_line_ids.filtered(lambda l: l.to_reconcile)
        if not selected:
            raise UserError(_("Tick at least one match before reconciling (or add a manual row)."))

        done = 0
        failed = []
        for line in selected:
            payment = line.payment_id
            invoice = line.invoice_id
            if not payment or not invoice:
                failed.append(_("A row is missing a payment or an invoice/bill — skipped."))
                continue
            try:
                payment_line = payment.move_id.line_ids.filtered(
                    lambda l: l.account_id.account_type in PAYABLE_RECEIVABLE_TYPES and not l.reconciled
                )
                invoice_line = invoice.line_ids.filtered(
                    lambda l: l.account_id.account_type in PAYABLE_RECEIVABLE_TYPES and not l.reconciled
                )
                to_reconcile = payment_line.filtered(lambda l: l.account_id in invoice_line.account_id) + invoice_line
                if not to_reconcile:
                    failed.append(_("%s ↔ %s: already settled or no matching open account lines found.") % (payment.name or payment.id, invoice.name))
                    continue
                to_reconcile.reconcile()
                done += 1
            except Exception as e:
                failed.append(_("%s ↔ %s: %s") % (payment.name or payment.id, invoice.name, str(e)))

        reconciled_line_ids = [l.id for l in selected if l.payment_id and l.payment_id.is_reconciled]
        if reconciled_line_ids:
            self.match_line_ids = [(3, lid) for lid in reconciled_line_ids]

        msg = _("%d reconciled.") % done
        if failed:
            msg += " " + _("%d could not be reconciled:") % len(failed) + " " + " | ".join(failed[:5])
        self.result_message = msg
        return self._reopen_view()


class AccountPaymentReconciliationWizardLine(models.TransientModel):
    _name = 'account.payment.reconciliation.wizard.line'
    _description = 'Suggested Payment/Invoice Match'

    wizard_id = fields.Many2one('account.payment.reconciliation.wizard', ondelete='cascade')
    anchor_mode = fields.Selection(related='wizard_id.anchor_mode', readonly=True)

    payment_id = fields.Many2one(
        'account.payment', string='Payment',
        domain="[('id', 'in', candidate_payment_ids)] if candidate_payment_ids else "
               "[('is_reconciled', '=', False), ('state', 'not in', ('draft', 'cancelled', 'rejected'))]",
        help="In invoice-first mode, this is the side to change if the suggestion is wrong.")
    invoice_id = fields.Many2one(
        'account.move', string='Invoice/Bill',
        domain="[('id', 'in', candidate_invoice_ids)] if candidate_invoice_ids else "
               "[('state', '=', 'posted'), ('payment_state', 'in', ('not_paid', 'partial'))]",
        help="In payment-first mode, this is the side to change if the suggestion is wrong.")
    candidate_invoice_ids = fields.Many2many(
        'account.move', 'wizard_line_candidate_invoice_rel', string='Candidate Invoices/Bills',
        help="Internal: dropdown options for invoice_id (payment-first mode).")
    candidate_payment_ids = fields.Many2many(
        'account.payment', 'wizard_line_candidate_payment_rel', string='Candidate Payments',
        help="Internal: dropdown options for payment_id (invoice-first mode).")
    partner_name = fields.Char(string='Partner', readonly=True)
    currency_id = fields.Many2one(related='payment_id.currency_id', readonly=True)
    payment_amount = fields.Monetary(string='Payment Amount', currency_field='currency_id')
    invoice_residual = fields.Monetary(string='Invoice Due', currency_field='currency_id')
    confidence = fields.Selection([
        ('exact_ref', 'Exact Match (Amount + Reference)'),
        ('exact_amount', 'Amount Match Only'),
        ('reference_only', 'Reference Match Only (review)'),
        ('multiple_matches', 'Multiple Possible Matches (review)'),
        ('no_close_match', 'Nothing Close Found — Pick Manually (review)'),
        ('no_open_invoice', 'Nothing Open For This Partner — Pick Manually (review)'),
        ('claimed_elsewhere', 'Already Claimed in This Batch (review)'),
        ('manual', 'Manually Added'),
    ], string='Confidence', default='manual')
    to_reconcile = fields.Boolean(string='Confirm', default=False)

    @api.onchange('invoice_id')
    def _onchange_invoice_id(self):
        for line in self:
            if not line.invoice_id:
                continue
            invoice = line.invoice_id
            if line.anchor_mode != 'invoice':
                line.invoice_residual = abs(invoice.amount_residual)
                if line.confidence in ('multiple_matches', 'no_close_match', 'no_open_invoice', 'claimed_elsewhere'):
                    line.to_reconcile = True
            elif not line.partner_name:
                # Fresh manual row in invoice-first mode: build the payment dropdown.
                line.partner_name = invoice.partner_id.display_name
                line.invoice_residual = abs(invoice.amount_residual)
                partner_type = 'customer' if invoice.move_type in ('out_invoice', 'out_refund') else 'supplier'
                partner = invoice.partner_id.commercial_partner_id or invoice.partner_id
                candidates = self.env['account.payment'].search([
                    ('is_reconciled', '=', False),
                    ('state', 'not in', ('draft', 'cancelled', 'rejected')),
                    ('partner_type', '=', partner_type),
                    ('currency_id', '=', invoice.currency_id.id),
                    '|', ('partner_id', '=', partner.id), ('partner_id.commercial_partner_id', '=', partner.id),
                ])
                residual = abs(invoice.amount_residual)
                tolerance = residual * AMOUNT_TOLERANCE_RATIO
                close = candidates.filtered(lambda p: abs(abs(p.amount) - residual) <= tolerance)
                line.candidate_payment_ids = [(6, 0, (close or candidates).ids)]

    @api.onchange('payment_id')
    def _onchange_payment_id(self):
        for line in self:
            if not line.payment_id:
                continue
            payment = line.payment_id
            if line.anchor_mode == 'invoice':
                # Invoice is fixed; user is picking a different payment for it.
                line.payment_amount = payment.amount
                if line.confidence in ('multiple_matches', 'no_close_match', 'no_open_invoice', 'claimed_elsewhere'):
                    line.to_reconcile = True
            elif not line.partner_name:
                # Fresh manual row in payment-first mode: build the invoice dropdown.
                line.partner_name = payment.partner_id.display_name
                line.payment_amount = payment.amount
                move_types = MAP_PARTNER_TYPE_MOVE_TYPES.get(payment.partner_type, ())
                partner = payment.partner_id.commercial_partner_id or payment.partner_id
                candidates = self.env['account.move'].search([
                    ('move_type', 'in', move_types),
                    ('state', '=', 'posted'),
                    ('payment_state', 'in', ('not_paid', 'partial')),
                    ('currency_id', '=', payment.currency_id.id),
                    '|', ('partner_id', '=', partner.id), ('partner_id.commercial_partner_id', '=', partner.id),
                ])
                tolerance = abs(payment.amount) * AMOUNT_TOLERANCE_RATIO
                close = candidates.filtered(lambda m: abs(abs(m.amount_residual) - abs(payment.amount)) <= tolerance)
                line.candidate_invoice_ids = [(6, 0, (close or candidates).ids)]

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
