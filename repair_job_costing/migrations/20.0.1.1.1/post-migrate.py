from odoo import SUPERUSER_ID, api
from odoo.tools import SQL


def migrate(cr, version):
    """Before 20.0.1.1.0, part-cost lines of repairs had no repair and timesheets on repairs
    had no customer. Fill both, then recompute the repaired product of repair lines."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    rel = env['stock.move']._fields['analytic_account_line_ids']
    cr.execute(SQL(
        """UPDATE account_analytic_line aal SET repair_id = sm.repair_id
             FROM %(rel)s rel JOIN stock_move sm ON sm.id = rel.%(move_col)s
            WHERE rel.%(line_col)s = aal.id AND sm.repair_id IS NOT NULL AND sm.repair_line_type IS NOT NULL
              AND aal.repair_id IS NULL""",
        rel=SQL.identifier(rel.relation), move_col=SQL.identifier(rel.column1), line_col=SQL.identifier(rel.column2),
    ))
    cr.execute(SQL(
        """UPDATE account_analytic_line aal SET partner_id = p.commercial_partner_id
             FROM repair_order r JOIN res_partner p ON p.id = r.partner_id
            WHERE aal.repair_id = r.id AND aal.partner_id IS NULL"""
    ))
    env.invalidate_all()
    lines = env['account.analytic.line'].search([
        '|', ('repair_id', '!=', False), ('move_line_id.sale_line_ids.order_id.repair_order_ids', '!=', False),
    ])
    env.add_to_compute(lines._fields['repair_product_id'], lines)
    env.flush_all()
