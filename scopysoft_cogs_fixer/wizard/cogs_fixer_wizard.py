# -*- coding: utf-8 -*-
import base64
import csv
import io
from datetime import datetime, timedelta

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
import logging

_logger = logging.getLogger(__name__)


class CogsFixerWizard(models.TransientModel):
    _name = 'cogs.fixer.wizard'
    _description = 'COGS Fixer Wizard'

    product_id = fields.Many2one(
        'product.product',
        string='Product',
        required=True,
        help='Select the product to fix journal entries for'
    )
    date_from = fields.Date(
        string='From Date',
        help='Only consider SVLs created on or after this date. Leave empty for no lower bound.'
    )
    date_to = fields.Date(
        string='To Date',
        help='Only consider SVLs created on or before this date. Leave empty for no upper bound.'
    )
    dry_run = fields.Boolean(
        string='Dry Run (Preview Only)',
        default=True,
        help='If checked, no changes will be made. Only a preview will be shown.'
    )
    batch_size = fields.Integer(
        string='Batch Size',
        default=100,
        required=True,
        help='Number of records to process before committing'
    )
    create_backup = fields.Boolean(
        string='Create Backup',
        default=True,
        help='Log the pre-change journal entry values before making changes, for your own records'
    )
    result_message = fields.Html(
        string='Results',
        readonly=True
    )
    progress_log = fields.Text(
        string='Progress Log',
        readonly=True
    )
    csv_export = fields.Binary(
        string='CSV Export',
        readonly=True
    )
    csv_export_filename = fields.Char(
        string='CSV Filename',
        readonly=True
    )
    backup_id = fields.Many2one(
        'cogs.fixer.backup',
        string='Backup Created',
        readonly=True
    )
    state = fields.Selection([
        ('draft', 'Draft'),
        ('processing', 'Processing'),
        ('done', 'Done'),
    ], default='draft')

    @api.constrains('batch_size')
    def _check_batch_size(self):
        for record in self:
            if record.batch_size < 1:
                raise ValidationError(_('Batch size must be at least 1'))

    @api.constrains('date_from', 'date_to')
    def _check_date_range(self):
        for record in self:
            if record.date_from and record.date_to and record.date_from > record.date_to:
                raise ValidationError(_('"From Date" cannot be after "To Date"'))

    def _log_progress(self, message):
        """Add a timestamped message to the progress log"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        current_log = self.progress_log or ""
        new_log = f"[{timestamp}] {message}\n{current_log}"
        self.write({'progress_log': new_log})
        _logger.info(message)

    def _get_stock_journal(self, product):
        """Find the journal entries should post to for this product, using the
        actual configured field rather than guessing by journal name - a hardcoded
        name match breaks the moment someone renames or translates their journal."""
        categ = product.categ_id
        journal = categ.property_stock_journal
        if journal:
            return journal
        journal = self.env['account.journal'].search([
            ('type', '=', 'general'),
            ('name', 'ilike', 'inventory'),
        ], limit=1)
        if journal:
            return journal
        return False

    def _get_or_create_backup_run(self, backup_run, product):
        if backup_run:
            return backup_run
        backup_run = self.env['cogs.fixer.backup'].create({
            'product_id': product.id,
            'notes': f"Created by COGS Fixer wizard run on {fields.Datetime.now()}"
                     f"{' (DRY RUN)' if self.dry_run else ''}.",
        })
        return backup_run

    def _backup_move_lines(self, backup_run, product, svl, move, was_pre_existing):
        """Snapshot a move's current lines before they get unlinked/rewritten,
        or record that a move did not exist at all if the fixer is about to create one."""
        try:
            backup_run = self._get_or_create_backup_run(backup_run, product)
            if was_pre_existing and move:
                for line in move.line_ids:
                    self.env['cogs.fixer.backup.line'].create({
                        'backup_id': backup_run.id,
                        'account_move_id': move.id,
                        'svl_id': svl.id,
                        'product_id': product.id,
                        'account_id': line.account_id.id,
                        'line_name': line.name,
                        'debit': line.debit,
                        'credit': line.credit,
                        'was_pre_existing': True,
                    })
            else:
                self.env['cogs.fixer.backup.line'].create({
                    'backup_id': backup_run.id,
                    'account_move_id': 0,
                    'svl_id': svl.id,
                    'product_id': product.id,
                    'line_name': 'No prior journal entry existed for this SVL',
                    'was_pre_existing': False,
                })
            return backup_run, True, None
        except Exception as e:
            return backup_run, False, str(e)

    def action_run_fixer(self):
        """Main method to run the COGS journal fixer"""
        self.ensure_one()

        self.write({'state': 'processing', 'progress_log': ''})

        start_time = datetime.now()
        self._log_progress('COGS Fixer Started')

        product = self.product_id
        dry_run = self.dry_run
        batch_size = self.batch_size
        backup_run = False
        csv_rows = []

        try:
            self._log_progress(f'Product: {product.name} (ID: {product.id})')
            self._log_progress(f'Mode: {"DRY RUN (Preview Only)" if dry_run else "LIVE MODE (Making Changes)"}')
            self._log_progress(f'Batch Size: {batch_size}')
            if self.date_from or self.date_to:
                self._log_progress(f'Date Range: {self.date_from or "any"} to {self.date_to or "any"}')

            valuation_acc = product.categ_id.property_stock_valuation_account_id
            interim_acc = product.categ_id.property_stock_account_output_categ_id

            if not valuation_acc or not interim_acc:
                self._log_progress('ERROR: Missing accounts!')
                raise UserError(_(
                    'Missing valuation or interim accounts for product category: %s'
                ) % product.categ_id.name)

            self._log_progress(f'Valuation Account: {valuation_acc.code} - {valuation_acc.name}')
            self._log_progress(f'Interim Account: {interim_acc.code} - {interim_acc.name}')

            stock_journal = self._get_stock_journal(product)
            if not stock_journal:
                self._log_progress('ERROR: No stock journal found!')
                raise UserError(_(
                    'No journal configured for posting stock valuation entries. '
                    'Set "Stock Journal" on the product category (%s) under Account Stock Properties.'
                ) % product.categ_id.name)

            self._log_progress(f'Using Journal: {stock_journal.name}')

            svl_domain = [('product_id', '=', product.id)]
            if self.date_from:
                svl_domain.append(('create_date', '>=', self.date_from))
            if self.date_to:
                svl_domain.append(('create_date', '<', fields.Date.to_string(
                    fields.Date.from_string(self.date_to) + timedelta(days=1)
                )))
            svls = self.env['stock.valuation.layer'].search(svl_domain)

            total_svls = len(svls)
            self._log_progress(f'Found {total_svls} SVL records to process')
            self._log_progress('---')

            move_id_seen = {}
            for svl in svls:
                if svl.account_move_id:
                    move_id_seen.setdefault(svl.account_move_id.id, []).append(svl.id)
            shared_move_svl_ids = set()
            for move_id, svl_ids in move_id_seen.items():
                if len(svl_ids) > 1:
                    shared_move_svl_ids.update(svl_ids[1:])
                    self._log_progress(
                        f'WARNING: Journal entry {move_id} is shared by {len(svl_ids)} SVLs '
                        f'(IDs: {svl_ids}). Only the first will be fixed to avoid overwriting it twice.'
                    )

            count_updated = 0
            count_created = 0
            count_skipped = 0
            count_already_correct = 0
            errors = []

            for idx, svl in enumerate(svls, 1):
                try:
                    if idx % batch_size == 0:
                        progress_pct = (idx / total_svls) * 100
                        self._log_progress(
                            f'Progress: {idx}/{total_svls} ({progress_pct:.1f}%) | '
                            f'Updated: {count_updated} | Created: {count_created} | '
                            f'Correct: {count_already_correct} | Skipped: {count_skipped}'
                        )
                        if not dry_run:
                            self.env.cr.commit()

                    if svl.id in shared_move_svl_ids:
                        count_skipped += 1
                        continue

                    if svl.account_move_id:
                        move = svl.account_move_id
                        amount = abs(svl.value)

                        if move.state == 'posted' and len(move.line_ids) == 2:
                            lines_correct = True
                            for line in move.line_ids:
                                if line.account_id == valuation_acc:
                                    if svl.value > 0:
                                        if line.debit != amount or line.credit != 0:
                                            lines_correct = False
                                    else:
                                        if line.credit != amount or line.debit != 0:
                                            lines_correct = False
                                elif line.account_id == interim_acc:
                                    if svl.value > 0:
                                        if line.credit != amount or line.debit != 0:
                                            lines_correct = False
                                    else:
                                        if line.debit != amount or line.credit != 0:
                                            lines_correct = False

                            if lines_correct:
                                count_already_correct += 1
                                continue

                        if not dry_run and self.create_backup:
                            backup_run, backup_ok, backup_err = self._backup_move_lines(
                                backup_run, product, svl, move, was_pre_existing=True
                            )
                            if not backup_ok:
                                _logger.warning('Backup failed for move %s: %s', move.id, backup_err)

                        old_lines_desc = '; '.join(
                            f"{l.account_id.code}: D{l.debit:.2f}/C{l.credit:.2f}" for l in move.line_ids
                        )

                        if move.state == 'posted':
                            try:
                                if not dry_run:
                                    move.button_draft()
                            except Exception as e:
                                count_skipped += 1
                                skip_reason = f"SVL {svl.id} (move {move.id}): locked/reconciled, can't reset to draft - {str(e)[:150]}"
                                errors.append(skip_reason)
                                if len(errors) <= 20:
                                    _logger.warning(skip_reason)
                                continue

                        if not dry_run:
                            move.line_ids.unlink()

                        if svl.value > 0:
                            valuation_debit, valuation_credit = amount, 0.0
                            interim_debit, interim_credit = 0.0, amount
                        else:
                            valuation_debit, valuation_credit = 0.0, amount
                            interim_debit, interim_credit = amount, 0.0

                        if not dry_run:
                            move.write({
                                'line_ids': [
                                    (0, 0, {
                                        'name': product.name,
                                        'account_id': valuation_acc.id,
                                        'debit': valuation_debit,
                                        'credit': valuation_credit,
                                        'product_id': product.id,
                                    }),
                                    (0, 0, {
                                        'name': product.name,
                                        'account_id': interim_acc.id,
                                        'debit': interim_debit,
                                        'credit': interim_credit,
                                        'product_id': product.id,
                                    }),
                                ]
                            })
                            move.action_post()

                        count_updated += 1
                        csv_rows.append({
                            'svl_id': svl.id,
                            'action': 'Updated',
                            'move_id': move.id,
                            'old_lines': old_lines_desc,
                            'new_lines': f"{valuation_acc.code}: D{valuation_debit:.2f}/C{valuation_credit:.2f}; "
                                         f"{interim_acc.code}: D{interim_debit:.2f}/C{interim_credit:.2f}",
                            'svl_date': svl.create_date,
                        })

                    else:
                        if not dry_run and self.create_backup:
                            backup_run, backup_ok, backup_err = self._backup_move_lines(
                                backup_run, product, svl, False, was_pre_existing=False
                            )
                            if not backup_ok:
                                _logger.warning('Backup failed for SVL %s: %s', svl.id, backup_err)

                        move_ref = (
                            svl.reference
                            or (svl.stock_move_id.reference if svl.stock_move_id else None)
                            or (svl.stock_move_id.picking_id.name if svl.stock_move_id and svl.stock_move_id.picking_id else None)
                            or 'Manual SVL Fix'
                        )
                        move_date = svl.create_date.date() if svl.create_date else fields.Date.today()
                        ref_name = f"{move_ref} - {product.name}"
                        amount = abs(svl.value)

                        if svl.value > 0:
                            valuation_debit, valuation_credit = amount, 0.0
                            interim_debit, interim_credit = 0.0, amount
                        else:
                            valuation_debit, valuation_credit = 0.0, amount
                            interim_debit, interim_credit = amount, 0.0

                        new_move_id = False
                        if not dry_run:
                            move_vals = {
                                'journal_id': stock_journal.id,
                                'ref': ref_name,
                                'date': move_date,
                                'line_ids': [
                                    (0, 0, {
                                        'name': product.name,
                                        'account_id': valuation_acc.id,
                                        'debit': valuation_debit,
                                        'credit': valuation_credit,
                                        'product_id': product.id,
                                    }),
                                    (0, 0, {
                                        'name': product.name,
                                        'account_id': interim_acc.id,
                                        'debit': interim_debit,
                                        'credit': interim_credit,
                                        'product_id': product.id,
                                    }),
                                ],
                            }
                            new_move = self.env['account.move'].create(move_vals)
                            new_move.action_post()
                            svl.write({'account_move_id': new_move.id})
                            new_move_id = new_move.id

                        count_created += 1
                        csv_rows.append({
                            'svl_id': svl.id,
                            'action': 'Created',
                            'move_id': new_move_id or '(preview)',
                            'old_lines': '(none)',
                            'new_lines': f"{valuation_acc.code}: D{valuation_debit:.2f}/C{valuation_credit:.2f}; "
                                         f"{interim_acc.code}: D{interim_debit:.2f}/C{interim_credit:.2f}",
                            'svl_date': svl.create_date,
                        })

                    if idx % batch_size == 0 and not dry_run:
                        self.env.cr.commit()

                except Exception as e:
                    error_msg = f"Failed SVL {svl.id}: {str(e)[:200]}"
                    errors.append(error_msg)
                    count_skipped += 1
                    if len(errors) <= 20:
                        _logger.error(error_msg)

            if not dry_run:
                self.env.cr.commit()

            if backup_run:
                backup_run.write({
                    'svl_count': len(set(l.svl_id for l in backup_run.line_ids)),
                    'line_count': len(backup_run.line_ids),
                })

            end_time = datetime.now()
            duration = end_time - start_time
            duration_str = str(duration).split('.')[0]

            self._log_progress('---')
            self._log_progress('PROCESSING COMPLETE')
            self._log_progress(f'Total Time: {duration_str}')
            self._log_progress(
                f'Final Stats: Updated={count_updated}, Created={count_created}, '
                f'Correct={count_already_correct}, Skipped={count_skipped}'
            )
            if dry_run:
                self._log_progress('THIS WAS A DRY RUN - NO CHANGES WERE MADE')
            else:
                self._log_progress('All changes have been committed to database')

            csv_buffer = io.StringIO()
            writer = csv.writer(csv_buffer)
            writer.writerow(['SVL ID', 'Action', 'Journal Entry ID', 'Old Lines', 'New Lines', 'SVL Date'])
            for row in csv_rows:
                svl_date_str = row['svl_date'].strftime('%Y-%m-%d %H:%M:%S') if row['svl_date'] else ''
                writer.writerow([
                    row['svl_id'], row['action'], row['move_id'],
                    row['old_lines'], row['new_lines'], svl_date_str,
                ])
            csv_content = csv_buffer.getvalue()
            csv_filename = f"cogs_fixer_{'dryrun_' if dry_run else ''}{fields.Datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

            dry_run_label = "<strong style='color: orange;'>[DRY RUN - NO CHANGES MADE]</strong>" if dry_run else "<strong style='color: green;'>[LIVE MODE - CHANGES APPLIED]</strong>"
            error_list = "<br/>".join(errors[:5]) if errors else "None"

            date_range_label = ""
            if self.date_from or self.date_to:
                date_range_label = f"<p><strong>Date Range:</strong> {self.date_from or 'any'} to {self.date_to or 'any'}</p>"

            backup_label = ""
            if backup_run:
                backup_label = f"<p><strong>Backup Created:</strong> {backup_run.name} ({backup_run.line_count} line(s)) - browse under COGS Fixer Backups for a full log of what changed</p>"

            shared_move_label = ""
            if shared_move_svl_ids:
                shared_move_label = (
                    f"<p style='color: #b36b00;'><strong>Note:</strong> {len(shared_move_svl_ids)} SVL(s) "
                    f"shared a journal entry with another SVL and were skipped to avoid overwriting a fix "
                    f"already applied in this same run. Check the Progress Log for details.</p>"
                )

            summary = f"""
            <div style="font-family: monospace; padding: 10px;">
                <h3>COGS JOURNAL FIX SUMMARY {dry_run_label}</h3>
                <hr/>
                <p><strong>Product:</strong> {product.name} (ID: {product.id})</p>
                <p><strong>Total SVLs:</strong> {len(svls)}</p>
                <p><strong>Batch Size:</strong> {batch_size}</p>
                <p><strong>Execution Time:</strong> {duration_str}</p>
                {date_range_label}
                {backup_label}
                {shared_move_label}
                <hr/>
                <h4>Results:</h4>
                <table style="width: 100%; border-collapse: collapse;">
                    <tr style="background-color: #e8f5e9;">
                        <td style="padding: 8px; border: 1px solid #ddd;"><strong>Already Correct:</strong></td>
                        <td style="padding: 8px; border: 1px solid #ddd;">{count_already_correct}</td>
                    </tr>
                    <tr style="background-color: #fff3e0;">
                        <td style="padding: 8px; border: 1px solid #ddd;"><strong>Updated:</strong></td>
                        <td style="padding: 8px; border: 1px solid #ddd;">{count_updated}</td>
                    </tr>
                    <tr style="background-color: #e3f2fd;">
                        <td style="padding: 8px; border: 1px solid #ddd;"><strong>Created:</strong></td>
                        <td style="padding: 8px; border: 1px solid #ddd;">{count_created}</td>
                    </tr>
                    <tr style="background-color: #ffebee;">
                        <td style="padding: 8px; border: 1px solid #ddd;"><strong>Skipped/Errors:</strong></td>
                        <td style="padding: 8px; border: 1px solid #ddd;">{count_skipped}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px; border: 1px solid #ddd;"><strong>Total Errors:</strong></td>
                        <td style="padding: 8px; border: 1px solid #ddd;">{len(errors)}</td>
                    </tr>
                </table>
                <hr/>
                <p><strong>First 5 Errors:</strong></p>
                <p style="font-size: 0.9em; color: #d32f2f;">{error_list}</p>
                <hr/>
                {'<div style="background-color: #fff3cd; padding: 15px; border-left: 4px solid #ff9800;"><strong>THIS WAS A DRY RUN - NO CHANGES WERE MADE</strong><br/>Uncheck "Dry Run" and click "Run Fixer" again to apply changes</div>' if dry_run else '<div style="background-color: #d4edda; padding: 15px; border-left: 4px solid #28a745;"><strong>PROCESS COMPLETE</strong><br/>All changes have been successfully applied and committed to the database.</div>'}
            </div>
            """

            self.write({
                'result_message': summary,
                'state': 'done',
                'backup_id': backup_run.id if backup_run else False,
                'csv_export': base64.b64encode(csv_content.encode('utf-8')),
                'csv_export_filename': csv_filename,
            })

            _logger.info('COGS fix completed for product: %s', product.name)

            return {
                'type': 'ir.actions.act_window',
                'res_model': 'cogs.fixer.wizard',
                'view_mode': 'form',
                'res_id': self.id,
                'target': 'new',
            }

        except Exception as e:
            if not dry_run:
                self.env.cr.rollback()
            self._log_progress(f'CRITICAL ERROR: {str(e)}')
            self.write({'state': 'draft'})
            raise UserError(_(f"CRITICAL ERROR: {str(e)}\nAll changes have been rolled back."))

    def action_reset(self):
        """Reset wizard to draft state"""
        self.write({
            'result_message': False,
            'progress_log': False,
            'state': 'draft',
            'csv_export': False,
            'csv_export_filename': False,
            'backup_id': False,
        })

    def action_view_backup(self):
        """Open the backup record created by this run, if any."""
        self.ensure_one()
        if not self.backup_id:
            raise UserError(_('No backup was created for this run.'))
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'cogs.fixer.backup',
            'view_mode': 'form',
            'res_id': self.backup_id.id,
            'target': 'current',
        }
