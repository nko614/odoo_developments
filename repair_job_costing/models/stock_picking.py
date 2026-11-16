from odoo import models


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def _action_done(self):
        res = super()._action_done()
        # Items received for a repair sold on a sale order get a repair each, as Create Repair would
        # make, linked to the sale order line like the repairs standard Odoo creates on confirmation.
        move_lines = self.move_ids.filtered(
            lambda m: m.state == 'done' and m.picking_code == 'incoming' and m.sale_line_id.service_tracking == 'repair'
        ).move_line_ids
        self.env['repair.order'].create([{
            'state': 'confirmed',
            'company_id': line.company_id.id,
            'partner_id': line.move_id.sale_line_id.order_id.partner_id.id,
            'sale_order_id': line.move_id.sale_line_id.order_id.id,
            'sale_order_line_id': line.move_id.sale_line_id.id,
            'picking_type_id': line.picking_id.picking_type_id.warehouse_id.repair_type_id.id,
            'picking_id': line.picking_id.id,
            'product_id': line.product_id.id,
            'product_qty': line.quantity,
            'lot_id': line.lot_id.id,
        } for line in move_lines])
        return res

    def action_repair_return(self):
        # Create Repair on a receipt whose items all have a repair opens those repairs instead.
        if self.repair_ids and len(self.repair_ids) >= len(self.move_line_ids):
            return self.action_view_repairs()
        return super().action_repair_return()
