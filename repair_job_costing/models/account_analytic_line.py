from odoo import api, fields, models


class AccountAnalyticLine(models.Model):
    _inherit = 'account.analytic.line'

    repair_id = fields.Many2one('repair.order', string="Repair Order", index='btree_not_null')
    repair_product_id = fields.Many2one(
        'product.product', string="Repaired Product", compute='_compute_repair_product_id',
        store=True, index='btree_not_null')

    @api.depends('repair_id.product_id', 'move_line_id.sale_line_ids.order_id.repair_order_ids.product_id')
    def _compute_repair_product_id(self):
        for line in self:
            # Invoice lines: only when the order's repairs are for a single product.
            products = line.repair_id.product_id or line.move_line_id.sale_line_ids.order_id.repair_order_ids.product_id
            line.repair_product_id = products if len(products) == 1 else False

    @api.model_create_multi
    def create(self, vals_list):
        # Lines of a repair (time and parts) get the repair's customer. Time logged on a repair
        # (a line without a product) is a timesheet of the Repairs project, posted to the
        # analytic account of the repair's sale order when there is one.
        repairs = self.env['repair.order'].browse(vals['repair_id'] for vals in vals_list if vals.get('repair_id'))
        if repairs:
            project = self.env.ref('repair_job_costing.project_repairs')
            repair_by_id = {repair.id: repair for repair in repairs}
            for vals in vals_list:
                if not vals.get('repair_id'):
                    continue
                repair = repair_by_id[vals['repair_id']]
                if not vals.get('partner_id'):
                    vals['partner_id'] = repair.partner_id.commercial_partner_id.id
                if vals.get('product_id'):
                    continue
                if not vals.get('project_id'):
                    vals['project_id'] = project.id
                if not vals.get('account_id') and repair.analytic_account_id:
                    vals['account_id'] = repair.analytic_account_id.id
        return super().create(vals_list)
