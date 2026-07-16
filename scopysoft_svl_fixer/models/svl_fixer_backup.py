# -*- coding: utf-8 -*-
from odoo import models, fields, api


class SvlFixerBackup(models.Model):
    """One record per fixer run. Holds the run-level metadata, with the
    individual pre-change SVL snapshots stored on svl.fixer.backup.line.
    This replaces the old raw SQL backup table so that backups are a real
    Odoo model: queryable, permission-controlled, and restorable from the UI.
    """
    _name = 'svl.fixer.backup'
    _description = 'SVL Fixer Backup Run'
    _order = 'create_date desc'

    name = fields.Char(
        string='Reference',
        required=True,
        default=lambda self: self.env['ir.sequence'].next_by_code('svl.fixer.backup') or 'New'
    )
    run_date = fields.Datetime(
        string='Run Date',
        default=fields.Datetime.now,
        required=True
    )
    user_id = fields.Many2one(
        'res.users',
        string='Run By',
        default=lambda self: self.env.user
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company
    )
    product_count = fields.Integer(string='Products Affected')
    line_count = fields.Integer(string='SVL Lines Backed Up')
    restored = fields.Boolean(string='Restored', default=False, readonly=True)
    restored_date = fields.Datetime(string='Restored On', readonly=True)
    notes = fields.Text(string='Notes')
    line_ids = fields.One2many(
        'svl.fixer.backup.line',
        'backup_id',
        string='Backed Up Lines'
    )

    def action_restore(self):
        """Restore all SVL values captured in this backup run."""
        self.ensure_one()
        if self.restored:
            from odoo.exceptions import UserError
            raise UserError('This backup has already been restored.')

        restored_count = 0
        missing_count = 0
        for line in self.line_ids:
            svl = self.env['stock.valuation.layer'].browse(line.svl_id)
            if not svl.exists():
                missing_count += 1
                continue
            svl.sudo().with_context(
                check_move_validity=False,
                disable_svls=True
            ).write({
                'quantity': line.quantity,
                'unit_cost': line.unit_cost,
                'value': line.value,
                'remaining_qty': line.remaining_qty,
                'remaining_value': line.remaining_value,
            })
            restored_count += 1

        self.write({
            'restored': True,
            'restored_date': fields.Datetime.now(),
            'notes': (self.notes or '') + (
                f"\nRestored {restored_count} line(s) on {fields.Datetime.now()}"
                f"{f' ({missing_count} SVL(s) no longer exist and were skipped)' if missing_count else ''}."
            )
        })
        return True


class SvlFixerBackupLine(models.Model):
    """Snapshot of a single SVL record's values before the fixer changed them."""
    _name = 'svl.fixer.backup.line'
    _description = 'SVL Fixer Backup Line'

    backup_id = fields.Many2one(
        'svl.fixer.backup',
        string='Backup Run',
        required=True,
        ondelete='cascade'
    )
    svl_id = fields.Integer(string='Original SVL ID', required=True, index=True)
    product_id = fields.Many2one('product.product', string='Product')
    stock_move_id = fields.Integer(string='Stock Move ID')
    quantity = fields.Float(string='Quantity (before)')
    unit_cost = fields.Float(string='Unit Cost (before)')
    value = fields.Float(string='Value (before)')
    remaining_qty = fields.Float(string='Remaining Qty (before)')
    remaining_value = fields.Float(string='Remaining Value (before)')
    description = fields.Char(string='Description')
    svl_create_date = fields.Datetime(string='SVL Create Date')
