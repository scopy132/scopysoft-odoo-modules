# -*- coding: utf-8 -*-
{
    'name': 'Product Price Tracking',
    'version': '17.0.1.0.0',
    'category': 'Sales',
    'summary': 'Full audit trail for every product sales price change — who, when, why.',
    'description': """
Product Price Tracking
======================
This module tracks all changes made to the product sales price field including:
- Manual changes by users
- Automated changes by scheduled actions
- Changes made by other modules/applications
- Import operations
- Any other price modifications

Features:
---------
* Complete audit trail of all sales price changes
* Track old price, new price, and percentage difference
* Record user, origin, date/time of change
* Accessible from Sales > Configuration > Product Price Tracking
* Accessible from Inventory > Configuration > Product Price Tracking
* Filter and group by product, user, company, date
* Export capabilities
* Works with product templates and variants
    """,
    'author': 'ScopySoft',
    'support': 'opeyemiajetunmobi9@gmail.com',
    'website': '',
    'live_test_url': '',
    'depends': ['product', 'stock', 'sale'],
    'data': [
        'security/ir.model.access.csv',
        'views/product_price_tracking_views.xml',
        'views/product_views.xml',
    ],
    'images': ['static/description/banner.png'],
    'price': 29.00,
    'currency': 'USD',
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'OPL-1',
}
