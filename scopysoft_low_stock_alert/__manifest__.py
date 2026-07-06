# -*- coding: utf-8 -*-
{
    'name': 'Product Low Stock Alert',
    'version': '16.0.2.0.0',
    'category': 'Inventory',
    'summary': 'Visual low stock warnings on product forms — know before you run out.',
    'description': """
Get warned before you run out of stock - on the product form, at the point of sale, and in a daily digest email.

Features:
* Visual low stock banner on the product form as soon as on-hand quantity hits your threshold
* Warning shown when adding a low-stock (or about-to-be-low-stock) product to a sales order
* Dedicated Low Stock Products list to see everything under threshold at a glance
* Optional daily digest email summarizing every low-stock product, sent to whoever you choose
* Per-product threshold - set it to 0 to disable the alert for that product
* Works on both storable and consumable products

Free to use. No configuration required beyond setting a threshold on each product you want to monitor.
    """,
    'author': 'ScopySoft',
    'website': 'https://apps.odoo.com/apps/modules?author=ScopySoft',
    'depends': ['stock', 'sale'],
    'data': [
        'security/ir.model.access.csv',
        'views/product_views.xml',
        'views/low_stock_views.xml',
        'views/sale_order_views.xml',
        'views/res_config_settings_views.xml',
        'data/low_stock_cron.xml',
    ],
    'images': ['static/description/banner.png'],
    'price': 0.0,
    'currency': 'USD',
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}
