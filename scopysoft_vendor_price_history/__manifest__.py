# -*- coding: utf-8 -*-
{
    'name': 'Vendor Price History',
    'version': '18.0.1.0.4',
    'category': 'Purchase',
    'summary': 'Full audit trail for every vendor price change — who, when, how much, and why.',
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
* Optional reason and auto-detected change source (manual, PO, import)
* Configurable alert threshold with automatic activity notifications on big jumps
* Pivot and graph views to visualize price trends over time
* Vendor Price Comparison view — see every vendor for a product side by
  side, with the cheapest one highlighted and % difference from cheapest
* Last Purchased Price comparison shown directly on the product form
* Accessible from Purchase > Configuration > Vendor Price History
* Filter and group by vendor, product, user, reason, source, date
* Export capabilities
    """,
    'author': 'ScopySoft',
    'website': 'https://apps.odoo.com/apps/modules?author=ScopySoft',
    'depends': ['purchase'],
    'data': [
        'security/ir.model.access.csv',
        'views/vendor_price_history_views.xml',
        'views/vendor_price_comparison_views.xml',
        'views/product_views.xml',
        'views/res_config_settings_views.xml',
    ],
    'price': 8.0,
    'currency': 'USD',
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'OPL-1',
    'images': ['static/description/banner.png'],
}
