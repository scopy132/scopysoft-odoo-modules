# -*- coding: utf-8 -*-
import itertools
import json
import re

from odoo import models, fields, api, _
from odoo.exceptions import UserError

from ..models.log import CONFIDENCE_SELECTION
from ..models.rule import SETTLE_TYPES

PAYABLE_RECEIVABLE_TYPES = ('asset_receivable', 'liability_payable')
AMOUNT_TOLERANCE_RATIO = 0.3
MIN_REF_LENGTH = 4
EXCLUDED_PAYMENT_STATES = ('draft', 'canceled', 'rejected')
COMBO_MAX_SIZE = 3
COMBO_POOL_CAP = 30
COMBO_MAX_HITS = 5

# (partner_type, payment_type) -> document types that payment can really settle.
# A receipt settles an invoice, a payment sent to a customer settles a credit note, etc.
# Same-sign pairs (e.g. receipt vs customer credit note) can never reconcile.
PAYMENT_SIDE_TO_MOVE_TYPES = {
    ('customer', 'inbound'): ('out_invoice', 'out_receipt'),
    ('customer', 'outbound'): ('out_refund',),
    ('supplier', 'outbound'): ('in_invoice', 'in_receipt'),
    ('supplier', 'inbound'): ('in_refund',),
}
MOVE_TYPE_TO_PAYMENT_SIDE = {
    move_type: side
    for side, move_types in PAYMENT_SIDE_TO_MOVE_TYPES.items()
    for move_type in move_types
}
INVOICE_MOVE_TYPES = tuple(MOVE_TYPE_TO_PAYMENT_SIDE)


def _open_lines(move):
    """Receivable/payable lines of a journal entry that still have something to reconcile."""
    return move.line_ids.filtered(
        lambda l: l.account_id.account_type in PAYABLE_RECEIVABLE_TYPES and not l.reconciled
    )


def _payment_open_amount(payment):
    """What is still available on the payment (a partly used payment is not worth its full amount)."""
    if not payment.move_id:
        return 0.0
    field_name = 'amount_residual' if payment.currency_id == payment.company_id.currency_id else 'amount_residual_currency'
    return abs(sum(_open_lines(payment.move_id).mapped(field_name)))


def _payment_text(payment):
    return ' '.join(filter(None, [payment.memo, payment.payment_reference]))


def _matched_reference(invoice, text):
    """The invoice number/reference found in the memo (whole token, case-insensitive), or ''.
    Short tokens are ignored: they match almost any text."""
    if not text:
        return ''
    low = text.lower()
    for token in (invoice.name, invoice.ref, invoice.payment_reference):
        token = (token or '').strip()
        if len(token) < MIN_REF_LENGTH:
            continue
        if re.search(r'(?<!\w)' + re.escape(token.lower()) + r'(?!\w)', low):
            return token
    return ''


def _reference_in_text(invoice, text):
    return bool(_matched_reference(invoice, text))


def _reconcilable_lines(payment, invoice):
    """(payment_lines, invoice_lines) that can genuinely be reconciled together:
    same account, still open, opposite sign. Both empty if the pair cannot work."""
    p_lines = _open_lines(payment.move_id)
    i_lines = _open_lines(invoice)
    accounts = p_lines.account_id & i_lines.account_id
    p_lines = p_lines.filtered(lambda l: l.account_id in accounts)
    i_lines = i_lines.filtered(lambda l: l.account_id in accounts)
    empty = payment.env['account.move.line']
    if not p_lines or not i_lines:
        return empty, empty
    if sum(p_lines.mapped('amount_residual')) * sum(i_lines.mapped('amount_residual')) >= 0:
        return empty, empty
    return p_lines, i_lines


def _score_pair(pay_amount, currency, invoice, text):
    """Shared scoring for a (payment, invoice) pair, used by both anchor directions."""
    residual = abs(invoice.amount_residual)
    amt_match = currency.is_zero(residual - pay_amount)
    amt_close = amt_match or abs(residual - pay_amount) <= pay_amount * AMOUNT_TOLERANCE_RATIO
    ref_match = _reference_in_text(invoice, text)
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
        # Matches are not pre-computed here: the server actions create a REAL saved
        # record and call action_find_matches() on it.
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

    # ---------- eligibility ----------

    @api.model
    def _eligible_invoices(self, moves):
        """Posted, still (partly) unpaid real invoices/bills/credit notes/receipts. Journal entries are never eligible."""
        return moves.filtered(
            lambda m: m.move_type in INVOICE_MOVE_TYPES and m.state == 'posted'
            and m.payment_state in ('not_paid', 'partial')
        )

    @api.model
    def _eligible_payments(self, payments):
        """Posted payments that still have an open receivable/payable balance."""
        return payments.filtered(
            lambda p: p.state not in EXCLUDED_PAYMENT_STATES and not p.is_reconciled and p.move_id
            and not p.currency_id.is_zero(_payment_open_amount(p))
        )

    # ---------- candidate lookups, shared by both anchor directions ----------

    @api.model
    def _search_open_invoices(self, partner, move_types, currency, company):
        domain = [
            ('move_type', 'in', move_types),
            ('state', '=', 'posted'),
            ('payment_state', 'in', ('not_paid', 'partial')),
            ('currency_id', '=', currency.id),
            ('company_id', '=', company.id),
            '|', ('partner_id', '=', partner.id), ('partner_id.commercial_partner_id', '=', partner.id),
        ]
        return self.env['account.move'].search(domain)

    @api.model
    def _search_open_payments(self, partner, partner_type, payment_type, currency, company):
        domain = [
            ('is_reconciled', '=', False),
            ('state', 'not in', EXCLUDED_PAYMENT_STATES),
            ('partner_type', '=', partner_type),
            ('payment_type', '=', payment_type),
            ('currency_id', '=', currency.id),
            ('company_id', '=', company.id),
            '|', ('partner_id', '=', partner.id), ('partner_id.commercial_partner_id', '=', partner.id),
        ]
        return self._eligible_payments(self.env['account.payment'].search(domain))

    def _get_scoped_payments(self):
        """All eligible payments within the wizard's filters/scope, regardless of anchor."""
        self.ensure_one()
        if self.scope_payment_ids:
            payments = self.scope_payment_ids
        else:
            domain = [('is_reconciled', '=', False), ('state', 'not in', EXCLUDED_PAYMENT_STATES)]
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
        return self._eligible_payments(payments)

    def _get_scoped_invoices(self):
        """All eligible invoices/bills within the wizard's filters/scope, regardless of anchor."""
        self.ensure_one()
        if self.scope_invoice_ids:
            return self._eligible_invoices(self.scope_invoice_ids)
        domain = [
            ('move_type', 'in', INVOICE_MOVE_TYPES),
            ('state', '=', 'posted'),
            ('payment_state', 'in', ('not_paid', 'partial')),
        ]
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
            'payment_amount': _payment_open_amount(payment) if payment else abs(invoice.amount_residual),
            'invoice_residual': abs(invoice.amount_residual) if invoice else 0.0,
            'confidence': confidence,
            'to_reconcile': to_reconcile,
            'score': score,
            'amount_diff': amount_diff,
            'explanation': self._explain(confidence, payment, invoice, amount_diff),
        }

    def _fmt(self, amount, currency):
        places = getattr(currency, 'decimal_places', 2) or 0
        return "{:,.{p}f}".format(amount or 0.0, p=places)

    def _explain(self, confidence, payment, invoice, amount_diff):
        """One plain sentence saying WHY a row was suggested (shown in the wizard and kept in the log)."""
        currency = (payment or invoice).currency_id if (payment or invoice) else self.env.company.currency_id
        ref = _matched_reference(invoice, _payment_text(payment)) if (payment and invoice) else ''
        diff = self._fmt(amount_diff, currency)
        if confidence == 'exact_ref':
            return _("Invoice reference %s was found in the payment memo and the amount equals the amount due.") % ref
        if confidence == 'exact_amount':
            return _("The payment amount equals the amount due. No invoice reference was found in the memo, so check it is the right invoice.")
        if confidence == 'reference_only':
            return _("Invoice reference %(ref)s was found in the memo, but the amount differs from the amount due by %(diff)s.") % {
                'ref': ref, 'diff': diff}
        if confidence == 'multiple_matches':
            return _("Several invoices fit this payment equally well. Pick the right one in the Invoice/Bill column.")
        if confidence == 'no_close_match':
            return _("No invoice has a matching reference or amount. The closest one is shown (difference %s).") % diff
        if confidence == 'no_open_invoice':
            return _("No open invoice/bill of the right type, currency and company exists for this partner.")
        return ''

    def _compute_matches_payment_anchor(self):
        """Payment is the fixed fact; find which invoice/bill it should settle."""
        raw_rows = []
        scoped_invoices = self._eligible_invoices(self.scope_invoice_ids) if self.scope_invoice_ids else False
        for payment in self._get_scoped_payments():
            move_types = PAYMENT_SIDE_TO_MOVE_TYPES.get((payment.partner_type, payment.payment_type), ())
            if not move_types or not payment.partner_id:
                continue
            pay_amount = _payment_open_amount(payment)
            partner = payment.partner_id.commercial_partner_id or payment.partner_id
            if scoped_invoices:
                candidates = scoped_invoices.filtered(
                    lambda m: m.move_type in move_types and m.currency_id == payment.currency_id
                    and m.company_id == payment.company_id
                    and (m.partner_id.commercial_partner_id or m.partner_id) == partner
                )
            else:
                candidates = self._search_open_invoices(partner, move_types, payment.currency_id, payment.company_id)
            # Only keep documents that can truly be reconciled with this payment
            # (same account, open, opposite sign).
            candidates = candidates.filtered(lambda m: _reconcilable_lines(payment, m)[0])
            if not candidates:
                raw_rows.append(self._row_dict(
                    payment, False, [], [payment.id],
                    'no_open_invoice', False, 0, None,
                ))
                continue
            text = _payment_text(payment)

            close_candidates = self.env['account.move']
            scored = []
            for inv in candidates:
                score, amt_match, ref_match, amt_close, residual = _score_pair(pay_amount, payment.currency_id, inv, text)
                if amt_close or ref_match:
                    close_candidates |= inv
                if score:
                    scored.append((score, inv, amt_match, ref_match, residual))

            if not scored:
                fallback_pool = close_candidates or candidates
                closest = min(fallback_pool, key=lambda m: abs(abs(m.amount_residual) - pay_amount))
                raw_rows.append(self._row_dict(
                    payment, closest, fallback_pool.ids, [payment.id],
                    'no_close_match', False, 0,
                    abs(abs(closest.amount_residual) - pay_amount),
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
                    abs(abs(default_inv.amount_residual) - pay_amount),
                ))
                continue

            score, inv, amt_match, ref_match, residual = top[0]
            confidence = 'exact_ref' if (amt_match and ref_match) else ('exact_amount' if amt_match else 'reference_only')
            raw_rows.append(self._row_dict(
                payment, inv, candidate_invoice_ids, [payment.id],
                confidence, confidence in ('exact_ref', 'exact_amount'), score,
                abs(residual - pay_amount),
            ))

        self._resolve_collisions(raw_rows, key='invoice_id')
        return raw_rows

    def _compute_matches_invoice_anchor(self):
        """Invoice/bill is the fixed fact; find which payment already covers it."""
        raw_rows = []
        scoped_payments = self._eligible_payments(self.scope_payment_ids) if self.scope_payment_ids else False
        for invoice in self._get_scoped_invoices():
            side = MOVE_TYPE_TO_PAYMENT_SIDE.get(invoice.move_type)
            if not side or not invoice.partner_id:
                continue
            partner_type, payment_type = side
            partner = invoice.partner_id.commercial_partner_id or invoice.partner_id
            if scoped_payments:
                candidates = scoped_payments.filtered(
                    lambda p: p.partner_type == partner_type and p.payment_type == payment_type
                    and p.currency_id == invoice.currency_id and p.company_id == invoice.company_id
                    and (p.partner_id.commercial_partner_id or p.partner_id) == partner
                )
            else:
                candidates = self._search_open_payments(partner, partner_type, payment_type, invoice.currency_id, invoice.company_id)
            candidates = candidates.filtered(lambda p: _reconcilable_lines(p, invoice)[0])
            if not candidates:
                raw_rows.append(self._row_dict(
                    False, invoice, [invoice.id], [],
                    'no_open_invoice', False, 0, None,
                ))
                continue
            residual = abs(invoice.amount_residual)

            close_candidates = self.env['account.payment']
            scored = []
            for pay in candidates:
                pay_amount = _payment_open_amount(pay)
                score, amt_match, ref_match, amt_close, _residual = _score_pair(pay_amount, pay.currency_id, invoice, _payment_text(pay))
                if amt_close or ref_match:
                    close_candidates |= pay
                if score:
                    scored.append((score, pay, amt_match, ref_match))

            if not scored:
                fallback_pool = close_candidates or candidates
                closest = min(fallback_pool, key=lambda p: abs(_payment_open_amount(p) - residual))
                raw_rows.append(self._row_dict(
                    closest, invoice, [invoice.id], fallback_pool.ids,
                    'no_close_match', False, 0,
                    abs(_payment_open_amount(closest) - residual),
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
                    abs(_payment_open_amount(default_pay) - residual),
                ))
                continue

            score, pay, amt_match, ref_match = top[0]
            confidence = 'exact_ref' if (amt_match and ref_match) else ('exact_amount' if amt_match else 'reference_only')
            raw_rows.append(self._row_dict(
                pay, invoice, [invoice.id], candidate_payment_ids,
                confidence, confidence in ('exact_ref', 'exact_amount'), score,
                abs(_payment_open_amount(pay) - residual),
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
                loser['explanation'] = _("Another row in this batch already reconciles this invoice/payment. Review before ticking.")

    def _compute_matches(self):
        raw_rows = self._compute_matches_invoice_anchor() if self.anchor_mode == 'invoice' else self._compute_matches_payment_anchor()
        raw_rows = self._extend_rows(raw_rows)
        skip = ('score', 'amount_diff', 'candidate_invoice_ids', 'candidate_payment_ids', 'payment_ids')
        commands = []
        for row in raw_rows:
            vals = {k: v for k, v in row.items() if k not in skip}
            vals['candidate_invoice_ids'] = [(6, 0, row['candidate_invoice_ids'])]
            vals['candidate_payment_ids'] = [(6, 0, row['candidate_payment_ids'])]
            vals['payment_ids'] = [(6, 0, row.get('payment_ids') or [])]
            commands.append((0, 0, vals))
        return commands

    # ---------- split / combination / adjustment matching ----------

    def _candidate_invoices(self, partner, move_types, currency, company, scoped_invoices):
        if scoped_invoices:
            return scoped_invoices.filtered(
                lambda m: m.move_type in move_types and m.currency_id == currency and m.company_id == company
                and (m.partner_id.commercial_partner_id or m.partner_id) == partner)
        return self._search_open_invoices(partner, move_types, currency, company)

    def _group_row(self, group, invoice, confidence, tick, currency, hits=1):
        total = sum(_payment_open_amount(p) for p in group)
        residual = abs(invoice.amount_residual)
        parts = " + ".join("%s %s" % (p.name or p.id, self._fmt(_payment_open_amount(p), currency)) for p in group)
        breakdown = _("%(parts)s = %(total)s of %(due)s due") % {
            'parts': parts, 'total': self._fmt(total, currency), 'due': self._fmt(residual, currency)}
        n = len(group)
        if confidence == 'split_exact':
            why = _("%(n)d payments from the same partner mention %(inv)s and add up exactly to the amount due.") % {'n': n, 'inv': invoice.name}
        elif confidence == 'split_partial':
            breakdown += " - " + _("%s remaining") % self._fmt(residual - total, currency)
            why = _("%(n)d payments mention %(inv)s but add up to less than the amount due. Tick to reconcile what is there and leave the invoice partially paid.") % {'n': n, 'inv': invoice.name}
        elif confidence == 'split_over':
            breakdown += " - " + _("%s over") % self._fmt(total - residual, currency)
            why = _("%(n)d payments mention %(inv)s and add up to MORE than the amount due. The extra may be an overpayment or a wrong reference - check before ticking.") % {'n': n, 'inv': invoice.name}
        elif confidence == 'combo_amount':
            why = _("No reference was found. These %d payments are the only combination of this partner's payments that adds up exactly to the amount due. Low confidence - verify before ticking.") % n
        else:
            why = _("No reference was found and %d different payment combinations add up to the amount due. This is one of them - choose the right payments yourself.") % hits
        return {
            'payment_id': False, 'payment_ids': group.ids, 'invoice_id': invoice.id,
            'candidate_invoice_ids': [invoice.id], 'candidate_payment_ids': group.ids,
            'partner_name': invoice.partner_id.display_name,
            'payment_amount': total, 'invoice_residual': residual,
            'confidence': confidence, 'to_reconcile': tick, 'score': 9,
            'amount_diff': abs(total - residual), 'explanation': why, 'breakdown': breakdown,
        }

    def _adjust_row(self, payment, invoice, rule, amount, tick, ref_ok, currency):
        paid = _payment_open_amount(payment)
        residual = abs(invoice.amount_residual)
        label = rule._label()
        breakdown = _("Paid %(paid)s + %(label)s %(adj)s = %(due)s due") % {
            'paid': self._fmt(paid, currency), 'label': label,
            'adj': self._fmt(amount, currency), 'due': self._fmt(residual, currency)}
        if ref_ok:
            why = _("Invoice reference found. The payment is short by %(adj)s, which is exactly the %(label)s defined by rule \"%(rule)s\".") % {
                'adj': self._fmt(amount, currency), 'label': label, 'rule': rule.name}
        else:
            why = _("No reference found, but the shortfall of %(adj)s is exactly the %(label)s defined by rule \"%(rule)s\". Verify before ticking.") % {
                'adj': self._fmt(amount, currency), 'label': label, 'rule': rule.name}
        return {
            'payment_id': payment.id, 'payment_ids': [], 'invoice_id': invoice.id,
            'candidate_invoice_ids': [invoice.id], 'candidate_payment_ids': [payment.id],
            'partner_name': payment.partner_id.display_name,
            'payment_amount': paid, 'invoice_residual': residual,
            'confidence': 'adjusted', 'to_reconcile': tick, 'score': 8, 'amount_diff': amount,
            'explanation': why, 'breakdown': breakdown, 'rule_id': rule.id, 'adjustment_amount': amount,
        }

    def _extend_rows(self, rows):
        """Second pass on payments that no 1-to-1 row has claimed with confidence:
        (1) several payments that quote the same invoice, (2) a payment short by a known WHT / bank
        charge / tolerance, (3) payments without reference that add up to one invoice.
        Order matters: stronger evidence claims payments first, so one payment never sits in two rows."""
        Rule = self.env['payment.recon.rule']
        payments = self._get_scoped_payments()
        scoped_invoices = self._eligible_invoices(self.scope_invoice_ids) if self.scope_invoice_ids else False
        claimed_pay = {r['payment_id'] for r in rows if r['to_reconcile'] and r['payment_id']}
        claimed_inv = {r['invoice_id'] for r in rows if r['to_reconcile'] and r['invoice_id']}
        buckets = {}
        for p in payments:
            if p.id in claimed_pay or not p.partner_id:
                continue
            side = (p.partner_type, p.payment_type)
            if side not in PAYMENT_SIDE_TO_MOVE_TYPES:
                continue
            partner = p.partner_id.commercial_partner_id or p.partner_id
            key = (partner.id, side, p.currency_id.id, p.company_id.id)
            buckets[key] = buckets.get(key, self.env['account.payment']) | p

        new_rows, used_pay, new_inv = [], set(), set()
        for (partner_id, side, currency_id, company_id), pays in buckets.items():
            partner = self.env['res.partner'].browse(partner_id)
            currency = self.env['res.currency'].browse(currency_id)
            company = self.env['res.company'].browse(company_id)
            invoices = self._candidate_invoices(
                partner, PAYMENT_SIDE_TO_MOVE_TYPES[side], currency, company, scoped_invoices)
            invoices = invoices.filtered(lambda m: m.id not in claimed_inv)
            if not invoices:
                continue
            cfg = self.env['payment.recon.config']._get(company)
            refs = {p.id: invoices.filtered(lambda m, t=_payment_text(p): _reference_in_text(m, t)) for p in pays}

            # 1) several payments quoting the same invoice (a payment quoting two invoices is never grouped)
            for inv in invoices:
                group = pays.filtered(
                    lambda p: p.id not in used_pay and refs[p.id] == inv and _reconcilable_lines(p, inv)[0])
                if len(group) < 2:
                    continue
                total = sum(_payment_open_amount(p) for p in group)
                residual = abs(inv.amount_residual)
                if currency.is_zero(total - residual):
                    conf, tick = 'split_exact', True
                elif total < residual:
                    conf, tick = 'split_partial', False
                else:
                    conf, tick = 'split_over', False
                new_rows.append(self._group_row(group, inv, conf, tick, currency))
                used_pay |= set(group.ids)
                new_inv.add(inv.id)

            # 2) payment short by an exactly-known WHT / bank charge / tolerance
            rules = Rule.search([('company_id', '=', company.id)])
            if rules and currency == company.currency_id:
                for p in pays:
                    if p.id in used_pay:
                        continue
                    amount = _payment_open_amount(p)
                    hits = []
                    for inv in invoices:
                        if inv.id in new_inv or not _reconcilable_lines(p, inv)[0]:
                            continue
                        ref_ok = inv in refs[p.id]
                        diff = abs(inv.amount_residual) - amount
                        for rule in rules:
                            adj = rule._adjustment_for(inv, diff, ref_ok)
                            if adj:
                                hits.append((inv, rule, adj, ref_ok))
                                break
                    if len(hits) != 1:
                        continue
                    inv, rule, adj, ref_ok = hits[0]
                    new_rows.append(self._adjust_row(p, inv, rule, adj, bool(rule.auto_tick and ref_ok), ref_ok, currency))
                    used_pay.add(p.id)
                    new_inv.add(inv.id)

            # 3) payments with no reference that add up exactly to one invoice (2-3 payments, low confidence)
            if cfg.combo_enabled:
                step = currency.rounding or 0.01
                cents = lambda a: int(round(a / step))
                for inv in invoices.sorted(lambda m: -abs(m.amount_residual)):
                    if inv.id in new_inv:
                        continue
                    inv_date = inv.invoice_date or inv.date
                    pool = pays.filtered(
                        lambda p: p.id not in used_pay and not refs[p.id] and _reconcilable_lines(p, inv)[0]
                        and abs((p.date - inv_date).days) <= cfg.combo_window_days)
                    if len(pool) < 2:
                        continue
                    pool = pool.sorted(lambda p: abs((p.date - inv_date).days))[:COMBO_POOL_CAP]
                    amounts = {p.id: cents(_payment_open_amount(p)) for p in pool}
                    target = cents(abs(inv.amount_residual))
                    found = []
                    for size in range(2, COMBO_MAX_SIZE + 1):
                        for combo in itertools.combinations(pool, size):
                            if sum(amounts[p.id] for p in combo) == target:
                                found.append(combo)
                                if len(found) > COMBO_MAX_HITS:
                                    break
                        if len(found) > COMBO_MAX_HITS:
                            break
                    if not found:
                        continue
                    group = pool.browse([p.id for p in found[0]])
                    conf = 'combo_amount' if len(found) == 1 else 'combo_ambiguous'
                    new_rows.append(self._group_row(group, inv, conf, False, currency, hits=len(found)))
                    used_pay |= set(group.ids)
                    new_inv.add(inv.id)

        if not new_rows:
            return rows
        if self.anchor_mode == 'invoice':
            rows = [r for r in rows if r['to_reconcile'] or r['invoice_id'] not in new_inv]
        else:
            rows = [r for r in rows if r['to_reconcile'] or not r['payment_id'] or r['payment_id'] not in used_pay]
        return rows + new_rows

    REVIEW_CONFIDENCES = ('multiple_matches', 'no_close_match', 'no_open_invoice', 'claimed_elsewhere',
                          'split_partial', 'split_over', 'combo_amount', 'combo_ambiguous')

    def _skipped_scope_note(self):
        """Selections that were dropped before scanning even started, so the count
        discrepancy isn't a silent mystery — e.g. 'picked 3 payments, only 1 row appears'."""
        self.ensure_one()
        if self.anchor_mode == 'payment' and self.scope_payment_ids:
            skipped = len(self.scope_payment_ids) - len(self._get_scoped_payments())
            if skipped:
                return " " + _("%d of your %d selected payment(s) are already reconciled, not posted, or have nothing left to match, and were skipped.") % (
                    skipped, len(self.scope_payment_ids))
        elif self.anchor_mode == 'invoice' and self.scope_invoice_ids:
            skipped = len(self.scope_invoice_ids) - len(self._eligible_invoices(self.scope_invoice_ids))
            if skipped:
                return " " + _("%d of your %d selected document(s) are already settled, not posted, or not an invoice/bill/credit note, and were skipped.") % (
                    skipped, len(self.scope_invoice_ids))
        return ""

    def action_find_matches(self):
        self.ensure_one()
        self.match_line_ids = [(5, 0, 0)]
        skipped_note = self._skipped_scope_note()
        lines = self._compute_matches()
        self.match_line_ids = lines
        if not lines:
            self.result_message = self._empty_result_message() + skipped_note
        elif all(l[2].get('confidence') == 'no_open_invoice' for l in lines):
            if self.anchor_mode == 'invoice':
                msg = _("No unreconciled payment was found for the selected invoice(s)/bill(s). This module matches payments only: credit notes and journal entries are not payments (use Odoo's Outstanding credits for those).")
            else:
                msg = _("No open invoice/bill of the right type, currency and company was found for the payment(s) listed below.")
            self.result_message = msg + skipped_note
        else:
            needs_review = len([1 for l in lines if l[2].get('confidence') in self.REVIEW_CONFIDENCES])
            msg = _("%d row(s) found.") % len(lines)
            if needs_review:
                msg += " " + _("%d need your review — pick the match yourself.") % needs_review
            self.result_message = msg + skipped_note
        return self._reopen_view()

    def _empty_result_message(self):
        """Say WHY nothing was found instead of a bare 'nothing found'."""
        self.ensure_one()
        if self.anchor_mode == 'invoice':
            return _("None of the selected documents is a posted, unpaid invoice/bill. This module matches payments to invoices and bills; credit notes and journal entries are not payments.")
        if not self._get_scoped_payments():
            return _("No posted, unreconciled payment was found for the current scope/filters. This module matches payments to invoices and bills; credit notes and journal entries are not payments (use Odoo's Outstanding credits for those).")
        return _("Nothing found for the current scope/filters.")

    def _check_pair(self, payment, invoice):
        """Raise a UserError explaining why this payment cannot settle this document."""
        if payment.state in EXCLUDED_PAYMENT_STATES or not payment.move_id:
            raise UserError(_("The payment is not posted."))
        if invoice.state != 'posted' or invoice.move_type not in INVOICE_MOVE_TYPES:
            raise UserError(_("The document is not a posted invoice, bill, credit note or receipt."))
        if payment.company_id != invoice.company_id:
            raise UserError(_("The payment and the document belong to different companies."))
        if (payment.partner_id.commercial_partner_id or payment.partner_id) != (invoice.partner_id.commercial_partner_id or invoice.partner_id):
            raise UserError(_("The payment and the document belong to different partners."))
        if payment.currency_id != invoice.currency_id:
            raise UserError(_("The payment and the document are in different currencies."))
        if invoice.move_type not in PAYMENT_SIDE_TO_MOVE_TYPES.get((payment.partner_type, payment.payment_type), ()):
            raise UserError(_("This kind of payment cannot settle this kind of document (e.g. a receipt against a credit note)."))

    def _create_adjustment_move(self, invoice, rule, amount, invoice_lines):
        """Journal entry that books the known difference (WHT, bank charge, write-off) so the invoice can be
        settled in full. Returns (move, the line on the receivable/payable account to reconcile)."""
        account = invoice_lines[:1].account_id
        is_sale = invoice.move_type in ('out_invoice', 'out_receipt')
        partner = invoice.commercial_partner_id or invoice.partner_id
        label = _("Smart Reconcile - %(label)s on %(inv)s") % {'label': rule._label(), 'inv': invoice.name}
        move = self.env['account.move'].create({
            'move_type': 'entry',
            'journal_id': rule.journal_id.id,
            'company_id': invoice.company_id.id,
            'date': fields.Date.context_today(self),
            'ref': label,
            'line_ids': [
                (0, 0, {'name': label, 'account_id': account.id, 'partner_id': partner.id,
                        'debit': 0.0 if is_sale else amount, 'credit': amount if is_sale else 0.0}),
                (0, 0, {'name': label, 'account_id': rule.account_id.id, 'partner_id': partner.id,
                        'debit': amount if is_sale else 0.0, 'credit': 0.0 if is_sale else amount}),
            ],
        })
        move.action_post()
        return move, move.line_ids.filtered(lambda l: l.account_id == account)

    def _reconcile_group(self, payments, invoice, line=None, source='manual'):
        """Reconcile one or several payments with one invoice/bill (plus an adjustment entry when the row
        carries a rule). Must be called inside a savepoint. Records the audit log entry."""
        Line = self.env['account.move.line']
        pay_lines, inv_lines = Line, Line
        for payment in payments:
            self._check_pair(payment, invoice)
            p_lines, i_lines = _reconcilable_lines(payment, invoice)
            if not p_lines or not i_lines:
                raise UserError(_("Already settled, or no matching open receivable/payable lines found."))
            pay_lines |= p_lines
            inv_lines = i_lines
        currency = invoice.currency_id
        rule = line.rule_id if (line and line.confidence == 'adjusted') else self.env['payment.recon.rule']
        adj_move, adj_lines, adj_amount = self.env['account.move'], Line, 0.0
        if rule:
            adj_amount = line.adjustment_amount
            diff = abs(invoice.amount_residual) - sum(_payment_open_amount(p) for p in payments)
            if not currency.is_zero(diff - adj_amount):
                raise UserError(_("The amounts changed since this match was found. Click Find Matches again."))
            adj_move, adj_lines = self._create_adjustment_move(invoice, rule, adj_amount, inv_lines)
        lines = pay_lines + inv_lines + adj_lines
        before = sum(abs(l.amount_residual) for l in lines)
        partials_before = set((lines.matched_debit_ids | lines.matched_credit_ids).ids)
        lines.reconcile()
        lines.invalidate_recordset(['amount_residual', 'amount_residual_currency', 'reconciled',
                                    'matched_debit_ids', 'matched_credit_ids'])
        after = sum(abs(l.amount_residual) for l in lines)
        if invoice.company_currency_id.is_zero(before - after):
            raise UserError(_("Nothing was reconciled."))
        created = sorted(set((lines.matched_debit_ids | lines.matched_credit_ids).ids) - partials_before)
        amount = sum(self.env['account.partial.reconcile'].browse(created).mapped('amount')) - adj_amount
        self.env['payment.recon.log'].create({
            'company_id': invoice.company_id.id,
            'partner_id': (invoice.commercial_partner_id or invoice.partner_id).id,
            'invoice_id': invoice.id,
            'payment_ids': [(6, 0, payments.ids)],
            'amount': amount,
            'adjustment_amount': adj_amount,
            'rule_id': rule.id or False,
            'adjustment_move_id': adj_move.id or False,
            'confidence': line.confidence if line else 'manual',
            'source': source,
            'explanation': (line.explanation if line else '') or '',
            'breakdown': (line.breakdown if line else '') or '',
            'partial_ids_json': json.dumps(created),
        })

    def _reconcile_lines(self, lines, source='manual'):
        """Reconcile the given wizard rows, one savepoint per row. Returns (done_count, failed_messages)."""
        done, failed = 0, []
        succeeded = self.env['account.payment.reconciliation.wizard.line']
        for line in lines:
            payments = line.payment_ids or line.payment_id
            invoice = line.invoice_id
            if not payments or not invoice:
                failed.append(_("A row is missing a payment or an invoice/bill - skipped."))
                continue
            label = "%s <-> %s" % (", ".join(payments.mapped(lambda p: p.name or str(p.id))), invoice.name)
            try:
                with self.env.cr.savepoint():
                    self._reconcile_group(payments, invoice, line, source)
                done += 1
                succeeded |= line
            except Exception as e:
                failed.append("%s: %s" % (label, str(e)))
        if succeeded:
            self.match_line_ids = [(3, lid) for lid in succeeded.ids]
        return done, failed

    def action_reconcile_confirmed(self):
        self.ensure_one()
        selected = self.match_line_ids.filtered(lambda l: l.to_reconcile)
        if not selected:
            raise UserError(_("Tick at least one match before reconciling (or add a manual row)."))
        done, failed = self._reconcile_lines(selected, source='manual')
        msg = _("%d reconciled.") % done
        if done:
            msg += " " + _("Every reconciliation is in the Reconciliation Log, where it can be undone with one click.")
            msg += " " + _("Click Find Matches again to refresh what is still open.")
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
               "[('is_reconciled', '=', False), ('state', 'not in', ('draft', 'canceled', 'rejected'))]",
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
    currency_id = fields.Many2one('res.currency', compute='_compute_currency_id')
    payment_amount = fields.Monetary(string='Payment Amount (open)', currency_field='currency_id')
    invoice_residual = fields.Monetary(string='Invoice Due', currency_field='currency_id')
    confidence = fields.Selection(CONFIDENCE_SELECTION, string='Confidence', default='manual')
    payment_ids = fields.Many2many(
        'account.payment', 'wizard_line_payments_rel', string='Payments',
        help="Used when several payments settle one invoice (split or combination rows).")
    explanation = fields.Char(string='Why', readonly=True)
    breakdown = fields.Char(string='Payments Breakdown', readonly=True)
    rule_id = fields.Many2one('payment.recon.rule', string='Adjustment Rule', readonly=True)
    adjustment_amount = fields.Monetary(string='Adjustment', currency_field='currency_id', readonly=True)
    to_reconcile = fields.Boolean(string='Confirm', default=False)

    @api.depends('payment_id.currency_id', 'payment_ids.currency_id', 'invoice_id.currency_id')
    def _compute_currency_id(self):
        for line in self:
            line.currency_id = (line.payment_id.currency_id or line.payment_ids[:1].currency_id
                                or line.invoice_id.currency_id or self.env.company.currency_id)

    @api.onchange('invoice_id')
    def _onchange_invoice_id(self):
        wizard_model = self.env['account.payment.reconciliation.wizard']
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
                side = MOVE_TYPE_TO_PAYMENT_SIDE.get(invoice.move_type)
                if not side:
                    line.candidate_payment_ids = [(5, 0, 0)]
                    continue
                partner = invoice.partner_id.commercial_partner_id or invoice.partner_id
                candidates = wizard_model._search_open_payments(partner, side[0], side[1], invoice.currency_id, invoice.company_id)
                residual = abs(invoice.amount_residual)
                tolerance = residual * AMOUNT_TOLERANCE_RATIO
                close = candidates.filtered(lambda p: abs(_payment_open_amount(p) - residual) <= tolerance)
                line.candidate_payment_ids = [(6, 0, (close or candidates).ids)]

    @api.onchange('payment_id')
    def _onchange_payment_id(self):
        wizard_model = self.env['account.payment.reconciliation.wizard']
        for line in self:
            if not line.payment_id:
                continue
            payment = line.payment_id
            pay_amount = _payment_open_amount(payment)
            if line.anchor_mode == 'invoice':
                # Invoice is fixed; user is picking a different payment for it.
                line.payment_amount = pay_amount
                if line.confidence in ('multiple_matches', 'no_close_match', 'no_open_invoice', 'claimed_elsewhere'):
                    line.to_reconcile = True
            elif not line.partner_name:
                # Fresh manual row in payment-first mode: build the invoice dropdown.
                line.partner_name = payment.partner_id.display_name
                line.payment_amount = pay_amount
                move_types = PAYMENT_SIDE_TO_MOVE_TYPES.get((payment.partner_type, payment.payment_type), ())
                partner = payment.partner_id.commercial_partner_id or payment.partner_id
                candidates = wizard_model._search_open_invoices(partner, move_types, payment.currency_id, payment.company_id)
                tolerance = pay_amount * AMOUNT_TOLERANCE_RATIO
                close = candidates.filtered(lambda m: abs(abs(m.amount_residual) - pay_amount) <= tolerance)
                line.candidate_invoice_ids = [(6, 0, (close or candidates).ids)]

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
