# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # ── RECEIPTS ─────────────────────────────────────────────────────────────
    sr_block_manual_receipt = fields.Boolean(
        string='Block manual receipts without a Purchase Order',
        help='Prevent users from creating receipts that are not linked to a Purchase Order.',
        config_parameter='scopysoft_stock_restrictions.block_manual_receipt',
    )

    # ── DELIVERIES ────────────────────────────────────────────────────────────
    sr_block_manual_delivery = fields.Boolean(
        string='Block manual deliveries without a Sales Order',
        help='Prevent users from creating deliveries that are not linked to a Sales Order.',
        config_parameter='scopysoft_stock_restrictions.block_manual_delivery',
    )

    # ── QUANTITY CONTROLS ─────────────────────────────────────────────────────
    sr_block_over_validation = fields.Boolean(
        string='Block over-validation of quantities',
        help='Prevent validating more than the demanded quantity.',
        config_parameter='scopysoft_stock_restrictions.block_over_validation',
    )
    sr_block_under_validation = fields.Boolean(
        string='Block under-validation of quantities',
        help='Prevent validating less than the demanded quantity.',
        config_parameter='scopysoft_stock_restrictions.block_under_validation',
    )
    sr_validation_tolerance = fields.Float(
        string='Quantity tolerance (%)',
        help='Allow this percentage over or under the demanded quantity. 0 = strict. Example: 5 means ±5% is allowed.',
        config_parameter='scopysoft_stock_restrictions.validation_tolerance',
    )
    sr_block_zero_qty = fields.Boolean(
        string='Block zero-quantity validation',
        help='Prevent validating stock operations where done quantity is zero.',
        config_parameter='scopysoft_stock_restrictions.block_zero_qty',
    )

    # ── STOCK CONTROLS ────────────────────────────────────────────────────────
    sr_block_negative_stock = fields.Boolean(
        string='Block negative stock on deliveries',
        help='Prevent deliveries that would result in stock going below zero.',
        config_parameter='scopysoft_stock_restrictions.block_negative_stock',
    )
    sr_block_backdate = fields.Boolean(
        string='Block backdating stock operations',
        help='Prevent setting a scheduled date in the past on stock operations.',
        config_parameter='scopysoft_stock_restrictions.block_backdate',
    )

    # ── USER CONTROLS ─────────────────────────────────────────────────────────
    sr_managers_only_validate = fields.Boolean(
        string='Only Inventory Managers can validate operations',
        help='Regular users cannot click Validate — only Inventory Managers and Administrators.',
        config_parameter='scopysoft_stock_restrictions.managers_only_validate',
    )
    sr_managers_only_cancel = fields.Boolean(
        string='Only Inventory Managers can cancel validated operations',
        help='Prevent regular users from cancelling stock operations that have already been validated.',
        config_parameter='scopysoft_stock_restrictions.managers_only_cancel',
    )
    sr_managers_only_delete = fields.Boolean(
        string='Only Inventory Managers can delete draft operations',
        help='Prevent regular users from deleting draft stock operations.',
        config_parameter='scopysoft_stock_restrictions.managers_only_delete',
    )

    # ── VENDOR CONTROLS ───────────────────────────────────────────────────────
    sr_block_unregistered_vendor = fields.Boolean(
        string='Block receiving from unregistered vendors',
        help='Prevent validating receipts from vendors not on the product approved vendor list.',
        config_parameter='scopysoft_stock_restrictions.block_unregistered_vendor',
    )

    # ── AUDIT ─────────────────────────────────────────────────────────────────
    sr_enable_audit_log = fields.Boolean(
        string='Enable restriction audit log',
        help='Record every blocked attempt and admin bypass in the audit log.',
        config_parameter='scopysoft_stock_restrictions.enable_audit_log',
    )
