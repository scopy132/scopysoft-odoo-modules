# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError
from datetime import date, timedelta


class CustomerStatementWizard(models.TransientModel):
    _name = 'customer.statement.wizard'
    _description = 'Customer Statement Wizard'

    partner_id = fields.Many2one(
        'res.partner',
        string='Customer',
        required=True
    )
    date_from = fields.Date(
        string='From Date',
        required=True,
        default=lambda self: date.today().replace(day=1)
    )
    date_to = fields.Date(
        string='To Date',
        required=True,
        default=fields.Date.today
    )
    filter_type = fields.Selection([
        ('all', 'All Invoices & Payments'),
        ('unpaid', 'Unpaid / Partially Paid Only'),
        ('paid', 'Paid Invoices Only'),
    ], string='Show', required=True, default='all')

    date_range = fields.Selection([
        ('this_month', 'This Month'),
        ('last_month', 'Last Month'),
        ('last_3_months', 'Last 3 Months'),
        ('last_6_months', 'Last 6 Months'),
        ('this_year', 'This Year'),
        ('custom', 'Custom Range'),
    ], string='Date Range', default='this_month')

    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company
    )

    @api.onchange('date_range')
    def _onchange_date_range(self):
        today = date.today()
        if self.date_range == 'this_month':
            self.date_from = today.replace(day=1)
            self.date_to = today
        elif self.date_range == 'last_month':
            first_day = today.replace(day=1)
            last_month_end = first_day - timedelta(days=1)
            self.date_from = last_month_end.replace(day=1)
            self.date_to = last_month_end
        elif self.date_range == 'last_3_months':
            self.date_from = (today - timedelta(days=90)).replace(day=1)
            self.date_to = today
        elif self.date_range == 'last_6_months':
            self.date_from = (today - timedelta(days=180)).replace(day=1)
            self.date_to = today
        elif self.date_range == 'this_year':
            self.date_from = today.replace(month=1, day=1)
            self.date_to = today

    def _get_invoices(self):
        domain = [
            ('partner_id', 'child_of', self.partner_id.id),
            ('move_type', 'in', ['out_invoice', 'out_refund']),
            ('state', '=', 'posted'),
            ('invoice_date', '>=', self.date_from),
            ('invoice_date', '<=', self.date_to),
            ('company_id', '=', self.company_id.id),
        ]
        if self.filter_type == 'unpaid':
            domain.append(('payment_state', 'in', ['not_paid', 'partial']))
        elif self.filter_type == 'paid':
            domain.append(('payment_state', '=', 'paid'))
        return self.env['account.move'].search(domain, order='invoice_date asc')

    def _get_payments(self):
        if self.filter_type == 'paid':
            return self.env['account.payment'].browse([])
        domain = [
            ('partner_id', 'child_of', self.partner_id.id),
            ('payment_type', '=', 'inbound'),
            ('state', '=', 'posted'),
            ('date', '>=', self.date_from),
            ('date', '<=', self.date_to),
            ('company_id', '=', self.company_id.id),
        ]
        return self.env['account.payment'].search(domain, order='date asc')

    def _build_statement_lines(self, invoices, payments):
        lines = []
        for inv in invoices:
            lines.append({
                'date': inv.invoice_date,
                'type': 'invoice',
                'ref': inv.name,
                'description': inv.invoice_origin or inv.ref or '',
                'move_type': inv.move_type,
                'amount_total': inv.amount_total if inv.move_type == 'out_invoice' else -inv.amount_total,
                'amount_residual': inv.amount_residual if inv.move_type == 'out_invoice' else -inv.amount_residual,
                'payment_state': inv.payment_state,
                'currency': inv.currency_id,
            })
        for pay in payments:
            lines.append({
                'date': pay.date,
                'type': 'payment',
                'ref': pay.name,
                'description': pay.ref or '',
                'move_type': False,
                'amount_total': -pay.amount,
                'amount_residual': 0,
                'payment_state': 'paid',
                'currency': pay.currency_id,
            })
        lines.sort(key=lambda x: x['date'])
        balance = 0.0
        for line in lines:
            balance += line['amount_total']
            line['balance'] = balance
        return lines

    def action_print_statement(self):
        self.ensure_one()
        invoices = self._get_invoices()
        payments = self._get_payments()
        if not invoices and not payments:
            raise UserError(_('No records found for the selected period and filter.'))
        return self.env.ref(
            'scopysoft_customer_statement.action_report_customer_statement'
        ).report_action(self)

    def get_statement_data(self):
        self.ensure_one()
        invoices = self._get_invoices()
        payments = self._get_payments()
        lines = self._build_statement_lines(invoices, payments)
        total_invoiced = sum(l['amount_total'] for l in lines if l['type'] == 'invoice')
        total_paid = sum(abs(l['amount_total']) for l in lines if l['type'] == 'payment')
        closing_balance = lines[-1]['balance'] if lines else 0.0
        return {
            'partner': self.partner_id,
            'company': self.company_id,
            'date_from': self.date_from,
            'date_to': self.date_to,
            'filter_type': self.filter_type,
            'filter_label': dict(self._fields['filter_type'].selection)[self.filter_type],
            'lines': lines,
            'total_invoiced': total_invoiced,
            'total_paid': total_paid,
            'closing_balance': closing_balance,
            'currency': self.company_id.currency_id,
        }
