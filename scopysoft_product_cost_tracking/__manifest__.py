# -*- coding: utf-8 -*-
{
    'name': 'Product Cost Tracking',
    'version': '18.0.1.0.0',
    'category': 'Inventory',
    'summary': 'Full audit trail for every product cost change — who, when, why.',
    'description': """
Product Cost Tracking
=====================
This module tracks all changes made to the product cost field including:
- Manual changes by users
- Automated changes by scheduled actions
- Changes made by other modules/applications
- Import operations
- Any other cost modifications

Features:
---------
* Complete audit trail of all cost changes
* Track old cost, new cost, and percentage difference
* Record user, origin, date/time of change
* Cost History tab and smart button right on the product form
* Accessible from Inventory > Configuration > Product Cost Tracking
* Filter and group by product, user, company, date
* Works with product templates and variants
    """,
    'author': 'ScopySoft',
    'website': 'https://apps.odoo.com/apps/modules?author=ScopySoft',
    'depends': ['product', 'stock'],
    'data': [
        'security/ir.model.access.csv',
        'views/product_cost_tracking_views.xml',
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
