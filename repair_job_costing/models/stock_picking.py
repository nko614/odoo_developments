from odoo import models


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def _action_done(self):
        res = super()._action_done()
        # Items received from a customer (receipt with an owner) are assigned, in receipt line
        # order, to that customer's open repairs from sale orders that have no product yet,
        # oldest repair first. Items left over stay unassigned.
        for picking in self.filtered(lambda p: p.picking_type_code == 'incoming' and p.owner_id):
            repairs = self.env['repair.order'].search([
                ('partner_id.commercial_partner_id', '=', picking.owner_id.commercial_partner_id.id),
                ('sale_order_id', '!=', False), ('product_id', '=', False), ('state', 'in', ('draft', 'confirmed')),
            ], order='id')
            for line, repair in zip(picking.move_line_ids, repairs):
                repair.write({'product_id': line.product_id.id, 'product_qty': line.quantity,
                              'lot_id': line.lot_id.id, 'picking_id': picking.id})
        return res

    def action_repair_return(self):
        # Create Repair on a receipt whose items all have a repair opens those repairs instead.
        if self.repair_ids and len(self.repair_ids) >= len(self.move_line_ids):
            return self.action_view_repairs()
        return super().action_repair_return()
