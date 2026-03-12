# -*- coding: utf-8 -*-
{
    'name': 'Vendor Price History',
    'version': '16.0.1.0.0',
    'category': 'Purchase',
    'summary': 'Full audit trail for every vendor price change — who, when, how much.',
    'description': """
Vendor Price History
====================
Automatically tracks every change made to vendor pricelists (supplierinfo) including:
- Manual changes by users
- Price updates from purchase orders
- Import operations
- Any other vendor price modifications

Features:
---------
* Complete audit trail of all vendor price changes
* Track old price, new price, and percentage difference
* Record vendor, product, user, date/time of change
* Accessible from Purchase > Configuration > Vendor Price History
* Filter and group by vendor, product, user, date
* Export capabilities
    """,
    'author': 'ScopySoft',
    'support': 'opeyemiajetunmobi9@gmail.com',
    'website': '',
    'depends': ['purchase'],
    'data': [
        'security/ir.model.access.csv',
        'views/vendor_price_history_views.xml',
        'views/product_views.xml',
    ],
    'images': ['static/description/banner.png'],
    'price': 0.0,
    'currency': 'USD',
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}
