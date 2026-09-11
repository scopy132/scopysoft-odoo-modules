{
    'name': 'MRP Multi-Product Manufacturing Pro',
    'version': '18.0.3.0.0',
    'category': 'Manufacturing',
    'summary': 'Pick multiple finished products on one MO like a Sales Order, pull straight from Sales Orders, and get a real per-product cost breakdown.',
    'description': """
MRP Multi-Product Manufacturing Pro
====================================

Manufacture several finished products on a single Manufacturing Order the
same way you add products to a Sales Order: pick them from one list, each
gets its own quantity and BOM, and every component is auto-combined behind
the scenes.

Sales-driven workflow
----------------------
- "Create Multi-Product MO" button right on the Sales Order — one click
  builds one MO covering every product on the order
- "Pull from Sales Order" on the MO side for orders created afterwards
- Both ways link back to the Sales Order properly, so it shows up under
  the Sales Order's own "Manufacturing" smart button — not just when the
  MO was created the manual, single-product way

Built for the shop floor
--------------------------
- Sale-order-style product picker: add as many finished products as you
  like in one go, each with its own quantity and auto-detected BOM
- Components auto-populate and combine live as products are added,
  edited, or removed — no stale leftover quantities
- Per-product component availability badge, plus one overall readiness
  badge on the order itself
- A warning banner flags any product missing a BOM before you find out
  the hard way that it produced with zero components
- Lot/serial number support per product
- The Products tab is locked to editing quantities, UoM, BOM and lots —
  products themselves are only added through "Add Products" or "Pull from
  Sales Order", so there's one clear way to build the list, not two

Real valuation, no guesswork
------------------------------
- Each finished product is produced via its own real stock move and
  costed the standard Odoo way — fully compatible with inventory
  valuation, WIP accounts, and analytic distribution
- A "Products Cost" smart button and combined PDF report break down the
  real cost of every product, pulled straight from the same Stock
  Valuation Layers as Odoo's own Valuation smart button — no separate
  guess-cost logic to keep in sync

Also included
---------------
- Merge Wizard for combining separate, already-existing MOs with
  different products into one (handy for MOs that weren't planned
  together from the start)

Known limitation
-----------------
Odoo's native "MO Overview" screen is built for a single product plus
true cost-shared byproducts, so it will still display $0 for additional
products there — that's a display quirk of that specific screen, not a
data problem (stock valuation and accounting are correct throughout, and
match the standard Valuation smart button). Use this module's own
"Products Cost" button and PDF report instead for accurate multi-product
cost visibility.

Author: ScopySoft
""",
    'author': 'ScopySoft',
    'website': 'https://apps.odoo.com/apps/modules?author=ScopySoft',
    'depends': ['mrp', 'stock', 'sale_mrp', 'mrp_account'],
    'data': [
        'security/ir.model.access.csv',
        'wizard/mrp_production_add_products_wizard_views.xml',
        'wizard/mrp_production_pull_so_wizard_views.xml',
        'views/mrp_production_views.xml',
        'views/sale_order_views.xml',
        'report/mrp_production_templates.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': False,
    'price': 89,
    'currency': 'USD',
    'license': 'OPL-1',
    'images': ['static/description/banner.png'],
}
