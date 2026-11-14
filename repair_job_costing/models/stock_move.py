from odoo import models


class StockMove(models.Model):
    _inherit = 'stock.move'

    def _get_analytic_distribution(self):
        if self.repair_line_type and self.repair_id.analytic_account_id:
            return {str(self.repair_id.analytic_account_id.id): 100}
        return super()._get_analytic_distribution()
