import logging

from . import models

_logger = logging.getLogger(__name__)


def post_init(env):
    """Check the bills of contracts that already exist when the bridge is installed."""
    try:
        contracts = env['contract.radar'].search([
            ('is_template', '=', False), ('check_bills', '=', True), ('flow', '=', 'cost'),
            ('state', 'in', ['active', 'expiring', 'renewed', 'expired'])])
        contracts._match_bills()
    except Exception:
        _logger.exception("Contract Guard: first bill check failed; it will run with the daily cron")
