# -*- coding: utf-8 -*-
{
    'name': 'Payment Reconciliation Auto-Matcher',
    'version': '18.0.1.0',
    'category': 'Accounting',
    'sequence': 1,
    'author': 'ScopySoft',
    'website': 'https://apps.odoo.com/apps/modules/browse?search=scopysoft',
    'support': 'https://apps.odoo.com/apps/modules/browse?search=scopysoft',
    'summary': 'Auto-match unreconciled payments to open invoices/bills by amount and reference, review, then bulk reconcile.',
    'description': """
Payment Reconciliation Auto-Matcher
====================================
Stop reconciling payments to invoices one at a time.

This tool scans your unreconciled customer payments and vendor payments,
compares them against open invoices and bills for the same partner, and
suggests matches based on:

* Exact amount match + invoice reference found in the payment memo (highest confidence)
* Exact amount match only
* Reference match only (lower confidence, for manual review)

Review the suggested matches, untick anything you are not sure about, and
reconcile everything you confirm in a single click. Anything the tool is
not confident about is left for you to handle manually instead of being
guessed at.

Filter by journal, partner, or date range to scope a single reconciliation run.
""",
    'depends': ['account'],
    'data': [
        'security/ir.model.access.csv',
        'wizard/payment_reconciliation_wizard_views.xml',
    ],
    'license': 'OPL-1',
    'currency': 'USD',
    'price': 29.00,
    'application': True,
    'installable': True,
    'auto_install': False,
}

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
