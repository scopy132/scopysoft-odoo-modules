# -*- coding: utf-8 -*-
import base64
import csv
import io
from datetime import timedelta

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
import logging

_logger = logging.getLogger(__name__)


class SvlFixerWizard(models.TransientModel):
    _name = 'svl.fixer.wizard'
    _description = 'SVL Quantity Fixer Wizard'

    product_tmpl_ids = fields.Many2many(
        'product.template',
        string='Products',
        required=True,
        help='Select one or more products to fix SVL quantities for'
    )
    process_all_products = fields.Boolean(
        string='Process All Products',
        default=False,
        help='Process all products in the system'
    )
    date_from = fields.Date(
        string='From Date',
        help='Only consider SVLs created on or after this date. Leave empty for no lower bound.'
    )
    date_to = fields.Date(
        string='To Date',
        help='Only consider SVLs created on or before this date. Leave empty for no upper bound.'
    )
    min_unit_cost_delta = fields.Float(
        string='Min Unit Cost Change to Report',
        default=0.0,
        digits='Product Price',
        help='Only count/report an SVL as updated if its unit cost is off by at least this amount. '
             'This looks at the per-unit cost gap, not the total value - so the same unit cost mistake '
             'is treated the same whether 1 unit moved or 1,000 did. Useful on large catalogs to filter out '
             'negligible rounding-level differences. Set to 0 to report all changes.'
    )
    cost_source = fields.Selection([
        ('current', 'Use Current Product Cost (standard_price)'),
        ('custom', 'Enter a Custom Cost Manually'),
    ], string='Cost Source', default='current', required=True,
        help='"Current Product Cost" realigns historical SVLs to whatever the product\'s cost field says right now - '
             'use this when you changed the cost and want history to match. '
             '"Custom Cost" lets you type in a specific corrected value instead - use this when you are fixing one '
             'known bad historical entry and do not want to touch it with today\'s unrelated cost.')
    custom_cost = fields.Float(
        string='Custom Cost',
        digits='Product Price',
        help='The cost value to apply to every selected SVL when Cost Source is "Enter a Custom Cost Manually". '
             'Not available with "Process All Products" since one manual price cannot correctly apply across multiple products.'
    )
    dry_run = fields.Boolean(
        string='Dry Run (Preview Only)',
        default=True,
        help='If checked, no changes will be made. Only a preview will be shown.'
    )
    batch_size = fields.Integer(
        string='Batch Size',
        default=50,
        required=True,
        help='Number of records to process before committing'
    )
    create_backup = fields.Boolean(
        string='Create Backup',
        default=True,
        help='Create a restorable backup record before making changes'
    )
    result_message = fields.Html(
        string='Results',
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
        'svl.fixer.backup',
        string='Backup Created',
        readonly=True
    )
    state = fields.Selection([
        ('draft', 'Draft'),
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

    @api.constrains('cost_source', 'custom_cost', 'process_all_products')
    def _check_cost_source(self):
        for record in self:
            if record.cost_source == 'custom':
                if record.process_all_products:
                    raise ValidationError(_(
                        'A custom cost cannot be applied with "Process All Products" since one manual price '
                        'cannot correctly apply across multiple different products. Either switch Cost Source '
                        'to "Use Current Product Cost", or untick "Process All Products" and select specific '
                        'products to apply the custom cost to.'
                    ))
                if record.custom_cost <= 0:
                    raise ValidationError(_('Please enter a Custom Cost greater than zero.'))

    def _get_or_create_backup_run(self, backup_run):
        """Get the current run's backup record, creating it on first use."""
        if backup_run:
            return backup_run
        if self.cost_source == 'custom':
            cost_note = f"custom cost {self.custom_cost:,.2f} applied uniformly"
        else:
            cost_note = "each product's current standard_price"
        backup_run = self.env['svl.fixer.backup'].create({
            'notes': f"Created by SVL Fixer wizard run on {fields.Datetime.now()}"
                     f"{' (DRY RUN)' if self.dry_run else ''}. Cost source: {cost_note}.",
        })
        return backup_run

    def _backup_svl(self, backup_run, svl):
        """Snapshot a single SVL's current values onto the backup run before
        it gets overwritten. Returns the (possibly newly created) backup_run."""
        try:
            backup_run = self._get_or_create_backup_run(backup_run)
            self.env['svl.fixer.backup.line'].create({
                'backup_id': backup_run.id,
                'svl_id': svl.id,
                'product_id': svl.product_id.id,
                'stock_move_id': svl.stock_move_id.id if svl.stock_move_id else False,
                'quantity': svl.quantity,
                'unit_cost': svl.unit_cost,
                'value': svl.value,
                'remaining_qty': svl.remaining_qty,
                'remaining_value': svl.remaining_value,
                'description': svl.description,
                'svl_create_date': svl.create_date,
            })
            return backup_run, True, None
        except Exception as e:
            return backup_run, False, str(e)

    def _calculate_correct_svl_values(self, move, unit_cost):
        """Calculate correct SVL values based on stock move"""
        # Get quantity from stock move
        move_qty = move.quantity if move.quantity > 0 else move.product_uom_qty
        
        # Determine direction and apply sign
        if move.location_id.usage == 'internal' and move.location_dest_id.usage != 'internal':
            # Outgoing: internal → non-internal
            correct_qty = -abs(move_qty)
        elif move.location_id.usage != 'internal' and move.location_dest_id.usage == 'internal':
            # Incoming: non-internal → internal
            correct_qty = abs(move_qty)
        else:
            # Internal transfer or other
            correct_qty = move_qty
        
        # Calculate values
        correct_value = unit_cost * correct_qty
        
        # For remaining quantities: positive quantities can remain, negative are consumed
        if correct_qty > 0:
            correct_remaining_qty = correct_qty
            correct_remaining_value = correct_value
        else:
            correct_remaining_qty = 0.0
            correct_remaining_value = 0.0
        
        return {
            'quantity': correct_qty,
            'unit_cost': unit_cost,
            'value': correct_value,
            'remaining_qty': correct_remaining_qty,
            'remaining_value': correct_remaining_value,
        }

    def _needs_update(self, svl, correct_values, tolerance_qty=0.001, tolerance_value=0.01, min_unit_cost_delta=0.0):
        """Check if SVL needs updating. min_unit_cost_delta lets the user treat
        small/negligible unit cost changes as not worth reporting on large catalogs -
        filtering on the per-unit gap means the same cost mistake is treated the same
        regardless of how many units happened to move in that particular SVL.
        Quantity mismatches always count since a wrong quantity is never noise."""
        qty_changed = abs(svl.quantity - correct_values['quantity']) > tolerance_qty
        remaining_qty_changed = abs(svl.remaining_qty - correct_values['remaining_qty']) > tolerance_qty
        value_changed = abs(svl.value - correct_values['value']) > tolerance_value
        unit_cost_changed = abs(svl.unit_cost - correct_values['unit_cost']) > tolerance_value
        remaining_value_changed = abs(svl.remaining_value - correct_values['remaining_value']) > tolerance_value

        any_changed = qty_changed or remaining_qty_changed or value_changed or unit_cost_changed or remaining_value_changed
        if not any_changed:
            return False

        if min_unit_cost_delta > 0:
            unit_cost_delta = abs(correct_values['unit_cost'] - svl.unit_cost)
            # A quantity change always counts regardless of threshold - it's a
            # correctness issue, not a rounding issue, so we never want to hide it.
            if not qty_changed and not remaining_qty_changed and unit_cost_delta < min_unit_cost_delta:
                return False

        return True

    def action_run_fixer(self):
        """Main method to run the SVL quantity fixer"""
        self.ensure_one()
        
        try:
            dry_run = self.dry_run
            batch_size = self.batch_size
            min_unit_cost_delta = self.min_unit_cost_delta
            currency_symbol = self.env.company.currency_id.symbol
            backup_run = False  # lazily created on first actual change, via _backup_svl

            # Build the date range domain fragment once, reused per product
            svl_date_domain = []
            if self.date_from:
                svl_date_domain.append(('create_date', '>=', self.date_from))
            if self.date_to:
                # include the whole "to" day
                svl_date_domain.append(('create_date', '<', fields.Date.to_string(
                    fields.Date.from_string(self.date_to) + timedelta(days=1)
                )))

            # Determine which products to process
            if self.process_all_products:
                product_templates = self.env['product.template'].search([])
            else:
                product_templates = self.product_tmpl_ids
            
            if not product_templates:
                raise UserError(_('No products selected to process'))
            
            _logger.info(
                'Starting SVL fix for %s products | DRY_RUN: %s | Date range: %s to %s',
                len(product_templates), dry_run, self.date_from or 'any', self.date_to or 'any'
            )
            
            # Track overall statistics
            overall_stats = {
                'total_products': len(product_templates),
                'products_processed': 0,
                'products_failed': 0,
                'products_skipped_no_change': 0,
                'total_svls_updated': 0,
                'total_svls_skipped': 0,
                'total_qty_delta': 0.0,
                'total_value_delta': 0.0,
                'product_summaries': [],
                'errors': []
            }
            csv_rows = []  # one row per SVL that was actually updated, for export
            
            # Process each product
            for product_template in product_templates:
                try:
                    product = product_template.product_variant_ids[0]
                    product_id = product.id
                    
                    _logger.info('Processing product: %s (ID: %s)', product_template.name, product_template.id)
                    
                    # Determine the cost to apply, per the chosen Cost Source
                    if self.cost_source == 'custom':
                        correct_cost = self.custom_cost
                    else:
                        correct_cost = product.standard_price
                    costing_method = product.categ_id.property_cost_method
                    
                    if costing_method != 'standard' and self.cost_source == 'current':
                        _logger.warning(
                            "Product %s uses '%s' costing method. "
                            "This script applies standard cost uniformly.",
                            product.name, costing_method
                        )
                    
                    # Get all SVLs with linked stock moves, within the date range if set
                    svl_domain = [
                        ('product_id', '=', product_id),
                        ('stock_move_id', '!=', False)
                    ] + svl_date_domain
                    svls = self.env['stock.valuation.layer'].search(svl_domain, order='create_date ASC')
                    
                    if not svls:
                        _logger.info('No SVL records with linked moves found for product: %s', product.name)
                        overall_stats['products_processed'] += 1
                        overall_stats['products_skipped_no_change'] += 1
                        continue
                    
                    # Process SVLs for this product
                    updated_count = 0
                    skipped_count = 0
                    qty_delta = 0.0
                    value_delta = 0.0
                    
                    for idx, svl in enumerate(svls, 1):
                        try:
                            move = svl.stock_move_id
                            if not move:
                                skipped_count += 1
                                continue
                            
                            # Calculate correct values
                            correct_values = self._calculate_correct_svl_values(move, correct_cost)
                            
                            # Check if update needed (respecting the min unit-cost-delta threshold)
                            if self._needs_update(svl, correct_values, min_unit_cost_delta=min_unit_cost_delta):
                                old_quantity = svl.quantity
                                old_value = svl.value
                                this_qty_delta = correct_values['quantity'] - old_quantity
                                this_value_delta = correct_values['value'] - old_value
                                qty_delta += this_qty_delta
                                value_delta += this_value_delta

                                # Snapshot the pre-change values before writing, if requested
                                if not dry_run and self.create_backup:
                                    backup_run, backup_success, backup_error = self._backup_svl(backup_run, svl)
                                    if not backup_success:
                                        _logger.warning('Backup failed for SVL %s: %s', svl.id, backup_error)

                                # Apply changes if not dry run
                                if not dry_run:
                                    svl.sudo().with_context(
                                        check_move_validity=False,
                                        disable_svls=True
                                    ).write(correct_values)
                                
                                updated_count += 1
                                csv_rows.append({
                                    'product': product.name,
                                    'svl_id': svl.id,
                                    'old_quantity': old_quantity,
                                    'new_quantity': correct_values['quantity'],
                                    'old_value': old_value,
                                    'new_value': correct_values['value'],
                                    'value_delta': this_value_delta,
                                    'svl_date': svl.create_date,
                                })
                                
                                # Commit in batches
                                if not dry_run and updated_count % batch_size == 0:
                                    self.env.cr.commit()
                                    _logger.info('Committed batch for product %s: %s/%s', product.name, updated_count, len(svls))
                            else:
                                skipped_count += 1
                                
                        except Exception as e:
                            error_msg = f"Product {product.name} - SVL {svl.id}: {str(e)}"
                            overall_stats['errors'].append(error_msg)
                            if len(overall_stats['errors']) <= 10:
                                _logger.error(error_msg)
                    
                    # Final commit for this product
                    if not dry_run and updated_count > 0:
                        self.env.cr.commit()
                        product.invalidate_recordset(['value_svl', 'quantity_svl', 'qty_available'])
                    
                    # Skip cluttering the report with products that had nothing to fix
                    if updated_count == 0:
                        overall_stats['products_skipped_no_change'] += 1
                        overall_stats['total_svls_skipped'] += skipped_count
                        overall_stats['products_processed'] += 1
                        _logger.info('No changes needed for product: %s', product.name)
                        continue

                    # Track product summary
                    overall_stats['product_summaries'].append({
                        'name': product.name,
                        'svls_found': len(svls),
                        'updated': updated_count,
                        'skipped': skipped_count,
                        'qty_delta': qty_delta,
                        'value_delta': value_delta,
                        'final_qty': product.qty_available,
                        'final_value': product.value_svl,
                    })
                    
                    overall_stats['total_svls_updated'] += updated_count
                    overall_stats['total_svls_skipped'] += skipped_count
                    overall_stats['total_qty_delta'] += qty_delta
                    overall_stats['total_value_delta'] += value_delta
                    overall_stats['products_processed'] += 1
                    
                    _logger.info('Completed product: %s - Updated: %s, Skipped: %s', product.name, updated_count, skipped_count)
                    
                except Exception as e:
                    error_msg = f"Failed to process product {product_template.name}: {str(e)}"
                    overall_stats['errors'].append(error_msg)
                    overall_stats['products_failed'] += 1
                    _logger.error(error_msg)
                    continue
            
            # Finalize the backup run, if one was created, with its totals
            if backup_run:
                backup_run.write({
                    'product_count': len(set(line.product_id.id for line in backup_run.line_ids)),
                    'line_count': len(backup_run.line_ids),
                })

            # Generate overall summary
            product_rows = ""
            for summary in overall_stats['product_summaries']:
                product_rows += f"""
                <tr>
                    <td>{summary['name']}</td>
                    <td>{summary['svls_found']}</td>
                    <td>{summary['updated']}</td>
                    <td>{summary['skipped']}</td>
                    <td>{summary['qty_delta']:+,.2f}</td>
                    <td>{currency_symbol}{summary['value_delta']:+,.2f}</td>
                    <td>{summary['final_qty']:,.2f}</td>
                    <td>{currency_symbol}{summary['final_value']:,.2f}</td>
                </tr>
                """
            
            product_table = f"""
            <h4>Product Summary:</h4>
            <table border="1" cellpadding="5" cellspacing="0" style="border-collapse: collapse; width: 100%;">
                <thead>
                    <tr style="background-color: #f0f0f0;">
                        <th>Product</th>
                        <th>SVLs Found</th>
                        <th>Updated</th>
                        <th>Skipped</th>
                        <th>Qty Δ</th>
                        <th>Value Δ</th>
                        <th>Final Qty</th>
                        <th>Final Value</th>
                    </tr>
                </thead>
                <tbody>
                    {product_rows}
                </tbody>
            </table>
            """
            
            error_list = "<br/>".join(overall_stats['errors'][:20]) if overall_stats['errors'] else "None"
            
            dry_run_label = "<strong style='color: orange;'>[DRY RUN - NO CHANGES MADE]</strong>" if dry_run else ""

            if self.cost_source == 'custom':
                cost_source_label = f"<p><strong>Cost Source:</strong> Custom cost ({currency_symbol}{self.custom_cost:,.2f}) applied uniformly</p>"
            else:
                cost_source_label = "<p><strong>Cost Source:</strong> Each product's current standard_price (per-product, see table below)</p>"

            date_range_label = ""
            if self.date_from or self.date_to:
                date_range_label = f"<p><strong>Date Range:</strong> {self.date_from or 'any'} to {self.date_to or 'any'}</p>"

            backup_label = ""
            if backup_run:
                backup_label = f"<p><strong>Backup Created:</strong> {backup_run.name} ({backup_run.line_count} line(s)) - browse under SVL Fixer Backups to restore if needed</p>"
            
            summary = f"""
            <div style="font-family: monospace; padding: 10px;">
                <h3>SVL FIX MULTI-PRODUCT SUMMARY {dry_run_label}</h3>
                <hr/>
                <h4>Overall Statistics:</h4>
                <p><strong>Total Products Selected:</strong> {overall_stats['total_products']}</p>
                <p><strong>Products Processed Successfully:</strong> {overall_stats['products_processed']}</p>
                <p><strong>Products Skipped (No Changes Needed):</strong> {overall_stats['products_skipped_no_change']}</p>
                <p><strong>Products Failed:</strong> {overall_stats['products_failed']}</p>
                <p><strong>Total SVLs Updated:</strong> {overall_stats['total_svls_updated']}</p>
                <p><strong>Total SVLs Skipped:</strong> {overall_stats['total_svls_skipped']}</p>
                <p><strong>Total Quantity Delta:</strong> {overall_stats['total_qty_delta']:+,.2f}</p>
                <p><strong>Total Value Delta:</strong> {currency_symbol}{overall_stats['total_value_delta']:+,.2f}</p>
                {cost_source_label}
                {date_range_label}
                {backup_label}
                <hr/>
                {product_table}
                <hr/>
                <h4>Errors (first 20):</h4>
                <p>{error_list}</p>
                <hr/>
                {'<p style="color: orange;"><strong>⚠️ THIS WAS A DRY RUN - NO CHANGES WERE MADE</strong><br/>Uncheck "Dry Run" to apply changes</p>' if dry_run else '<p style="color: green;"><strong>✓ Changes have been applied successfully</strong></p>'}
            </div>
            """

            # Build the CSV export of every individual SVL change made/previewed this run
            csv_buffer = io.StringIO()
            writer = csv.writer(csv_buffer)
            writer.writerow([
                'Product', 'SVL ID', 'Old Quantity', 'New Quantity',
                'Old Value', 'New Value', f'Value Delta ({currency_symbol})', 'SVL Date'
            ])
            for row in csv_rows:
                svl_date_str = row['svl_date'].strftime('%Y-%m-%d %H:%M:%S') if row['svl_date'] else ''
                writer.writerow([
                    row['product'], row['svl_id'],
                    f"{row['old_quantity']:.4f}", f"{row['new_quantity']:.4f}",
                    f"{row['old_value']:.2f}", f"{row['new_value']:.2f}",
                    f"{row['value_delta']:+.2f}", svl_date_str,
                ])
            csv_content = csv_buffer.getvalue()
            csv_filename = f"svl_fixer_{'dryrun_' if dry_run else ''}{fields.Datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            
            self.write({
                'result_message': summary,
                'state': 'done',
                'backup_id': backup_run.id if backup_run else False,
                'csv_export': base64.b64encode(csv_content.encode('utf-8')),
                'csv_export_filename': csv_filename,
            })
            
            _logger.info('SVL fix completed for %s products', overall_stats['products_processed'])
            
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'svl.fixer.wizard',
                'view_mode': 'form',
                'res_id': self.id,
                'target': 'new',
            }
            
        except Exception as e:
            # Rollback on any error
            if not dry_run:
                self.env.cr.rollback()
            error_msg = f"CRITICAL ERROR: {str(e)}<br/>All changes have been rolled back."
            _logger.error('Critical error in SVL fixer: %s', str(e))
            raise UserError(_(error_msg))

    def action_reset(self):
        """Reset wizard to draft state"""
        self.write({
            'result_message': False,
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
            'res_model': 'svl.fixer.backup',
            'view_mode': 'form',
            'res_id': self.backup_id.id,
            'target': 'current',
        }