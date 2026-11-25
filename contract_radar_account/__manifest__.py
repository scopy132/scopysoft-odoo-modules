# -*- coding: utf-8 -*-
{
    'name': 'Contract Guard - Vendor Bill Matching',
    'version': '19.0.1.0.0',
    'category': 'Productivity',
    'summary': 'Flag vendor bills that exceed the agreed contract price, silent price increases and missing bills',
    'description': """
Contract Guard - Vendor Bill Matching
=====================================
Bridge between Contract Renewal & Cancellation Guard and Accounting. It compares the posted vendor
bills of each counterparty with the projected payments of the contract and flags:

* bills above the agreed price (overcharges and price increases nobody announced),
* bills below it,
* periods with no bill at all.

It only reads accounting data: it never creates, edits or posts an accounting entry.
Installs automatically when both apps are present.
    """,
    'author': 'ScopySoft',
    'images': ['static/description/banner.png'],
    'price': 0.00,
    'currency': 'USD',
    'license': 'OPL-1',
    'depends': ['contract_radar', 'account'],
    'data': [
        'views/contract_radar_views.xml',
    ],
    'auto_install': True,
    'post_init_hook': 'post_init',
    'installable': True,
}
