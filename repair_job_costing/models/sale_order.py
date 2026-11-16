from odoo import fields, models
from odoo.fields import Command


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    analytic_currency_id = fields.Many2one(related='analytic_account_id.currency_id', string="Analytic Account Currency")
    analytic_balance = fields.Monetary(
        "Gross Margin", related='analytic_account_id.balance', currency_field='analytic_currency_id',
        groups='analytic.group_analytic_accounting')

    def _action_confirm(self):
        res = super()._action_confirm()
        # sudo: salespeople can't read operation types; standard Odoo creates an order's deliveries
        # as superuser too (stock.rule._run_pull). All values come from the confirmed orders.
        receipt_type = self.env.ref('repair_job_costing.picking_type_repair_receipt').sudo()
        pickings_vals = []
        for order in self:
            repair_lines = order.order_line.filtered(lambda line: line.service_tracking == 'repair')
            if not repair_lines:
                continue
            order._get_or_create_analytic_account(False)
            item_lines = repair_lines.filtered('repair_product_id')
            if not item_lines or order.company_id != receipt_type.company_id:
                continue
            # A receipt for the customer's items and a delivery to return them, both owned by the customer.
            values = {'partner_id': order.partner_id.id, 'owner_id': order.partner_id.id, 'origin': order.name}
            moves = [{'product_id': line.repair_product_id.id, 'product_uom_qty': 1, 'sale_line_id': line.id}
                     for line in item_lines]
            pickings_vals += [
                {**values, 'picking_type_id': receipt_type.id,
                 'move_ids': [Command.create(move) for move in moves]},
                # Make to order without a procurement: the moves wait, and can't be reserved,
                # until their repair is done (see repair.order._prepare_return_delivery).
                {**values, 'picking_type_id': receipt_type.return_picking_type_id.id,
                 'location_id': receipt_type.default_location_dest_id.id,
                 'move_ids': [Command.create({**move, 'procure_method': 'make_to_order'}) for move in moves]},
            ]
        self.env['stock.picking'].sudo().create(pickings_vals).move_ids._action_confirm(create_proc=False)
        return res

    def action_view_analytic_lines(self):
        self.ensure_one()
        # Same action as the Gross Margin button of the analytic account form.
        action = self.env['ir.actions.act_window']._for_xml_id('analytic.account_analytic_line_action')
        action['domain'] = [('auto_account_id', '=', self.analytic_account_id.id)]
        action['context'] = {'group_by': ['category'], 'default_auto_account_id': self.analytic_account_id.id}
        return action
