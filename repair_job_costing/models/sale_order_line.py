from odoo import api, models


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    @api.depends('order_id.analytic_account_id')
    def _compute_analytic_distribution(self):
        super()._compute_analytic_distribution()
        for line in self:
            if line.order_id.analytic_account_id and not line.analytic_distribution and not line.display_type:
                line.analytic_distribution = {str(line.order_id.analytic_account_id.id): 100}
