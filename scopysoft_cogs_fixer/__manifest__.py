# -*- coding: utf-8 -*-
{
    'name': 'COGS Fixer',
    'version': '16.0.1.0.0',
    'category': 'Inventory/Accounting',
    'summary': 'Fix missing or incorrect journal entries for stock valuation layers',
    'description': """
Fix missing or incorrect accounting journal entries for stock valuation layers.

Features:
* Dry run mode to preview changes before applying - nothing is touched until you say so
* Full change log for every run - browse exactly what was created or updated
* Date range filter to target a specific period
* CSV export of every run for your records
* Batch processing with progress tracking
* Transaction rollback on errors
* Automatic journal entry creation and correction
* Detailed logging and error reporting
* Time tracking for execution

This module posts and modifies real accounting journal entries. Always run in Dry Run mode first, and always
test on a duplicate database before applying changes to production. Recommended for use by Odoo administrators only.
    """,
    'author': 'ScopySoft',
    'website': 'https://apps.odoo.com/apps/modules?author=ScopySoft',
    'images': ['static/description/banner.png'],
    'depends': ['stock_account'],
    'data': [
        'security/ir.model.access.csv',
        'data/cogs_fixer_backup_sequence.xml',
        'wizard/cogs_fixer_wizard_views.xml',
        'views/cogs_fixer_backup_views.xml',
    ],
    'price': 35.00,
    'currency': 'USD',
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'OPL-1',
}