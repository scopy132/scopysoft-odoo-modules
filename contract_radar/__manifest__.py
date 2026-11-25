# -*- coding: utf-8 -*-
{
    'name': 'Contract Renewal & Cancellation Guard',
    'version': '18.0.1.1.1',
    'category': 'Productivity',
    'summary': 'Never pay for an auto-renewal you meant to cancel: cancel-by deadlines, notice letters, renewal approvals and savings tracking',
    'description': """
Contract Renewal & Cancellation Guard
=====================================
Track vendor, customer, lease, warranty, license, NDA and service contracts.
The headline date is the last day to cancel (end date minus notice period,
moved earlier when it falls on a weekend or public holiday), with multi-stage
reminders, one-click notice letters, renewal decisions with manager approval,
a savings counter, price escalation, renewal chain, amendments and a 12-month
commitment forecast.
    """,
    'author': 'ScopySoft',
    'images': ['static/description/banner.png'],
    'price': 79.00,
    'currency': 'USD',
    'license': 'OPL-1',
    'depends': ['mail', 'analytic'],
    'data': [
        'security/contract_security.xml',
        'security/ir.model.access.csv',
        'security/ir_rules.xml',
        'data/ir_sequence_data.xml',
        'data/ir_cron_data.xml',
        'views/contract_radar_views.xml',
        'views/contract_escalation_views.xml',
        'views/contract_cost_line_views.xml',
        'views/contract_reminder_log_views.xml',
        'views/contract_webhook_views.xml',
        'views/contract_notice_wizard_views.xml',
        'views/dashboard_views.xml',
        'report/contract_report.xml',
        'report/contract_report_templates.xml',
        'report/contract_notice_report.xml',
        'views/menus.xml',
    ],
    'demo': ['demo/contract_demo.xml'],
    'assets': {
        'web.assets_backend': [
            'contract_radar/static/src/**/*',
        ],
    },
    'application': True,
    'installable': True,
}
