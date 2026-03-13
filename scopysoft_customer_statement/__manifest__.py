# -*- coding: utf-8 -*-
{
    'name': 'Customer Statement',
    'version': '16.0.1.0.0',
    'category': 'Accounting',
    'summary': 'Generate and email professional customer account statements in one click.',
    'description': """
Customer Statement
==================
Generate professional PDF account statements for any customer in one click.

Features:
---------
* One-click PDF statement from the customer form
* Filter by: All Invoices, Unpaid Only, Paid Only
* Custom date range selection
* Shows all invoices, payments, and running balance
* Total amount due clearly displayed
* Send directly by email to the customer
* Company logo and branding on every statement
* Works with multi-company setups
    """,
    'author': 'ScopySoft',
    'support': 'opeyemiajetunmobi9@gmail.com',
    'website': '',
    'depends': ['account'],
    'data': [
        'security/ir.model.access.csv',
        'wizard/statement_wizard_views.xml',
        'report/paperformat.xml',
        'report/statement_report.xml',
        'report/statement_template.xml',
        'views/partner_views.xml',
        'report/set_paperformat.xml',
    ],
    'images': ['static/description/banner.png'],
    'price': 29.00,
    'currency': 'USD',
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'OPL-1',
}
