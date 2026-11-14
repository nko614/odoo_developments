from odoo import models


class StockMove(models.Model):
    _inherit = 'stock.move'

    def _get_analytic_distribution(self):
        if self.repair_line_type and self.repair_id.analytic_account_id:
            return {str(self.repair_id.analytic_account_id.id): 100}
        return super()._get_analytic_distribution()

    def _prepare_analytic_line_values(self, account_field_values, amount, unit_amount):
        res = super()._prepare_analytic_line_values(account_field_values, amount, unit_amount)
        if self.repair_line_type:
            res['repair_id'] = self.repair_id.id
        return res
