# -*- coding: utf-8 -*-
{
    'name': 'SVL Fixer',
    'version': '17.0.1.0.0',
    'category': 'Inventory',
    'summary': 'Fix incorrect stock valuation layer quantities from stock moves',
    'description': """
Fix incorrect SVL quantities and values caused by cost price changes or moves out of sync with their valuation layers.

Features:
* Dry run mode to preview changes before applying - nothing is touched until you say so
* Real backup/restore audit trail (not just a raw table - browse and restore from the UI)
* Choose your cost source: realign to the product's current cost, or type in a specific corrected value
* Date range filter to target a specific period
* Minimum unit cost change threshold to skip rounding-level noise on large catalogs
* CSV export of every run for your records
* Batch processing for large datasets
* Transaction rollback on errors
* MULTIPLE PRODUCTS at once, or process all products
* Detailed change tracking per product

Always run in Dry Run mode first. Always take a full database backup before applying changes on a production
database. Recommended for use by Odoo administrators only.
    """,
    'author': 'ScopySoft',
    'website': 'https://apps.odoo.com/apps/modules?author=ScopySoft',
    'images': ['static/description/banner.png'],
    'depends': ['stock_account'],
    'data': [
        'security/ir.model.access.csv',
        'data/svl_fixer_backup_sequence.xml',
        'wizard/svl_fixer_wizard_views.xml',
        'views/svl_fixer_backup_views.xml',
    ],
    'price': 35.00,
    'currency': 'USD',
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'OPL-1',
}