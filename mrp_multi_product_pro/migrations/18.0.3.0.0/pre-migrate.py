"""Clean up the persistent technical byproduct rows earlier versions (<=
18.0.2.5.4) attached to shared BOMs. Those rows leaked into every other MO
built on the same BOM and caused duplicate/stale byproduct moves after
duplicating an MO. From 18.0.2.5.5 onward, additional-product cost sharing
is done purely via move.cost_share, with no mrp.bom.byproduct record and no
byproduct_id on the move at all.

Runs BEFORE the module's own is_multi_product_technical column is dropped,
so we can still find these rows by that flag.
"""


def migrate(cr, version):
    if not version:
        return

    # Guard: only run this cleanup if the old technical-byproduct column
    # actually exists on this database. Some installs never had the
    # 18.0.2.5.x byproduct-based architecture (e.g. jumped here from an
    # even earlier dev version, or a fresh install mistakenly carrying a
    # prior version number), so the column this script depends on may
    # simply never have existed. Querying it unconditionally crashes the
    # whole upgrade with UndefinedColumn.
    cr.execute("""
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'mrp_bom_byproduct'
          AND column_name = 'is_multi_product_technical'
    """)
    if not cr.fetchone():
        return

    # 1) Remove leaked/duplicate byproduct moves this bug produced on MOs
    #    that are not yet done (their cost_share hasn't been posted to any
    #    valuation layer, so nothing of value is lost by removing them).
    cr.execute("""
        SELECT sm.id
        FROM stock_move sm
        JOIN mrp_bom_byproduct bb ON sm.byproduct_id = bb.id
        WHERE bb.is_multi_product_technical IS TRUE
          AND sm.state NOT IN ('done', 'cancel')
    """)
    stray_move_ids = [r[0] for r in cr.fetchall()]
    if stray_move_ids:
        cr.execute("DELETE FROM stock_move WHERE id IN %s", (tuple(stray_move_ids),))

    # 2) Remove the technical byproduct rows from every BOM. stock_move.
    #    byproduct_id has no explicit ondelete, so Odoo's default 'set
    #    null' applies - any already-DONE move keeps its own frozen
    #    cost_share (a separate stored field on the move), it just loses
    #    the now-meaningless link back to this technical row.
    cr.execute("""
        DELETE FROM mrp_bom_byproduct WHERE is_multi_product_technical IS TRUE
    """)
