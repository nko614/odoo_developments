from odoo import api, models


class StockPickingType(models.Model):
    _inherit = 'stock.picking.type'

    @api.depends('code')
    def _compute_analytic_costs(self):
        super()._compute_analytic_costs()
        self.filtered(lambda pt: pt.code == 'repair_operation').analytic_costs = True
