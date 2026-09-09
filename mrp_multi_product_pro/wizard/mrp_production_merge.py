# The multi-product merge logic overrides mrp.production.action_merge()
# directly (see models/mrp_production.py) so it plugs into Odoo's native
# "Merge" action already available from the Manufacturing Orders list view.
# No separate transient wizard model is needed.
