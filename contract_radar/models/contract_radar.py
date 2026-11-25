# -*- coding: utf-8 -*-
import logging
from datetime import date, datetime, time, timedelta

import pytz
from dateutil.relativedelta import relativedelta
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.misc import format_amount, format_date

_logger = logging.getLogger(__name__)

CONTRACT_TYPES = [
    ('vendor', 'Vendor'),
    ('customer', 'Customer'),
    ('lease', 'Lease'),
    ('warranty', 'Warranty'),
    ('license', 'License'),
    ('nda', 'NDA'),
    ('service', 'Service'),
]
STATES = [
    ('draft', 'Draft'),
    ('active', 'Active'),
    ('expiring', 'Expiring'),
    ('renewed', 'Renewed'),
    ('cancelled', 'Cancelled'),
    ('expired', 'Expired'),
]
LIVE_STATES = ('active', 'expiring')
BILLING_PERIODS = [
    ('monthly', 'Monthly'),
    ('quarterly', 'Quarterly'),
    ('semiannual', 'Every 6 Months'),
    ('yearly', 'Yearly'),
    ('one_time', 'One-time'),
]
PERIOD_MONTHS = {'monthly': 1, 'quarterly': 3, 'semiannual': 6, 'yearly': 12, 'one_time': 0}
EXPIRING_WINDOW_DAYS = 90
HORIZON_MONTHS = 36
MAX_COST_LINES = 240
ACTIVITY_PREFIX = 'Contract Radar: '
APPROVAL_PREFIX = 'Contract approval: '
DECISIONS = [
    ('renew', 'Renew'),
    ('renegotiate', 'Renegotiate'),
    ('cancel', 'Cancel / Do Not Renew'),
]
DECISION_STATES = [
    ('none', 'No Decision'),
    ('pending', 'Waiting for Approval'),
    ('approved', 'Approved'),
    ('rejected', 'Rejected'),
]
MAX_WORKDAY_STEPS = 14

# Changing any of these fields re-projects the payment schedule.
REBUILD_FIELDS = {
    'amount', 'billing_period', 'date_start', 'date_end', 'auto_renew', 'notice_sent_date', 'state',
    'escalation_mode', 'escalation_percent', 'escalation_every_months', 'currency_id', 'is_template',
    'company_id', 'active',
}


def _months_between(d1, d2):
    """Whole months elapsed from d1 to d2 (negative when d2 is before d1)."""
    months = (d2.year - d1.year) * 12 + d2.month - d1.month
    if d2.day < d1.day:
        months -= 1
    return months


class ContractRadar(models.Model):
    _name = 'contract.radar'
    _description = 'Contract'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'next_action_date asc, id desc'
    _check_company_auto = True
    _mail_post_access = 'read'

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------
    name = fields.Char(string='Contract Title', required=True, tracking=True)
    reference = fields.Char(string='Reference', default='New', copy=False, readonly=True, index=True)
    active = fields.Boolean(default=True)
    contract_type = fields.Selection(CONTRACT_TYPES, string='Type', required=True, default='vendor', tracking=True)
    flow = fields.Selection(
        [('cost', 'Cost / Payable'), ('revenue', 'Revenue / Receivable')], string='Flow',
        compute='_compute_flow', store=True, readonly=False, precompute=True,
        help="Cost contracts count towards your commitment forecast; revenue contracts (customers) do not.")
    state = fields.Selection(STATES, string='Status', default='draft', required=True, copy=False,
                             tracking=True, index=True)
    kanban_state = fields.Selection(
        [('normal', 'In Progress'), ('done', 'Ready'), ('blocked', 'Blocked')],
        string='Kanban Status', default='normal', copy=False)
    is_template = fields.Boolean(string='Is a Template', copy=False)
    template_id = fields.Many2one('contract.radar', string='Template', domain="[('is_template', '=', True)]",
                                  copy=False)

    # ------------------------------------------------------------------
    # Parties and ownership
    # ------------------------------------------------------------------
    partner_id = fields.Many2one('res.partner', string='Counterparty', tracking=True, check_company=True, index=True)
    company_id = fields.Many2one('res.company', string='Company', required=True, index=True,
                                 default=lambda self: self.env.company)
    currency_id = fields.Many2one('res.currency', string='Currency', required=True, tracking=True,
                                  default=lambda self: self.env.company.currency_id)
    company_currency_id = fields.Many2one(related='company_id.currency_id', string='Company Currency', readonly=True)
    analytic_account_id = fields.Many2one('account.analytic.account', string='Analytic Account', check_company=True)
    user_id = fields.Many2one('res.users', string='Responsible', default=lambda self: self.env.user,
                              tracking=True, index=True)
    backup_user_id = fields.Many2one('res.users', string='Backup', tracking=True)
    visibility = fields.Selection(
        [('all', 'All Contract Users'), ('followers', 'Confidential (Responsible & Followers Only)')],
        string='Visibility', required=True, default='all', tracking=True,
        help="Confidential contracts are only visible to the responsible user, the backup, followers "
             "and members of the Confidential Access group.")

    # ------------------------------------------------------------------
    # Dates and deadlines
    # ------------------------------------------------------------------
    date_start = fields.Date(string='Start Date', default=fields.Date.context_today, tracking=True)
    date_end = fields.Date(string='End Date', tracking=True)
    term_months = fields.Integer(string='Default Term (months)', default=12)
    renewal_term_months = fields.Integer(string='Renewal Term (months)', default=12)
    auto_renew = fields.Boolean(string='Auto-Renew', tracking=True)
    notice_days = fields.Integer(string='Notice Period (days)', default=30, tracking=True)
    notice_sent_date = fields.Date(string='Notice Served On', copy=False, tracking=True)
    notice_body = fields.Html(string='Notice Letter', copy=False, readonly=True, sanitize=True,
                              help="The text of the notice letter that was served.")
    cancel_by_business_day = fields.Boolean(
        string='Skip Weekends & Holidays', default=True,
        help="When the last day to cancel falls on a weekend or a public holiday of the company working "
             "calendar, move it back to the previous working day.")
    cancel_by_note = fields.Char(string='Deadline Note', compute='_compute_cancel_by_note')
    will_renew = fields.Boolean(string='Will Renew', compute='_compute_will_renew', store=True)
    date_cancel_by = fields.Date(string='Last Day to Cancel', compute='_compute_date_cancel_by', store=True)
    next_action_date = fields.Date(string='Next Action Date', compute='_compute_next_action_date', store=True,
                                   help="Last day to cancel for auto-renewing contracts, otherwise the end date.")
    days_to_end = fields.Integer(string='Days to End', compute='_compute_days')
    days_to_action = fields.Integer(string='Days to Action', compute='_compute_days')
    urgency = fields.Selection(
        [('none', 'N/A'), ('ok', 'On Track'), ('soon', 'Soon'), ('urgent', 'Urgent'), ('overdue', 'Window Closed')],
        string='Urgency', compute='_compute_days')
    alert_text = fields.Char(string='Alert', compute='_compute_alert_text')

    # ------------------------------------------------------------------
    # Money
    # ------------------------------------------------------------------
    billing_period = fields.Selection(BILLING_PERIODS, string='Billing Period', default='yearly', required=True)
    amount = fields.Monetary(string='Recurring Amount', currency_field='currency_id', tracking=True,
                             help="Amount per billing period (or the total for one-time contracts).")
    annual_value = fields.Monetary(string='Annual Value', currency_field='currency_id',
                                   compute='_compute_annual_value', store=True)
    renewal_amount = fields.Monetary(string='Cost of Next Term', currency_field='currency_id',
                                     compute='_compute_renewal_amount')
    projected_total = fields.Monetary(string='Projected Total Cost', currency_field='currency_id',
                                      readonly=True, copy=False,
                                      help="Sum of all projected payments over the life of this contract.")
    escalation_mode = fields.Selection(
        [('none', 'None'), ('fixed_percent', 'Fixed % Every Period'), ('scheduled', 'Scheduled Step-ups')],
        string='Price Escalation', default='none', required=True, tracking=True)
    escalation_percent = fields.Float(string='Increase %', digits=(16, 2))
    escalation_every_months = fields.Integer(string='Every (months)', default=12)
    escalation_ids = fields.One2many('contract.radar.escalation', 'contract_id', string='Scheduled Step-ups')
    escalation_clause = fields.Text(string='Price Increase Clause')

    # ------------------------------------------------------------------
    # Renewal decision and savings
    # ------------------------------------------------------------------
    decision = fields.Selection(DECISIONS, string='Decision', copy=False, tracking=True)
    decision_state = fields.Selection(DECISION_STATES, string='Decision Status', default='none', required=True,
                                      copy=False, tracking=True, index=True)
    decision_reason = fields.Text(string='Reason', copy=False)
    decision_requested_by_id = fields.Many2one('res.users', string='Requested By', copy=False, readonly=True)
    decision_approver_id = fields.Many2one('res.users', string='Decided By', copy=False, readonly=True)
    decision_date = fields.Date(string='Decision Date', copy=False, readonly=True, index=True)
    decision_baseline_amount = fields.Monetary(
        string='Cost of Next Term (When Submitted)', currency_field='currency_id', copy=False, readonly=True,
        help="What the next term would have cost if nothing was done. Saved when the decision is submitted.")
    decision_new_amount = fields.Monetary(
        string='Agreed Cost of Next Term', currency_field='currency_id', copy=False, tracking=True,
        help="For a renegotiation: the total cost of the next term you agreed with the counterparty. "
             "It becomes the price of the renewed contract.")
    saving_amount = fields.Monetary(
        string='Approved Savings', currency_field='currency_id', compute='_compute_saving_amount', store=True,
        help="Cancel: the cost of the next term avoided (counted once the notice is served). "
             "Renegotiate: the cost of the next term minus the agreed cost.")

    # ------------------------------------------------------------------
    # Details, reminders, history
    # ------------------------------------------------------------------
    external_ref = fields.Char(string='Counterparty Contract No.')
    asset_ref = fields.Char(string='Asset / Property / Product')
    license_seats = fields.Integer(string='License Seats')
    terms = fields.Html(string='Terms & Notes')
    reminder_days = fields.Char(string='Reminder Stages (days before)', default='90,60,30,7',
                                help="Comma-separated days before the next action date, e.g. 90,60,30,7.")
    reminder_log_ids = fields.One2many('contract.radar.reminder.log', 'contract_id', string='Reminders Sent')
    cost_line_ids = fields.One2many('contract.radar.cost.line', 'contract_id', string='Projected Payments')
    amendment_ids = fields.One2many('contract.radar.amendment', 'contract_id', string='Amendments')
    amendment_count = fields.Integer(compute='_compute_counts')
    renewed_from_id = fields.Many2one('contract.radar', string='Renewed From', copy=False, index=True)
    renewed_to_ids = fields.One2many('contract.radar', 'renewed_from_id', string='Renewals')
    renewal_count = fields.Integer(compute='_compute_counts')
    attachment_count = fields.Integer(string='Documents', compute='_compute_attachment_count')

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains('date_start', 'date_end')
    def _check_dates(self):
        for rec in self:
            if rec.date_start and rec.date_end and rec.date_end < rec.date_start:
                raise ValidationError(_("The end date cannot be before the start date."))

    @api.constrains('partner_id', 'date_start', 'is_template')
    def _check_required_for_contracts(self):
        for rec in self:
            if not rec.is_template and (not rec.partner_id or not rec.date_start):
                raise ValidationError(_("A contract needs a counterparty and a start date."))

    @api.constrains('notice_days', 'renewal_term_months', 'term_months', 'escalation_every_months')
    def _check_positive(self):
        for rec in self:
            if rec.notice_days < 0 or rec.renewal_term_months < 0 or rec.term_months < 0 \
                    or rec.escalation_every_months < 0:
                raise ValidationError(_("Notice period, terms and escalation interval cannot be negative."))

    @api.constrains('reminder_days')
    def _check_reminder_days(self):
        for rec in self:
            for chunk in (rec.reminder_days or '').replace(';', ',').split(','):
                chunk = chunk.strip()
                if chunk and not chunk.isdigit():
                    raise ValidationError(_("Reminder stages must be whole numbers separated by commas, e.g. 90,60,30,7."))

    @api.ondelete(at_uninstall=False)
    def _unlink_except_live(self):
        if any(rec.state in LIVE_STATES for rec in self):
            raise UserError(_("You cannot delete an active contract. Cancel or archive it instead."))

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends('contract_type')
    def _compute_flow(self):
        for rec in self:
            rec.flow = 'revenue' if rec.contract_type == 'customer' else 'cost'

    @api.depends('auto_renew', 'notice_sent_date')
    def _compute_will_renew(self):
        for rec in self:
            rec.will_renew = bool(rec.auto_renew and not rec.notice_sent_date)

    @api.depends('date_end', 'notice_days', 'cancel_by_business_day', 'company_id')
    def _compute_date_cancel_by(self):
        for rec in self:
            if not rec.date_end:
                rec.date_cancel_by = False
                continue
            deadline = rec.date_end - timedelta(days=rec.notice_days or 0)
            if rec.cancel_by_business_day:
                deadline = rec._previous_working_day(deadline)
            rec.date_cancel_by = deadline

    @api.depends('date_end', 'notice_days', 'date_cancel_by')
    def _compute_cancel_by_note(self):
        for rec in self:
            note = False
            if rec.date_end and rec.date_cancel_by:
                nominal = rec.date_end - timedelta(days=rec.notice_days or 0)
                if nominal != rec.date_cancel_by:
                    note = _("Moved earlier from %s because that day is a weekend or a public holiday.",
                             format_date(self.env, nominal))
            rec.cancel_by_note = note

    def _is_working_day(self, calendar, day):
        """True when ``day`` is a working day of ``calendar`` and not covered by a company-wide leave."""
        self.ensure_one()
        if not calendar._works_on_date(day):
            return False
        tz = pytz.timezone(calendar.tz or 'UTC')
        day_start = tz.localize(datetime.combine(day, time.min)).astimezone(pytz.utc).replace(tzinfo=None)
        day_end = tz.localize(datetime.combine(day, time.max)).astimezone(pytz.utc).replace(tzinfo=None)
        holidays = self.env['resource.calendar.leaves'].sudo().search_count([
            ('resource_id', '=', False),
            ('calendar_id', 'in', [calendar.id, False]),
            ('company_id', 'in', [self.company_id.id, False]),
            ('date_from', '<=', day_end),
            ('date_to', '>=', day_start),
        ])
        return not holidays

    def _previous_working_day(self, day):
        """``day`` itself when it is a working day, otherwise the closest earlier working day."""
        self.ensure_one()
        calendar = self.company_id.sudo().resource_calendar_id
        if not calendar or not day:
            return day
        for _step in range(MAX_WORKDAY_STEPS):
            if self._is_working_day(calendar, day):
                return day
            day -= timedelta(days=1)
        return day

    @api.depends('decision_state', 'decision', 'decision_baseline_amount', 'decision_new_amount',
                 'notice_sent_date', 'auto_renew', 'flow')
    def _compute_saving_amount(self):
        for rec in self:
            saving = 0.0
            if rec.decision_state == 'approved' and rec.flow == 'cost':
                if rec.decision == 'cancel':
                    if rec.notice_sent_date or not rec.auto_renew:
                        saving = rec.decision_baseline_amount
                elif rec.decision == 'renegotiate' and rec.decision_new_amount:
                    saving = max(rec.decision_baseline_amount - rec.decision_new_amount, 0.0)
            rec.saving_amount = saving

    @api.depends('will_renew', 'date_cancel_by', 'date_end')
    def _compute_next_action_date(self):
        for rec in self:
            rec.next_action_date = rec.date_cancel_by if (rec.will_renew and rec.date_cancel_by) else rec.date_end

    @api.depends('date_end', 'next_action_date', 'state', 'is_template')
    def _compute_days(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.days_to_end = (rec.date_end - today).days if rec.date_end else 0
            rec.days_to_action = (rec.next_action_date - today).days if rec.next_action_date else 0
            if rec.is_template or rec.state not in LIVE_STATES or not rec.next_action_date:
                rec.urgency = 'none'
            elif rec.days_to_action < 0:
                rec.urgency = 'overdue' if (rec.date_end and rec.date_end >= today) else 'none'
            elif rec.days_to_action <= 7:
                rec.urgency = 'urgent'
            elif rec.days_to_action <= 30:
                rec.urgency = 'soon'
            else:
                rec.urgency = 'ok'

    @api.depends('amount', 'billing_period')
    def _compute_annual_value(self):
        for rec in self:
            pm = PERIOD_MONTHS.get(rec.billing_period, 0)
            rec.annual_value = (rec.amount or 0.0) * (12.0 / pm) if pm else (rec.amount or 0.0)

    @api.depends('amount', 'billing_period', 'renewal_term_months', 'date_end', 'date_start', 'escalation_mode',
                 'escalation_percent', 'escalation_every_months', 'escalation_ids.date', 'escalation_ids.value',
                 'escalation_ids.step_type')
    def _compute_renewal_amount(self):
        for rec in self:
            if not rec.amount:
                rec.renewal_amount = 0.0
                continue
            start = rec.date_end + timedelta(days=1) if rec.date_end else fields.Date.context_today(rec)
            per_period = rec._period_amount_at(start)
            pm = PERIOD_MONTHS.get(rec.billing_period, 0)
            periods = 1.0 if not pm else max(rec.renewal_term_months or 12, 1) / float(pm)
            total = per_period * periods
            rec.renewal_amount = rec.currency_id.round(total) if rec.currency_id else total

    @api.depends('state', 'amount', 'renewal_amount', 'date_end', 'date_cancel_by', 'will_renew', 'currency_id',
                 'is_template')
    def _compute_alert_text(self):
        for rec in self:
            if rec.is_template or rec.state not in LIVE_STATES or not rec.date_end:
                rec.alert_text = False
            else:
                rec.alert_text = rec._alert_text()

    @api.depends('amendment_ids', 'renewed_to_ids', 'renewed_from_id')
    def _compute_counts(self):
        for rec in self:
            rec.amendment_count = len(rec.amendment_ids)
            rec.renewal_count = len(rec.renewed_to_ids) + (1 if rec.renewed_from_id else 0)

    def _compute_attachment_count(self):
        counts = {}
        if self.ids:
            data = self.env['ir.attachment'].sudo()._read_group(
                [('res_model', '=', self._name), ('res_id', 'in', self.ids)], ['res_id'], ['__count'])
            counts = {res_id: count for res_id, count in data}
        for rec in self:
            rec.attachment_count = counts.get(rec.id, 0)

    @api.depends('reference', 'name')
    def _compute_display_name(self):
        for rec in self:
            if rec.reference and rec.reference != 'New':
                rec.display_name = "%s - %s" % (rec.reference, rec.name or '')
            else:
                rec.display_name = rec.name or ''

    # ------------------------------------------------------------------
    # Pricing logic
    # ------------------------------------------------------------------
    def _period_amount_at(self, on_date, force_scheduled=False):
        """Recurring amount per billing period that applies on ``on_date`` after price escalation."""
        self.ensure_one()
        amount = self.amount or 0.0
        if not on_date:
            return amount
        if self.escalation_mode == 'fixed_percent' and self.escalation_percent \
                and self.escalation_every_months > 0 and self.date_start:
            steps = max(_months_between(self.date_start, on_date), 0) // self.escalation_every_months
            if steps:
                amount *= (1 + self.escalation_percent / 100.0) ** steps
        elif self.escalation_mode == 'scheduled' or force_scheduled:
            for step in self.escalation_ids.sorted(key=lambda s: s.date or date.max):
                if step.date and step.date <= on_date:
                    if step.step_type == 'percent':
                        amount *= (1 + (step.value or 0.0) / 100.0)
                    else:
                        amount = step.value or 0.0
        return amount

    def _compute_cost_line_vals(self):
        """Projected payments for one contract, as create() values."""
        self.ensure_one()
        if self.is_template or not self.active or not self.date_start or not self.amount \
                or self.state == 'draft' or not self.id:
            return []
        today = fields.Date.context_today(self)
        pm = PERIOD_MONTHS.get(self.billing_period, 0)
        rolling = self.state in LIVE_STATES and (self.will_renew or not self.date_end)
        if rolling:
            end = today + relativedelta(months=HORIZON_MONTHS)
        elif self.date_end:
            end = self.date_end
        else:
            end = today
        pay_dates = []
        if not pm:
            pay_dates = [self.date_start]
        else:
            k = 0
            while len(pay_dates) < MAX_COST_LINES:
                d = self.date_start + relativedelta(months=k * pm)
                if d > end:
                    break
                pay_dates.append(d)
                k += 1
        vals_list = []
        for d in pay_dates:
            amt = self.currency_id.round(self._period_amount_at(d))
            amt_company = self.currency_id._convert(amt, self.company_currency_id, self.company_id, today)
            vals_list.append({
                'contract_id': self.id,
                'date': d,
                'amount': amt,
                'amount_company': amt_company,
            })
        return vals_list

    def _rebuild_cost_lines(self):
        records = self.exists()
        if not records:
            return
        Line = self.env['contract.radar.cost.line'].sudo()
        Line.search([('contract_id', 'in', records.ids)]).unlink()
        vals_list, totals = [], {}
        for rec in records:
            rec_vals = rec._compute_cost_line_vals()
            vals_list += rec_vals
            totals[rec.id] = sum(v['amount'] for v in rec_vals)
        if vals_list:
            Line.create(vals_list)
        for rec in records:
            if rec.projected_total != totals[rec.id]:
                rec.sudo().with_context(tracking_disable=True).write({'projected_total': totals[rec.id]})

    # ------------------------------------------------------------------
    # ORM overrides
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('reference', 'New') == 'New':
                code = 'contract.radar.template' if vals.get('is_template') else 'contract.radar'
                vals['reference'] = self.env['ir.sequence'].next_by_code(code) or 'New'
        records = super().create(vals_list)
        records._rebuild_cost_lines()
        return records

    def write(self, vals):
        res = super().write(vals)
        if REBUILD_FIELDS & set(vals):
            self._rebuild_cost_lines()
        return res

    @api.onchange('template_id')
    def _onchange_template_id(self):
        copy_fields = [
            'contract_type', 'billing_period', 'amount', 'currency_id', 'notice_days', 'auto_renew',
            'term_months', 'renewal_term_months', 'escalation_mode', 'escalation_percent',
            'escalation_every_months', 'escalation_clause', 'reminder_days', 'visibility', 'terms',
            'asset_ref', 'license_seats', 'flow',
        ]
        for rec in self:
            tpl = rec.template_id
            if not tpl:
                continue
            for fname in copy_fields:
                rec[fname] = tpl[fname]
            if rec.date_start and tpl.term_months:
                rec.date_end = rec.date_start + relativedelta(months=tpl.term_months) - timedelta(days=1)

    @api.onchange('date_start')
    def _onchange_date_start(self):
        for rec in self:
            if rec.date_start and not rec.date_end and rec.term_months and not rec.is_template:
                rec.date_end = rec.date_start + relativedelta(months=rec.term_months) - timedelta(days=1)

    @api.model
    def get_import_templates(self):
        return [{
            'label': _('Import Template for Contracts'),
            'template': '/contract_radar/static/xls/contract_import_template.csv',
        }]

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def _sync_state(self, today=None):
        """Move live contracts between Active and Expiring depending on the end date."""
        today = today or fields.Date.context_today(self)
        window = today + timedelta(days=EXPIRING_WINDOW_DAYS)
        for rec in self.filtered(lambda r: r.state in LIVE_STATES and not r.is_template):
            if rec.date_end and rec.date_end < today:
                continue  # handled by the daily cron (renew or expire)
            new_state = 'expiring' if (rec.date_end and rec.date_end <= window) else 'active'
            if rec.state != new_state:
                rec.state = new_state

    def action_activate(self):
        for rec in self:
            if rec.is_template:
                raise UserError(_("Templates cannot be activated."))
            if not rec.partner_id or not rec.date_start:
                raise UserError(_("Set a counterparty and a start date before activating."))
            rec.state = 'active'
        self._sync_state()
        return True

    def action_open_notice_wizard(self):
        """Open the Serve Notice dialog (letter text, recipients, PDF)."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Serve Notice"),
            'res_model': 'contract.radar.notice.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_contract_id': self.id},
        }

    def action_serve_notice(self):
        """Record that notice was served today. The wizard calls this after it builds the letter."""
        today = fields.Date.context_today(self)
        for rec in self:
            rec.notice_sent_date = today
            rec.message_post(body=_("Notice served on %s. This contract will not auto-renew.",
                                    format_date(self.env, today)))
            rec._notify_webhooks('notice', _("Notice served for %(name)s on %(date)s. It will not auto-renew.",
                                             name=rec.name, date=format_date(self.env, today)))
        return True

    def action_cancel(self):
        self.write({'state': 'cancelled'})
        return True

    def action_draft(self):
        self.write({'state': 'draft'})
        return True

    def _do_renew(self, auto=False):
        """Create the next-term contract, mark this one Renewed, and return the new record."""
        self.ensure_one()
        if not self.date_end:
            raise UserError(_("Set an end date before renewing."))
        new_start = self.date_end + timedelta(days=1)
        term = max(self.renewal_term_months or 12, 1)
        new_end = new_start + relativedelta(months=term) - timedelta(days=1)
        new_amount = self.currency_id.round(self._period_amount_at(new_start))
        if self.decision_state == 'approved' and self.decision == 'renegotiate' and self.decision_new_amount:
            pm = PERIOD_MONTHS.get(self.billing_period, 0)
            periods = 1.0 if not pm else term / float(pm)
            new_amount = self.currency_id.round(self.decision_new_amount / periods)
        new = self.copy({
            'date_start': new_start,
            'date_end': new_end,
            'amount': new_amount,
            'state': 'active',
            'notice_sent_date': False,
            'renewed_from_id': self.id,
            'kanban_state': 'normal',
        })
        for step in self.escalation_ids.filtered(lambda s: s.date and s.date > new_start):
            step.copy({'contract_id': new.id})
        self.write({'state': 'renewed'})
        how = _("Auto-renewed") if auto else _("Renewed")
        self.message_post(body=Markup("<p>{}: <b>{}</b></p>").format(how, new.display_name))
        new.message_post(body=Markup("<p>{} {} ({} - {})</p>").format(
            how, _("from %s", self.display_name), format_date(self.env, new_start), format_date(self.env, new_end)))
        new._sync_state()
        return new

    def action_renew(self):
        self.ensure_one()
        if self.state not in LIVE_STATES:
            raise UserError(_("Only active or expiring contracts can be renewed."))
        new = self._do_renew()
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': new.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_view_documents(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Documents'),
            'res_model': 'ir.attachment',
            'view_mode': 'list,form',
            'domain': [('res_model', '=', self._name), ('res_id', '=', self.id)],
            'context': {'default_res_model': self._name, 'default_res_id': self.id},
        }

    def action_view_renewals(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Renewal History'),
            'res_model': self._name,
            'view_mode': 'list,form',
            'domain': [('id', 'in', (self.renewed_to_ids | self.renewed_from_id).ids)],
            'context': {'active_test': False},
        }

    def action_view_amendments(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Amendments'),
            'res_model': 'contract.radar.amendment',
            'view_mode': 'list,form',
            'domain': [('contract_id', '=', self.id)],
            'context': {'default_contract_id': self.id},
        }

    # ------------------------------------------------------------------
    # Renewal decision workflow
    # ------------------------------------------------------------------
    def _decision_approvers(self):
        """Managers who are allowed to see this contract (confidential contracts narrow the list)."""
        self.ensure_one()
        group = self.env.ref('contract_radar.group_contract_manager')
        users = self.env['res.users'].search(
            [('active', '=', True), ('share', '=', False)]).filtered(
            lambda u: u.has_group('contract_radar.group_contract_manager'))
        if self.visibility == 'followers':
            users = users.filtered(
                lambda u: u.has_group('contract_radar.group_contract_confidential')
                or u in (self.user_id | self.backup_user_id)
                or u.partner_id in self.message_partner_ids)
        return users

    def _schedule_approval_activities(self):
        self.ensure_one()
        deadline = self.next_action_date or fields.Date.context_today(self)
        summary = APPROVAL_PREFIX + _("approve renewal decision")
        note = Markup("<p>{}</p>").format(
            _("%(user)s proposes to %(decision)s: %(name)s.", user=self.env.user.name,
              decision=dict(DECISIONS).get(self.decision, ''), name=self.name))
        for user in self._decision_approvers()[:10]:
            try:
                with self.env.cr.savepoint():
                    self.sudo().activity_schedule(
                        'mail.mail_activity_data_todo', date_deadline=deadline,
                        summary=summary, note=note, user_id=user.id)
            except Exception:
                _logger.exception("Contract Radar: could not schedule approval activity for user %s", user.id)

    def _close_approval_activities(self):
        self.ensure_one()
        acts = self.sudo().activity_ids.filtered(lambda a: (a.summary or '').startswith(APPROVAL_PREFIX))
        acts.unlink()

    def action_submit_decision(self):
        for rec in self:
            if rec.is_template or rec.state not in LIVE_STATES:
                raise UserError(_("Only active or expiring contracts can have a renewal decision."))
            if rec.decision_state not in ('none', 'rejected'):
                raise UserError(_("This decision has already been submitted."))
            if not rec.decision:
                raise UserError(_("Choose a decision first: renew, renegotiate or cancel."))
            if not (rec.decision_reason or '').strip():
                raise UserError(_("Please give a reason for the decision."))
            if rec.decision == 'renegotiate' and not rec.decision_new_amount:
                raise UserError(_("Enter the agreed cost of the next term for a renegotiation."))
            rec.write({
                'decision_state': 'pending',
                'decision_requested_by_id': self.env.user.id,
                'decision_baseline_amount': rec.renewal_amount,
                'decision_approver_id': False,
                'decision_date': False,
            })
            rec.message_post(body=Markup("<p>{}: <b>{}</b></p><p>{}</p>").format(
                _("Decision submitted for approval"), dict(DECISIONS).get(rec.decision, ''),
                rec.decision_reason))
            rec._schedule_approval_activities()
        return True

    def _check_can_decide(self):
        if not self.env.user.has_group('contract_radar.group_contract_manager'):
            raise AccessError(_("Only contract managers can approve or reject a renewal decision."))

    def action_approve_decision(self):
        self._check_can_decide()
        today = fields.Date.context_today(self)
        for rec in self:
            if rec.decision_state != 'pending':
                raise UserError(_("There is no pending decision to approve on %s.", rec.name))
            rec.write({
                'decision_state': 'approved',
                'decision_approver_id': self.env.user.id,
                'decision_date': today,
            })
            rec._close_approval_activities()
            rec.message_post(body=Markup("<p>{}: <b>{}</b></p>").format(
                _("Decision approved"), dict(DECISIONS).get(rec.decision, '')))
            rec._notify_webhooks('decision', _("Decision approved for %(name)s: %(decision)s.",
                                               name=rec.name, decision=dict(DECISIONS).get(rec.decision, '')))
        return True

    def action_reject_decision(self):
        self._check_can_decide()
        for rec in self:
            if rec.decision_state != 'pending':
                raise UserError(_("There is no pending decision to reject on %s.", rec.name))
            rec.write({'decision_state': 'rejected', 'decision_approver_id': self.env.user.id})
            rec._close_approval_activities()
            rec.message_post(body=_("Decision rejected. It can be edited and submitted again."))
        return True

    def action_reset_decision(self):
        self._check_can_decide()
        for rec in self:
            rec.write({
                'decision': False,
                'decision_state': 'none',
                'decision_reason': False,
                'decision_requested_by_id': False,
                'decision_approver_id': False,
                'decision_date': False,
                'decision_baseline_amount': 0.0,
                'decision_new_amount': 0.0,
            })
            rec._close_approval_activities()
        return True

    # ------------------------------------------------------------------
    # Webhooks (Slack, Teams-type workflows, anything that takes JSON)
    # ------------------------------------------------------------------
    def _notify_webhooks(self, event, text):
        """Push a short message to the configured webhooks. Never raises; confidential contracts are skipped."""
        self.ensure_one()
        if self.visibility == 'followers' or self.is_template:
            return
        hooks = self.env['contract.radar.webhook'].sudo().search([
            ('active', '=', True),
            ('company_id', 'in', [False, self.company_id.id]),
            ('on_' + event, '=', True),
        ])
        for hook in hooks:
            try:
                with self.env.cr.savepoint():
                    hook._send(event, text, {
                        'contract': self.display_name,
                        'contract_id': self.id,
                        'counterparty': self.partner_id.name or '',
                    })
            except Exception:
                _logger.exception("Contract Radar: webhook %s failed", hook.id)

    # ------------------------------------------------------------------
    # Alerts and reminders
    # ------------------------------------------------------------------
    def _alert_text(self):
        """Human sentence with the money in it, e.g. 'Office lease renews in 14 days for NGN 4,200,000. Cancel by ...'"""
        self.ensure_one()
        today = fields.Date.context_today(self)
        if not self.date_end:
            return ''
        days_end = (self.date_end - today).days
        if days_end < 0:
            when = _("%s days ago", -days_end)
        elif days_end == 0:
            when = _("today")
        else:
            when = _("in %s days", days_end)
        amount = format_amount(self.env, self.renewal_amount, self.currency_id)
        if self.will_renew and self.date_cancel_by:
            if self.date_cancel_by >= today:
                return _("%(name)s renews %(when)s for %(amount)s. Cancel by %(date)s.",
                         name=self.name, when=when, amount=amount, date=format_date(self.env, self.date_cancel_by))
            return _("%(name)s renews %(when)s for %(amount)s. The cancellation window closed on %(date)s.",
                     name=self.name, when=when, amount=amount, date=format_date(self.env, self.date_cancel_by))
        return _("%(name)s ends %(when)s. Renewal value would be %(amount)s.",
                 name=self.name, when=when, amount=amount)

    def _get_reminder_stages(self):
        self.ensure_one()
        stages = set()
        for chunk in (self.reminder_days or '').replace(';', ',').split(','):
            chunk = chunk.strip()
            if chunk.isdigit():
                stages.add(int(chunk))
        return sorted(stages, reverse=True)

    def _process_reminder(self, today):
        self.ensure_one()
        if not self.next_action_date or self.next_action_date < today:
            return
        stages = self._get_reminder_stages()
        days = (self.next_action_date - today).days
        due = [s for s in stages if days <= s]
        if not due:
            return
        Log = self.env['contract.radar.reminder.log'].sudo()
        logged = set(Log.search([
            ('contract_id', '=', self.id), ('anchor_date', '=', self.next_action_date)]).mapped('stage_days'))
        fresh = [s for s in due if s not in logged]
        if not fresh:
            return
        stage = min(fresh)
        self._send_reminder(stage)
        self._notify_webhooks('reminder', self._alert_text())
        Log.create([{
            'contract_id': self.id,
            'stage_days': s,
            'anchor_date': self.next_action_date,
            'notified': s == stage,
        } for s in fresh])

    def _send_reminder(self, stage):
        self.ensure_one()
        text = self._alert_text()
        users = (self.user_id | self.backup_user_id).filtered(lambda u: u.active and not u.share)
        if not users:
            return
        if self.will_renew:
            short = _("Cancel by %s", format_date(self.env, self.next_action_date))
        else:
            short = _("Ends %s", format_date(self.env, self.next_action_date))
        old = self.activity_ids.filtered(
            lambda a: (a.summary or '').startswith(ACTIVITY_PREFIX) and a.user_id in users)
        old.unlink()
        for user in users:
            if user.has_group('contract_radar.group_contract_viewer'):
                self.activity_schedule(
                    'mail.mail_activity_data_todo',
                    date_deadline=self.next_action_date,
                    summary=ACTIVITY_PREFIX + short,
                    note=Markup("<p>{}</p>").format(text),
                    user_id=user.id)
        partners = users.mapped('partner_id').filtered('email')
        if partners:
            self.message_post(
                body=Markup("<p>{}</p><p>{}</p>").format(
                    text, _("Reminder stage: %s days before the deadline.", stage)),
                subject=_("Contract reminder: %s", self.name),
                partner_ids=partners.ids,
                subtype_xmlid='mail.mt_comment',
                message_type='comment',
                email_layout_xmlid='mail.mail_notification_light')

    # ------------------------------------------------------------------
    # Crons
    # ------------------------------------------------------------------
    def _daily_step(self, today):
        rec = self
        loops = 0
        while rec.date_end and rec.date_end < today and loops < 36:
            if rec.state not in LIVE_STATES:
                break
            if rec.will_renew:
                rec = rec._do_renew(auto=True)
            else:
                rec.state = 'expired'
                break
            loops += 1
        rec._sync_state(today)

    def _needs_daily_rebuild(self):
        """Only contracts whose projection moves with the calendar need rebuilding every day:
        rolling ones (the horizon follows today) and foreign-currency ones (today's exchange rate)."""
        self.ensure_one()
        return bool(self.will_renew or not self.date_end or self.currency_id != self.company_currency_id)

    @api.model
    def _cron_daily_update(self):
        today = fields.Date.context_today(self)
        live_dom = [('is_template', '=', False), ('state', 'in', list(LIVE_STATES))]

        # 1. Public holidays can be added after a contract was saved: refresh the cancel-by dates.
        renewing = self.search(live_dom + [('auto_renew', '=', True), ('cancel_by_business_day', '=', True)])
        renewing.modified(['notice_days'])

        # 2. Status moves (renew / expire / active <-> expiring) only concern contracts near their end date.
        window = today + timedelta(days=EXPIRING_WINDOW_DAYS)
        for rec in self.search(live_dom + ['|', ('state', '=', 'expiring'), ('date_end', '<=', window)]):
            try:
                with self.env.cr.savepoint():
                    rec._daily_step(today)
            except Exception:
                _logger.exception("Contract Radar: daily update failed for contract %s", rec.id)

        # 3. Rebuild the payment schedule only where it can change, then send reminders.
        for rec in self.search(live_dom):
            try:
                with self.env.cr.savepoint():
                    if rec._needs_daily_rebuild():
                        rec._rebuild_cost_lines()
                    rec._process_reminder(today)
            except Exception:
                _logger.exception("Contract Radar: reminder run failed for contract %s", rec.id)

    @api.model
    def _cron_weekly_digest(self):
        today = fields.Date.context_today(self)
        Users = self.env['res.users']
        finance_group = self.env.ref('contract_radar.group_contract_finance')
        managers = Users.search([('active', '=', True), ('share', '=', False)]).filtered(
            lambda u: u.has_group('contract_radar.group_contract_finance'))
        live = self.search([('is_template', '=', False), ('state', 'in', list(LIVE_STATES))])
        owners = (live.mapped('user_id') | live.mapped('backup_user_id')).filtered(
            lambda u: u.active and not u.share and u.has_group('contract_radar.group_contract_viewer'))
        for user in (managers | owners):
            try:
                with self.env.cr.savepoint():
                    self._send_digest_to(user, user in managers, today)
            except Exception:
                _logger.exception("Contract Radar: digest failed for user %s", user.id)

    @api.model
    def _send_digest_to(self, user, is_manager, today):
        if not user.email:
            return
        env_user = self.with_user(user).with_context(allowed_company_ids=user.company_ids.ids)
        dom = [('is_template', '=', False), ('state', 'in', list(LIVE_STATES))]
        if not is_manager:
            dom += ['|', ('user_id', '=', user.id), ('backup_user_id', '=', user.id)]
        limit = today + timedelta(days=30)
        urgent = env_user.search(
            dom + [('next_action_date', '>=', today), ('next_action_date', '<=', limit)],
            order='next_action_date asc', limit=25)
        later = env_user.search_count(dom + [('date_end', '>', limit), ('date_end', '<=', today + timedelta(days=90))])
        no_owner = env_user.search_count(dom + [('user_id', '=', False)]) if is_manager else 0
        no_end = env_user.search_count(dom + [('date_end', '=', False)]) if is_manager else 0
        if not (urgent or later or no_owner or no_end):
            return
        rows = []
        for rec in urgent:
            rows.append(Markup(
                '<tr><td style="padding:4px 8px">{}</td><td style="padding:4px 8px">{}</td>'
                '<td style="padding:4px 8px"><b>{}</b></td></tr>').format(
                    rec.display_name, rec.partner_id.name or '', rec._alert_text()))
        table = Markup(
            '<table style="border-collapse:collapse;font-size:13px"><tr style="background:#f1f3f5">'
            '<th align="left" style="padding:4px 8px">{}</th><th align="left" style="padding:4px 8px">{}</th>'
            '<th align="left" style="padding:4px 8px">{}</th></tr>{}</table>').format(
                _("Contract"), _("Counterparty"), _("What needs attention"), Markup('').join(rows)) if rows else Markup('')
        notes = []
        if later:
            notes.append(_("%s more contract(s) end within 90 days.", later))
        if no_owner:
            notes.append(_("%s contract(s) have no responsible user.", no_owner))
        if no_end:
            notes.append(_("%s contract(s) have no end date.", no_end))
        body = Markup('<div style="font-family:Arial,sans-serif"><p>{}</p>{}<ul>{}</ul></div>').format(
            _("Here is what needs attention in the next 30 days:") if urgent else _("Contract Radar summary:"),
            table, Markup('').join(Markup('<li>{}</li>').format(n) for n in notes))
        mail = self.env['mail.mail'].sudo().create({
            'subject': _("Contract Radar - weekly digest (%s)", format_date(self.env, today)),
            'body_html': body,
            'recipient_ids': [(4, user.partner_id.id)],
            'auto_delete': True,
        })
        mail.send()

    # ------------------------------------------------------------------
    # Dashboard
    # ------------------------------------------------------------------
    @api.model
    def get_dashboard_data(self):
        today = fields.Date.context_today(self)
        company = self.env.company
        cur = company.currency_id
        to_s = fields.Date.to_string

        def conv(rec, value):
            return rec.currency_id._convert(value, cur, rec.company_id or company, today)

        def fmt(value):
            return format_amount(self.env, value, cur)

        base_dom = [('is_template', '=', False)]
        live_dom = base_dom + [('state', 'in', list(LIVE_STATES))]
        live = self.search(live_dom)

        expiring = []
        for days in (30, 60, 90):
            limit = today + timedelta(days=days)
            recs = live.filtered(lambda r, limit=limit: r.date_end and today <= r.date_end <= limit)
            expiring.append({
                'key': 'exp%s' % days,
                'title': _("Expiring in %s days", days),
                'count': len(recs),
                'value': fmt(sum(conv(r, r.annual_value) for r in recs if r.flow == 'cost')),
                'domain': live_dom + [('date_end', '>=', to_s(today)), ('date_end', '<=', to_s(limit))],
            })

        soon_limit = today + timedelta(days=60)
        auto = live.filtered(lambda r: r.will_renew and r.date_end and today <= r.date_end <= soon_limit)
        auto_renewing = {
            'count': len(auto),
            'value': fmt(sum(conv(r, r.renewal_amount) for r in auto if r.flow == 'cost')),
            'domain': live_dom + [('will_renew', '=', True), ('date_end', '>=', to_s(today)),
                                  ('date_end', '<=', to_s(soon_limit))],
        }

        risk_limit = today + timedelta(days=30)
        at_risk = live.filtered(lambda r: r.flow == 'cost' and r.will_renew and r.date_cancel_by
                                and today <= r.date_cancel_by <= risk_limit)
        money_at_risk = {
            'count': len(at_risk),
            'value': fmt(sum(conv(r, r.renewal_amount) for r in at_risk)),
            'domain': live_dom + [('flow', '=', 'cost'), ('will_renew', '=', True),
                                  ('date_cancel_by', '>=', to_s(today)), ('date_cancel_by', '<=', to_s(risk_limit))],
        }

        # 12-month commitment from the projected payment lines
        Line = self.env['contract.radar.cost.line']
        month_start = today.replace(day=1)
        end12 = month_start + relativedelta(months=12)
        line_dom = [('state', 'in', list(LIVE_STATES)), ('flow', '=', 'cost'),
                    ('date', '>=', to_s(month_start)), ('date', '<', to_s(end12))]
        groups = Line._read_group(line_dom, ['date:month', 'company_currency_id'], ['amount_company:sum'])
        per_month = {}
        for month, line_cur, total in groups:
            if not month:
                continue
            converted = line_cur._convert(total or 0.0, cur, company, today) if line_cur else (total or 0.0)
            key = (month.year, month.month)
            per_month[key] = per_month.get(key, 0.0) + converted
        months = []
        for i in range(12):
            m = month_start + relativedelta(months=i)
            months.append({'label': m.strftime('%b %y'), 'amount': per_month.get((m.year, m.month), 0.0)})
        peak = max([m['amount'] for m in months] + [0.0])
        for m in months:
            m['pct'] = round(m['amount'] / peak * 100.0, 1) if peak else 0.0
            m['display'] = fmt(m['amount'])
        total12 = sum(m['amount'] for m in months)
        commitment = {
            'value': fmt(total12),
            'months': months,
            'domain': [],
        }

        needs = live.filtered(lambda r: r.next_action_date and 0 <= (r.next_action_date - today).days <= 30)
        needs = needs.sorted(key=lambda r: r.next_action_date)[:12]
        alerts = [{
            'id': r.id,
            'name': r.name,
            'partner': r.partner_id.name or '',
            'text': r._alert_text(),
            'days': (r.next_action_date - today).days,
            'urgency': r.urgency,
        } for r in needs]

        by_type = []
        labels = dict(CONTRACT_TYPES)
        for key, label in CONTRACT_TYPES:
            recs = live.filtered(lambda r, key=key: r.contract_type == key)
            if recs:
                by_type.append({
                    'key': key,
                    'label': labels[key],
                    'count': len(recs),
                    'value': fmt(sum(conv(r, r.annual_value) for r in recs)),
                    'domain': live_dom + [('contract_type', '=', key)],
                })

        # Savings from approved cancel / renegotiate decisions in the last 12 months
        saved_from = today - timedelta(days=365)
        saved_dom = base_dom + [('decision_state', '=', 'approved'), ('saving_amount', '>', 0),
                                ('decision_date', '>=', to_s(saved_from))]
        saved = self.with_context(active_test=False).search(saved_dom)
        savings = {
            'count': len(saved),
            'value': fmt(sum(conv(r, r.saving_amount) for r in saved)),
            'domain': saved_dom,
        }

        open_dom = base_dom + [('state', 'in', ['draft', 'active', 'expiring'])]
        flags = {
            'pending_decisions': {
                'count': self.search_count(live_dom + [('decision_state', '=', 'pending')]),
                'domain': live_dom + [('decision_state', '=', 'pending')],
            },
            'no_owner': {
                'count': self.search_count(open_dom + [('user_id', '=', False)]),
                'domain': open_dom + [('user_id', '=', False)],
            },
            'no_end': {
                'count': self.search_count(open_dom + [('date_end', '=', False)]),
                'domain': open_dom + [('date_end', '=', False)],
            },
            'confidential': {
                'count': self.search_count(open_dom + [('visibility', '=', 'followers')]),
                'domain': open_dom + [('visibility', '=', 'followers')],
            },
        }
        return {
            'today': format_date(self.env, today),
            'live_count': len(live),
            'expiring': expiring,
            'auto_renewing': auto_renewing,
            'money_at_risk': money_at_risk,
            'savings': savings,
            'commitment': commitment,
            'alerts': alerts,
            'by_type': by_type,
            'flags': flags,
        }
