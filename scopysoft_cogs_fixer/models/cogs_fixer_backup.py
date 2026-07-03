# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import UserError


class CogsFixerBackup(models.Model):
    """One record per fixer run. Holds the run-level metadata, with the
    individual pre-change journal entry line snapshots stored on
    cogs.fixer.backup.line. This is a LOG ONLY for now - it captures exactly
    what each run touched, but does not expose a restore action. Users are
    expected to test in a duplicate database first per the module's
    disclaimer; restore can be re-enabled later (see action_restore below,
    intentionally left in place but disconnected from any button) without
    a data migration, since the snapshot data is already being captured.
    """
    _name = 'cogs.fixer.backup'
    _description = 'COGS Fixer Backup Run'
    _order = 'create_date desc'

    name = fields.Char(
        string='Reference',
        required=True,
        default=lambda self: self.env['ir.sequence'].next_by_code('cogs.fixer.backup') or 'New'
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
    product_id = fields.Many2one('product.product', string='Product')
    svl_count = fields.Integer(string='SVLs Touched')
    line_count = fields.Integer(string='Journal Lines Backed Up')
    restored = fields.Boolean(string='Restored', default=False, readonly=True)
    restored_date = fields.Datetime(string='Restored On', readonly=True)
    notes = fields.Text(string='Notes')
    line_ids = fields.One2many(
        'cogs.fixer.backup.line',
        'backup_id',
        string='Backed Up Journal Lines'
    )

    # NOTE: Restore is intentionally not exposed via any button/view right now.
    # This is a log-only backup - users are expected to test in a duplicate
    # database first. The method below is kept ready so it can be re-enabled
    # later (wire a button to it + add to the views) without needing a data
    # migration, since every existing backup record already has everything
    # this method needs.
    #
    # def action_restore(self):
    #     """Restore the journal entries touched by this run back to their
    #     pre-fix state. Entries that were freshly created by the fixer (no
    #     prior move existed) are unposted and deleted, since there is nothing
    #     to roll back TO. Entries that existed before are reset to draft,
    #     their current lines wiped, and the backed-up lines recreated."""
    #     self.ensure_one()
    #     if self.restored:
    #         raise UserError('This backup has already been restored.')
    #
    #     restored_count = 0
    #     deleted_count = 0
    #     missing_count = 0
    #
    #     # Group backup lines by the move they belong to
    #     moves_seen = {}
    #     for line in self.line_ids:
    #         moves_seen.setdefault(line.account_move_id, []).append(line)
    #
    #     for move_id, lines in moves_seen.items():
    #         move = self.env['account.move'].browse(move_id)
    #         had_prior_lines = any(l.was_pre_existing for l in lines)
    #
    #         if not had_prior_lines:
    #             # The fixer created this move from nothing - undo by removing it
    #             if move.exists():
    #                 try:
    #                     if move.state == 'posted':
    #                         move.button_draft()
    #                     move.unlink()
    #                     deleted_count += 1
    #                 except Exception:
    #                     missing_count += 1
    #             else:
    #                 missing_count += 1
    #             continue
    #
    #         if not move.exists():
    #             missing_count += 1
    #             continue
    #
    #         try:
    #             if move.state == 'posted':
    #                 move.button_draft()
    #             move.line_ids.unlink()
    #             move.write({
    #                 'line_ids': [
    #                     (0, 0, {
    #                         'name': l.line_name,
    #                         'account_id': l.account_id.id,
    #                         'debit': l.debit,
    #                         'credit': l.credit,
    #                         'product_id': l.product_id.id if l.product_id else False,
    #                     }) for l in lines
    #                 ]
    #             })
    #             move.action_post()
    #             restored_count += 1
    #         except Exception:
    #             missing_count += 1
    #
    #     self.write({
    #         'restored': True,
    #         'restored_date': fields.Datetime.now(),
    #         'notes': (self.notes or '') + (
    #             f"\nRestored on {fields.Datetime.now()}: "
    #             f"{restored_count} move(s) reset to prior state, "
    #             f"{deleted_count} fixer-created move(s) removed"
    #             f"{f', {missing_count} could not be restored' if missing_count else ''}."
    #         )
    #     })
    #     return True


class CogsFixerBackupLine(models.Model):
    """Snapshot of a single journal entry line's values before the fixer
    deleted/rewrote it. was_pre_existing distinguishes 'this line existed
    before the fixer touched the move' from 'the fixer created this move
    and this is just a record of its first-ever state', which matters for
    deciding how to restore."""
    _name = 'cogs.fixer.backup.line'
    _description = 'COGS Fixer Backup Line'

    backup_id = fields.Many2one(
        'cogs.fixer.backup',
        string='Backup Run',
        required=True,
        ondelete='cascade'
    )
    account_move_id = fields.Integer(string='Journal Entry ID', required=True, index=True)
    svl_id = fields.Integer(string='Related SVL ID', index=True)
    product_id = fields.Many2one('product.product', string='Product')
    account_id = fields.Many2one('account.account', string='Account (before)')
    line_name = fields.Char(string='Line Description (before)')
    debit = fields.Float(string='Debit (before)')
    credit = fields.Float(string='Credit (before)')
    was_pre_existing = fields.Boolean(
        string='Move Existed Before Fixer Ran',
        default=True,
        help='False if the fixer created this journal entry from nothing - in that '
             'case there is no prior state to restore, only the option to remove it.'
    )
