# -*- coding: utf-8 -*-
{
    'name': 'Smart Reconcile - Payment & Invoice Auto-Matcher',
    'version': '20.0.2.0.7',
    'category': 'Accounting',
    'sequence': 1,
    'author': 'ScopySoft',
    'website': 'https://apps.odoo.com/apps/modules/browse?search=scopysoft',
    'support': 'https://apps.odoo.com/apps/modules/browse?search=scopysoft',
    'summary': 'Auto-match payments to invoices and bills: split payments, WHT and bank-charge rules, one-click undo, nightly auto-run.',
    'description': """
Smart Reconcile - Payment & Invoice Auto-Matcher
================================================
Stop reconciling payments to invoices one at a time.

* 1-to-1 matches by amount and invoice reference, reviewed before anything is booked
* Split payments: several payments quoting the same invoice are grouped and reconciled together
  (exact total ticked; partial or over-paid totals left for your review)
* Payments without reference: suggests 2-3 payments that add up exactly to an invoice (low confidence, never automatic)
* Adjustment rules: withholding tax, bank charges and small write-off tolerances booked automatically
* Every row says WHY it matched
* Reconciliation log with one-click undo, and a dashboard
* Optional nightly auto-run (off by default) with an email summary
""",
    'depends': ['account', 'mail'],
    'data': [
        'security/ir.access.csv',
        'wizard/payment_reconciliation_wizard_views.xml',
        'views/recon_views.xml',
        'data/cron.xml',
    ],
    'images': [
        'static/description/banner.png',
        'static/description/icon.png',
        'static/description/step_wizard.png',
        'static/description/step_two_ways.png',
        'static/description/step_confidence.png',
        'static/description/step_splits.png',
        'static/description/step_vendor_bills.png',
        'static/description/step_confirm.png',
        'static/description/step_rules_list.png',
        'static/description/step_rule_form.png',
        'static/description/step_log_list.png',
        'static/description/step_log_form.png',
        'static/description/step_dashboard.png',
        'static/description/step_settings.png',
    ],
    'license': 'OPL-1',
    'currency': 'USD',
    'price': 79.00,
    'application': True,
    'installable': True,
    'auto_install': False,
}

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
