from odoo import api, fields, models


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    repair_product_id = fields.Many2one(
        'product.product', string="Item to Repair", domain=[('type', '=', 'consu')],
        help="The customer's item to repair. Confirming the order creates a receipt for it and a delivery "
             "to return it, and receiving it creates the repair.")

    @api.depends('order_id.analytic_account_id')
    def _compute_analytic_distribution(self):
        super()._compute_analytic_distribution()
        for line in self:
            if line.order_id.analytic_account_id and not line.analytic_distribution and not line.display_type:
                line.analytic_distribution = {str(line.order_id.analytic_account_id.id): 100}

    def _create_repair_order(self):
        # Lines with an item to repair get their repairs when the item is received (see stock.picking).
        return super(SaleOrderLine, self.filtered(lambda line: not line.repair_product_id))._create_repair_order()
