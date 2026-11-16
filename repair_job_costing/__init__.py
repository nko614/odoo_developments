from . import controllers
from . import models


def _post_init_hook(env):
    # Records that existed before the module was installed.
    env['stock.picking.type'].search([('code', '=', 'repair_operation')]).analytic_costs = True
    env['repair.order'].search([('stage_id', '=', False)]).with_context(tracking_disable=True).stage_id = (
        env.ref('repair_job_costing.repair_stage_new'))
    # The repaired item stays in WH/Repair, where it was received, so it keeps its owner.
    repair_location = env.ref('repair_job_costing.stock_location_repair')
    env.ref('stock.warehouse0').repair_type_id.write({
        'default_product_location_src_id': repair_location.id,
        'default_product_location_dest_id': repair_location.id,
    })
