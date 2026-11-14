from . import controllers
from . import models


def _post_init_hook(env):
    # Records that existed before the module was installed.
    env['stock.picking.type'].search([('code', '=', 'repair_operation')]).analytic_costs = True
    env['repair.order'].search([('stage_id', '=', False)]).with_context(tracking_disable=True).stage_id = (
        env.ref('repair_job_costing.repair_stage_new'))
