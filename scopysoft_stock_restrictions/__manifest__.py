# -*- coding: utf-8 -*-
{
    'name': 'Stock Restrictions & Controls',
    'version': '16.0.1.0.0',
    'category': 'Inventory',
    'summary': 'Complete stock operation control — block, restrict, and audit everything in your warehouse.',
    'description': """
Stock Restrictions & Controls
==============================
Give your business full control over every stock operation.
Block bad habits, enforce process, and protect your inventory data.

Features:
---------
* Block manual receipts without a Purchase Order
* Block manual deliveries without a Sales Order
* Block over-validation of quantities
* Block under-validation of quantities
* Configurable tolerance % for over/under
* Block zero-quantity validation
* Block negative stock on deliveries
* Block backdating stock operations
* Restrict validation to Inventory Managers only
* Restrict cancellation of validated pickings to managers
* Restrict deletion of draft pickings to managers
* Block receiving from unregistered/unapproved vendors
* Complete audit log of all blocked attempts
* Admin always bypasses ALL restrictions
* Per-company settings
    """,
    'author': 'ScopySoft',
    'support': 'opeyemiajetunmobi9@gmail.com',
    'website': '',
    'depends': ['stock', 'purchase', 'sale_stock'],
    'data': [
        'security/ir.model.access.csv',
        'views/stock_restriction_log_views.xml',
        'views/res_config_settings_views.xml',
    ],
    'images': ['static/description/banner.png'],
    'price': 29.00,
    'currency': 'USD',
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'OPL-1',
}
