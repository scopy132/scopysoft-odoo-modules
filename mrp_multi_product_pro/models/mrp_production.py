from collections import defaultdict

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.fields import Command


class MrpProduction(models.Model):
    _inherit = 'mrp.production'

    multi_product_line_ids = fields.One2many(
        'mrp.production.product.line', 'production_id', 'Additional Products',
        copy=True)
    has_multi_products = fields.Boolean(compute='_compute_has_multi_products', store=True)
    total_products_count = fields.Integer(compute='_compute_total_products_count')
    all_product_ids = fields.Many2many(
        'product.product', compute='_compute_all_product_ids',
        string='All Products in this MO')
    multi_product_missing_bom = fields.Boolean(
        compute='_compute_multi_product_missing_bom',
        help="At least one additional product has no Bill of Materials "
             "selected, so it will be produced with zero components.")
    multi_product_has_standard_cost_product = fields.Boolean(
        compute='_compute_multi_product_has_standard_cost_product',
        help="True when this order has more than one product and at least "
             "one of them (main or additional) uses the 'Standard Price' "
             "costing method. Odoo only splits real production cost across "
             "products when using AVCO or FIFO — on Standard Price, each "
             "product is valued at its own configured cost regardless of "
             "what this specific run actually consumed.")
    multi_product_overall_status = fields.Selection([
        ('available', 'Available'),
        ('partial', 'Partial'),
        ('unavailable', 'Not Available'),
    ], compute='_compute_multi_product_overall_status',
        string='Products Readiness')
    multi_product_total_cost = fields.Monetary(
        compute='_compute_multi_product_total_cost',
        currency_field='company_currency_id',
        help="Sum of the real per-product cost breakdown for every "
             "product on this order (see the Cost Breakdown button).")
    multi_product_shortage_summary = fields.Char(
        compute='_compute_multi_product_shortage_summary',
        help="One-line summary of component availability across every "
             "product on this order.")
    company_currency_id = fields.Many2one(
        related='company_id.currency_id', string='Currency', readonly=True)

    @api.depends('multi_product_line_ids.component_status', 'multi_product_overall_status',
                 'total_products_count')
    def _compute_multi_product_shortage_summary(self):
        for production in self:
            if production.total_products_count <= 1:
                production.multi_product_shortage_summary = ''
                continue
            short = production.multi_product_line_ids.filtered(
                lambda l: l.component_status != 'available')
            total = len(production.multi_product_line_ids)
            if not short:
                production.multi_product_shortage_summary = _(
                    'All %s additional products have their components available.', total)
            else:
                names = ', '.join(short.mapped('product_id.display_name')[:3])
                if len(short) > 3:
                    names += _(' and %s more', len(short) - 3)
                production.multi_product_shortage_summary = _(
                    '%(short)s of %(total)s additional products are short on components: %(names)s',
                    short=len(short), total=total, names=names,
                )

    @api.depends('multi_product_line_ids.bom_id')
    def _compute_multi_product_missing_bom(self):
        for production in self:
            production.multi_product_missing_bom = bool(
                production.multi_product_line_ids.filtered(lambda l: not l.bom_id))

    @api.depends('all_product_ids.categ_id.property_cost_method', 'total_products_count')
    def _compute_multi_product_has_standard_cost_product(self):
        for production in self:
            if production.total_products_count <= 1:
                production.multi_product_has_standard_cost_product = False
                continue
            production.multi_product_has_standard_cost_product = bool(
                production.all_product_ids.filtered(
                    lambda p: p.categ_id.property_cost_method == 'standard'))

    @api.depends('multi_product_line_ids.component_status')
    def _compute_multi_product_overall_status(self):
        for production in self:
            statuses = set(production.multi_product_line_ids.mapped('component_status'))
            if not statuses or statuses == {'available'}:
                production.multi_product_overall_status = 'available'
            elif statuses == {'unavailable'}:
                production.multi_product_overall_status = 'unavailable'
            else:
                production.multi_product_overall_status = 'partial'

    def _compute_multi_product_total_cost(self):
        for production in self:
            if production.has_multi_products:
                rows = production._get_multi_product_cost_breakdown()
                production.multi_product_total_cost = sum(r['total_cost'] for r in rows)
            else:
                production.multi_product_total_cost = 0.0

    @api.depends('multi_product_line_ids')
    def _compute_has_multi_products(self):
        for production in self:
            production.has_multi_products = bool(production.multi_product_line_ids)

    @api.depends('multi_product_line_ids', 'product_id')
    def _compute_total_products_count(self):
        for production in self:
            production.total_products_count = (
                len(production.multi_product_line_ids) + (1 if production.product_id else 0)
            )

    @api.depends('multi_product_line_ids.product_id', 'product_id')
    def _compute_all_product_ids(self):
        for production in self:
            products = production.multi_product_line_ids.product_id
            if production.product_id:
                products |= production.product_id
            production.all_product_ids = products

    # -------------------------------------------------------------------------
    # Sale-order-style multi-select entry point
    # -------------------------------------------------------------------------

    def action_add_products(self):
        """Open the bulk product picker (like adding several products to a
        Sales Order at once). The first product picked becomes the MO's
        primary product if the MO doesn't already have one."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Add Products to Manufacture'),
            'res_model': 'mrp.production.add.products.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_production_id': self.id},
        }

    def _add_product(self, product, qty, bom=None, uom=None, sale_line=None):
        """Add a single product to this MO: becomes the primary product if
        none is set yet, otherwise becomes an additional product line."""
        self.ensure_one()
        if not bom:
            domain = [
                ('type', '=', 'normal'),
                '|',
                ('product_id', '=', product.id),
                '&',
                ('product_tmpl_id', '=', product.product_tmpl_id.id),
                ('product_id', '=', False),
            ]
            bom = self.env['mrp.bom'].search(domain, limit=1)

        if not self.product_id:
            vals = {
                'product_id': product.id,
                'product_qty': qty,
                'product_uom_id': (uom or product.uom_id).id,
            }
            if bom:
                vals['bom_id'] = bom.id
            self.write(vals)
            # IMPORTANT: writing product_id/bom_id directly (instead of
            # going through the form's onchange, which is what normally
            # builds the finished-good move and explodes the BOM into raw
            # material moves) leaves this MO with an empty Components tab
            # and no way to actually produce anything. Build both explicitly.
            self._generate_primary_product_moves()
            if sale_line:
                self._link_move_to_sale_line(self.move_finished_ids, sale_line)
        else:
            self.write({
                'multi_product_line_ids': [(0, 0, {
                    'product_id': product.id,
                    'product_qty': qty,
                    'product_uom_id': (uom or product.uom_id).id,
                    'bom_id': bom.id if bom else False,
                })],
            })
            if sale_line:
                new_line = self.multi_product_line_ids.filtered(
                    lambda l: l.product_id == product and not l.sale_line_id)
                if new_line:
                    new_line[-1].sale_line_id = sale_line.id
                    self._link_move_to_sale_line(new_line[-1].move_id, sale_line)

    def _generate_primary_product_moves(self):
        """Build the finished-good move and BOM-exploded raw material moves
        for the MO's primary product when they don't exist yet. Needed
        because setting `product_id`/`bom_id` via write() (e.g. from the
        Add Products wizard) skips the form's onchange logic that normally
        creates them."""
        self.ensure_one()
        if not self.product_id:
            return
        if not self.move_finished_ids.filtered(
                lambda m: m.product_id == self.product_id and not m.byproduct_id):
            self.env['stock.move'].create({
                'name': self.name or _('New'),
                'product_id': self.product_id.id,
                'product_uom_qty': self.product_qty,
                'product_uom': self.product_uom_id.id,
                'location_id': self.product_id.with_company(
                    self.company_id).property_stock_production.id,
                'location_dest_id': self.location_dest_id.id,
                'production_id': self.id,
                'company_id': self.company_id.id,
                'picking_type_id': self.picking_type_id.id,
                'origin': self._get_origin(),
                'state': 'draft',
                'date': self.date_start,
                'date_deadline': self.date_start,
            })
        if self.bom_id and not self.move_raw_ids:
            factor = self.product_uom_id._compute_quantity(
                self.product_qty, self.bom_id.product_uom_id
            ) / (self.bom_id.product_qty or 1.0)
            _boms, bom_lines = self.bom_id.explode(
                self.product_id, factor, picking_type=self.bom_id.picking_type_id)
            production_loc = self.product_id.with_company(self.company_id).property_stock_production
            for bom_line, line_data in bom_lines:
                if (bom_line.child_bom_id and bom_line.child_bom_id.type == 'phantom') \
                        or bom_line.product_id.type != 'consu':
                    continue
                self.env['stock.move'].create({
                    'name': _('New'),
                    'product_id': bom_line.product_id.id,
                    'product_uom_qty': line_data['qty'],
                    'product_uom': bom_line.product_uom_id.id,
                    'location_id': self.location_src_id.id,
                    'location_dest_id': production_loc.id,
                    'raw_material_production_id': self.id,
                    'company_id': self.company_id.id,
                    'picking_type_id': self.picking_type_id.id,
                    'origin': self._get_origin(),
                    'bom_line_id': bom_line.id,
                    'state': 'draft',
                    'procure_method': 'make_to_stock',
                    'group_id': self.procurement_group_id.id,
                    'propagate_cancel': self.propagate_cancel,
                    'warehouse_id': self.location_src_id.warehouse_id.id,
                    'date': self.date_start,
                    'date_deadline': self.date_start,
                })

    def _link_move_to_sale_line(self, move, sale_line):
        """Wire a finished-good move back to a Sales Order line so the
        Sales Order's native 'Manufacturing' smart button (and count)
        picks up MOs created through this module's product picker or
        'Pull from Sales Order' — not just MOs linked the manual/native
        way. Without this, only manually-created single-product MOs show
        up on the Sales Order."""
        if not move or not sale_line:
            return
        target_move = move[:1]
        delivery_move = sale_line.move_ids.filtered(
            lambda m: not m.created_production_id and m.state not in ('done', 'cancel'))[:1]
        if delivery_move:
            delivery_move.created_production_id = target_move.production_id.id

    # -------------------------------------------------------------------------
    # Pull from Sales Order
    # -------------------------------------------------------------------------

    def action_pull_from_sale_order(self):
        self.ensure_one()
        sale_order = self._find_linked_sale_order()
        if not sale_order:
            return {
                'type': 'ir.actions.act_window',
                'name': _('Pull Products from a Sales Order'),
                'res_model': 'mrp.production.pull.so.wizard',
                'view_mode': 'form',
                'target': 'new',
                'context': {'default_production_id': self.id},
            }
        return self._pull_products_from_sale_order(sale_order)

    def _find_linked_sale_order(self):
        self.ensure_one()
        sale_order = self.procurement_group_id.sale_id
        if sale_order:
            return sale_order
        if self.origin:
            return self.env['sale.order'].search([('name', '=', self.origin)], limit=1)
        return self.env['sale.order']

    def _pull_products_from_sale_order(self, sale_order):
        self.ensure_one()
        existing_products = self.all_product_ids
        added = 0
        for sol in sale_order.order_line.filtered(
                lambda l: l.product_id.type in ('consu', 'product')
                and l.product_id not in existing_products
                and not l.display_type):
            self._add_product(sol.product_id, sol.product_uom_qty, uom=sol.product_uom, sale_line=sol)
            added += 1

        if not added:
            raise UserError(_(
                "Every product on Sales Order %s is already on this "
                "Manufacturing Order.", sale_order.name))
        return True

    # -------------------------------------------------------------------------
    # Auto-sync: keep finished moves + combined components in step with the
    # product lines on every create/write.
    # -------------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        productions = super().create(vals_list)
        for production in productions:
            if production.multi_product_line_ids and production.state in ('draft', 'confirmed'):
                production._sync_multi_product_moves()
        return productions

    def write(self, vals):
        res = super().write(vals)
        if 'multi_product_line_ids' in vals:
            for production in self:
                if production.state in ('draft', 'confirmed'):
                    production._sync_multi_product_moves()
        return res

    def _sync_multi_product_moves(self):
        """Idempotently reconcile finished moves and combined raw component
        moves with the current set of product lines. Safe to call
        repeatedly — lines added, edited, or removed are all reflected
        correctly without double-counting."""
        self.ensure_one()
        self._create_multi_product_finished_moves()
        self._cleanup_orphan_multi_product_finished_moves()
        self._add_multi_product_components()

    def _create_multi_product_finished_moves(self):
        self.ensure_one()
        for line in self.multi_product_line_ids.filtered(lambda l: not l.move_id):
            move = self.env['stock.move'].create({
                'name': self.name or _('New'),
                'product_id': line.product_id.id,
                'product_uom_qty': line.product_qty,
                'product_uom': line.product_uom_id.id,
                'location_id': self.product_id.with_company(
                    self.company_id).property_stock_production.id,
                'location_dest_id': self.location_dest_id.id,
                'production_id': self.id,
                'company_id': self.company_id.id,
                'picking_type_id': self.picking_type_id.id,
                'origin': self._get_origin(),
                'state': 'draft',
                'date': self.date_start,
                'date_deadline': self.date_start,
            })
            line.move_id = move.id
        # Keep quantities in sync for lines that already have a move
        for line in self.multi_product_line_ids.filtered(
                lambda l: l.move_id and l.move_id.state not in ('done', 'cancel')):
            if line.move_id.product_uom_qty != line.product_qty:
                line.move_id.product_uom_qty = line.product_qty

    def _cleanup_orphan_multi_product_finished_moves(self):
        """Remove finished-good moves left behind by product lines that
        were deleted before being produced, and stale copies left behind
        by duplicating an MO (core's copy_data() copies move_finished_ids
        verbatim, including old additional-product moves, before
        `mrp.production.product.line.move_id` — which is copy=False — gets
        reset and a fresh move created for the current line)."""
        self.ensure_one()
        line_move_ids = set(self.multi_product_line_ids.move_id.ids)
        orphans = self.move_finished_ids.filtered(
            lambda m: m.production_id == self
            and not m.byproduct_id
            and m.product_id != self.product_id
            and m.id not in line_move_ids
            and m.state not in ('done', 'cancel')
        )
        orphans.unlink()

    def _add_multi_product_components(self):
        """Explode every additional product's BOM and merge the resulting
        components into the MO's raw material moves, combining quantities
        for components shared with the primary product (or with each
        other).

        Safe to call repeatedly: any quantity previously contributed by
        additional-product lines is first unwound (tracked via
        `multi_product_qty` on the move), then recomputed from scratch, so
        edited quantities and removed lines are reflected correctly
        instead of accumulating on top of stale values.
        """
        self.ensure_one()
        production_loc = self.product_id.with_company(
            self.company_id).property_stock_production if self.product_id \
            else self.location_dest_id
        live_moves = self.move_raw_ids.filtered(lambda m: m.state not in ('done', 'cancel'))

        # 1) Unwind any previously-added multi-product quantity.
        for move in live_moves.filtered('multi_product_qty'):
            move.product_uom_qty -= move.multi_product_qty
            move.multi_product_qty = 0.0
        live_moves.filtered(
            lambda m: not m.bom_line_id and m.product_uom_qty <= 0
        ).unlink()
        live_moves = self.move_raw_ids.filtered(lambda m: m.state not in ('done', 'cancel'))

        # 2) Aggregate required component quantities across all additional lines.
        aggregated_qty = defaultdict(float)
        aggregated_uom = {}
        for line in self.multi_product_line_ids.filtered('bom_id'):
            bom = line.bom_id
            factor = line.product_uom_id._compute_quantity(
                line.product_qty, bom.product_uom_id
            ) / (bom.product_qty or 1.0)

            _boms, bom_lines = bom.explode(
                line.product_id, factor,
                picking_type=bom.picking_type_id,
            )
            for bom_line, line_data in bom_lines:
                if (bom_line.child_bom_id and bom_line.child_bom_id.type == 'phantom') \
                        or bom_line.product_id.type != 'consu':
                    continue
                component = bom_line.product_id
                aggregated_qty[component.id] += line_data['qty']
                aggregated_uom.setdefault(component.id, bom_line.product_uom_id)

        # 3) Re-apply: bump an existing move for that component, or create one.
        for component_id, qty in aggregated_qty.items():
            if qty <= 0:
                continue
            existing = live_moves.filtered(lambda m, c=component_id: m.product_id.id == c)
            if existing:
                existing[0].product_uom_qty += qty
                existing[0].multi_product_qty += qty
            else:
                component_uom = aggregated_uom[component_id]
                new_move = self.env['stock.move'].create({
                    'name': _('New'),
                    'product_id': component_id,
                    'product_uom_qty': qty,
                    'product_uom': component_uom.id,
                    'location_id': self.location_src_id.id,
                    'location_dest_id': production_loc.id,
                    'raw_material_production_id': self.id,
                    'company_id': self.company_id.id,
                    'picking_type_id': self.picking_type_id.id,
                    'origin': self._get_origin(),
                    'state': 'draft',
                    'procure_method': 'make_to_stock',
                    'group_id': self.procurement_group_id.id,
                    'propagate_cancel': self.propagate_cancel,
                    'warehouse_id': self.location_src_id.warehouse_id.id,
                    'date': self.date_start,
                    'date_deadline': self.date_start,
                })
                new_move.multi_product_qty = qty
                live_moves |= new_move

    # -------------------------------------------------------------------------
    # Lot/serial validation for additional products
    # -------------------------------------------------------------------------

    # -------------------------------------------------------------------------
    # Lot/serial validation and assignment for additional products
    # -------------------------------------------------------------------------

    def pre_button_mark_done(self):
        for production in self:
            for line in production.multi_product_line_ids:
                if line.product_id.tracking == 'none':
                    continue
                if not line.lot_producing_ids:
                    raise UserError(_(
                        'You need to generate Lot/Serial Numbers for the following product: %s',
                        line.product_id.display_name,
                    ))
                if line.product_id.tracking == 'serial':
                    expected = int(line.product_qty)
                    actual = len(line.lot_producing_ids)
                    if actual != expected:
                        raise UserError(_(
                            'Product "%(product)s" requires %(expected)s serial number(s) but %(actual)s were provided.',
                            product=line.product_id.display_name,
                            expected=expected,
                            actual=actual,
                        ))
        return super().pre_button_mark_done()

    def _post_inventory(self, cancel_backorder=False):
        for order in self:
            for line in order.multi_product_line_ids.filtered(
                lambda l: l.move_id and l.move_id.state not in ('done', 'cancel')
            ):
                move = line.move_id
                qty = line.product_uom_id._compute_quantity(
                    line.product_qty, move.product_uom
                )
                move.quantity = qty
                if line.lot_producing_ids and move.has_tracking != 'none':
                    lots = list(line.lot_producing_ids)
                    lot_idx = 0
                    for ml in move.move_line_ids:
                        if ml.lot_id or lot_idx >= len(lots):
                            continue
                        ml.lot_id = lots[lot_idx]
                        if move.product_id.tracking == 'serial':
                            lot_idx += 1
            # Freeze cost allocation AFTER quantities are final and BEFORE
            # super() (which calls native _cal_price to read cost_share).
            order._allocate_multi_product_cost_shares()
        return super()._post_inventory(cancel_backorder=cancel_backorder)

    # -------------------------------------------------------------------------
    # Merge Wizard (secondary path — for MOs not created via the multi-select
    # picker, e.g. created separately and later combined)
    # -------------------------------------------------------------------------

    def _pre_action_split_merge_hook(self, merge=False, split=False):
        """Override to allow merging MOs with different products."""
        if not merge and not split:
            return True
        ope_str = merge and _('merged') or _('split')
        if any(production.state not in ('draft', 'confirmed') for production in self):
            raise UserError(_(
                "Only manufacturing orders in either a draft or confirmed state can be %s.",
                ope_str))
        if any(not production.bom_id for production in self):
            raise UserError(_(
                "Only manufacturing orders with a Bill of Materials can be %s.",
                ope_str))
        if split:
            return True
        if len(self) < 2:
            raise UserError(_("You need at least two production orders to merge them."))
        additional_raw_ids = self.mapped('move_raw_ids').filtered(lambda m: not m.bom_line_id)
        additional_byproduct_ids = self.mapped('move_byproduct_ids').filtered(lambda m: not m.byproduct_id)
        if additional_raw_ids or additional_byproduct_ids:
            raise UserError(_(
                "You can only merge manufacturing orders with no additional components or by-products."))
        if len(set(self.mapped('state'))) > 1:
            raise UserError(_("You can only merge manufacturing with the same state."))
        if len(set(self.mapped('picking_type_id').ids)) > 1:
            raise UserError(_('You can only merge manufacturing with the same operation type'))
        return True

    def action_merge(self):
        """Merge selected MOs. Routes to multi-product merge when products differ."""
        product_bom_pairs = set((p.product_id.id, p.bom_id.id) for p in self)
        if len(product_bom_pairs) == 1:
            return super().action_merge()
        self._pre_action_split_merge_hook(merge=True)
        return self._do_multi_product_merge()

    def _do_multi_product_merge(self):
        """Create one merged MO that produces all products from the selected MOs."""
        groups = defaultdict(lambda: self.env['mrp.production'])
        for mo in self:
            groups[(mo.product_id.id, mo.bom_id.id)] |= mo

        primary_key = max(
            groups,
            key=lambda k: sum(groups[k].mapped('product_uom_qty')),
        )
        primary_mos = groups[primary_key]
        primary_product = primary_mos[0].product_id
        primary_bom = primary_mos[0].bom_id

        user_ids = set(self.mapped('user_id').ids)
        user_id = list(user_ids)[0] if len(user_ids) == 1 else self.env.user.id

        multi_lines = []
        for (prod_id, bom_id), mos in groups.items():
            if (prod_id, bom_id) == primary_key:
                continue
            product = mos[0].product_id
            multi_lines.append(Command.create({
                'product_id': prod_id,
                'product_qty': sum(mo.product_uom_qty for mo in mos),
                'product_uom_id': product.uom_id.id,
                'bom_id': bom_id,
            }))

        origs = primary_mos._prepare_merge_orig_links()

        dests = {}
        for move in self.move_finished_ids:
            dests.setdefault(move.byproduct_id.id, []).extend(move.move_dest_ids.ids)

        location_final = (
            all(mo.location_final_id for mo in self)
            and len(self.location_final_id) == 1
            and self.location_final_id.id
            or False
        )

        production = self.env['mrp.production'].with_context(
            default_picking_type_id=self.picking_type_id.id
        ).create({
            'product_id': primary_product.id,
            'bom_id': primary_bom.id,
            'picking_type_id': self.picking_type_id.id,
            'product_qty': sum(mo.product_uom_qty for mo in primary_mos),
            'product_uom_id': primary_product.uom_id.id,
            'location_final_id': location_final,
            'user_id': user_id,
            'origin': ",".join(sorted(self.mapped('name'))),
            'multi_product_line_ids': multi_lines,
        })

        for move in production.move_raw_ids:
            bom_line_id = move.bom_line_id.id
            if bom_line_id in origs:
                for field, vals in origs[bom_line_id].items():
                    move[field] = vals

        for move in production.move_finished_ids:
            bp_id = move.byproduct_id.id
            dest_ids = dests.get(bp_id, [])
            if dest_ids:
                move.move_dest_ids = [Command.set(dest_ids)]

        production._create_multi_product_finished_moves()
        production._add_multi_product_components()

        self.move_dest_ids.created_production_id = production.id
        self.procurement_group_id.stock_move_ids.group_id = production.procurement_group_id

        if 'confirmed' in self.mapped('state'):
            production.move_raw_ids._adjust_procure_method()
            (production.move_raw_ids | production.move_finished_ids).write({'state': 'confirmed'})
            production.action_confirm()

        self.with_context(skip_activity=True)._action_cancel()

        for p in self:
            p._message_log(body=_('This production has been merged into %s', production.display_name))

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'mrp.production',
            'view_mode': 'form',
            'res_id': production.id,
        }

    def action_view_multi_product_cost_breakdown(self):
        """Post-production: open the real Stock Valuation Layer records (same
        source of truth as the native Valuation smart button). Pre-production:
        there's nothing posted yet, so show the live estimated breakdown as a
        quick notification instead of an empty list."""
        self.ensure_one()
        if self.state == 'done':
            finished_moves = self._get_multi_product_finished_move_ids()
            return {
                'type': 'ir.actions.act_window',
                'name': _('Products Cost Breakdown'),
                'res_model': 'stock.valuation.layer',
                'view_mode': 'list,form',
                'domain': [('stock_move_id', 'in', finished_moves.ids)],
            }
        rows = self._get_multi_product_cost_breakdown()
        lines = '\n'.join(
            _('%(product)s: %(qty)g x %(unit)s = %(total)s',
              product=r['product'].display_name, qty=r['qty'],
              unit=r['unit_cost'], total=r['total_cost'])
            for r in rows
        )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Estimated Batch Cost (based on standard cost)'),
                'message': lines or _('No products on this order yet.'),
                'sticky': True,
            },
        }

    def _estimate_product_cost_from_bom(self, product, bom, qty):
        """Pre-Done cost estimate for one product: the cost of the
        components its BOM actually calls for, not the finished product's
        own standard_price. standard_price is frequently stale or
        meaningless before a product has ever been produced (a default
        placeholder, an old manual entry, or — on AVCO/FIFO — just a
        running average that has nothing to do with this specific BOM) and
        using it directly gave a batch estimate wildly disconnected from
        the real Products Cost once the order is done. Summing the exploded
        BOM's own components (whose standard_price is usually kept current,
        since it's what actually gets purchased/consumed) is a much closer
        approximation of what this run will really cost. Falls back to the
        product's own standard_price only when there's no BOM to explode.
        """
        self.ensure_one()
        if not bom:
            return product.standard_price * qty
        factor = product.uom_id._compute_quantity(
            qty, bom.product_uom_id) / (bom.product_qty or 1.0)
        _boms, bom_lines = bom.explode(product, factor, picking_type=bom.picking_type_id)
        total = 0.0
        for bom_line, line_data in bom_lines:
            if (bom_line.child_bom_id and bom_line.child_bom_id.type == 'phantom') \
                    or bom_line.product_id.type != 'consu':
                continue
            total += bom_line.product_id.standard_price * line_data['qty']
        return total

    def _get_multi_product_cost_breakdown(self):
        """Per-product cost breakdown for every product on this MO (primary +
        additional lines). Once done, this reads the real stock valuation
        layers directly — `_cal_price` above now posts genuine, non-zero
        cost for every product on the order, so these numbers match the
        native Valuation smart button exactly, product by product. Before
        done, there's nothing posted yet, so it estimates from each
        product's own BOM component cost (see
        _estimate_product_cost_from_bom) rather than the finished product's
        standard_price.
        """
        self.ensure_one()
        Layer = self.env['stock.valuation.layer']
        rows = []
        all_finished_moves = self._get_multi_product_finished_move_ids()

        if self.state == 'done':
            for move in all_finished_moves:
                qty = move.quantity or move.product_uom_qty
                layers = Layer.search([('stock_move_id', '=', move.id)])
                total_cost = sum(layers.mapped('value'))
                unit_cost = total_cost / qty if qty else 0.0
                rows.append({
                    'product': move.product_id,
                    'qty': qty,
                    'unit_cost': unit_cost,
                    'total_cost': total_cost,
                })
        else:
            line_bom_by_product = {
                line.product_id.id: line.bom_id
                for line in self.multi_product_line_ids
            }
            for move in all_finished_moves:
                qty = move.product_uom_qty
                bom = self.bom_id if move.product_id == self.product_id \
                    else line_bom_by_product.get(move.product_id.id)
                total_cost = self._estimate_product_cost_from_bom(move.product_id, bom, qty)
                unit_cost = total_cost / qty if qty else 0.0
                rows.append({
                    'product': move.product_id,
                    'qty': qty,
                    'unit_cost': unit_cost,
                    'total_cost': total_cost,
                })
        return rows

    def _get_multi_product_finished_move_ids(self):
        """Primary + additional-product finished moves for this module's own
        breakdown UI. Our own additional-product moves never carry
        byproduct_id (see _allocate_multi_product_cost_shares) — only a
        real, manually-configured BOM byproduct does, so excluding any move
        with byproduct_id set is enough to keep this to "our" products."""
        self.ensure_one()
        return self.move_finished_ids.filtered(
            lambda m: m.state != 'cancel' and not m.byproduct_id)

    def _allocate_multi_product_cost_shares(self):
        """Freeze cost_share across additional finished moves right before
        production is posted. Allocation basis: standard_price x quantity
        per finished product, normalised across the main product + every
        additional product. Native _cal_price then gives the main product
        the residual (1 - sum(byproduct shares)) exactly as it already does
        for real byproducts.

        IMPORTANT: this only ever writes `cost_share` directly onto the
        additional-product moves that already live on THIS order
        (move_finished_ids, created by _create_multi_product_finished_moves).
        It deliberately never creates or touches an `mrp.bom.byproduct`
        record on the shared BOM.

        `mrp.production.move_byproduct_ids` (what _cal_price actually reads)
        is computed purely from `product_id != order.product_id` — a move
        does NOT need `byproduct_id` set to be picked up by native costing.
        Attaching a persistent byproduct record to the BOM instead of just
        setting cost_share on our own move was the root cause of two bugs:
        it leaked into every other MO built on that BOM (Odoo explodes
        bom.byproduct_ids on the interactive product/BOM onchange path,
        which does not consult _skip_byproduct_line), and duplicating an MO
        copied the stale byproduct-flagged move forward with its frozen
        share, pushing the order's total byproduct cost share over 100 on
        validate. Keeping this purely move-local avoids both.
        """
        self.ensure_one()
        lines = self.multi_product_line_ids.filtered(
            lambda l: l.move_id and l.move_id.state not in ('done', 'cancel')
            and l.move_id.quantity > 0
        )
        if not lines:
            return

        def weight(product, move):
            return product.standard_price * move.quantity

        main_move = self.move_finished_ids.filtered(
            lambda m: m.product_id == self.product_id
            and m.state not in ('done', 'cancel') and m.quantity > 0
        )[:1]
        main_weight = weight(self.product_id, main_move) if main_move else 0.0

        total_weight = main_weight + sum(
            weight(l.product_id, l.move_id) for l in lines
        )
        if not total_weight:
            return

        for line in lines:
            move = line.move_id
            share = round(
                weight(line.product_id, move) / total_weight * 100.0, 4)
            move.cost_share = share

    def action_confirm(self):
        res = super().action_confirm()
        for production in self:
            production._sync_multi_product_moves()
        return res
