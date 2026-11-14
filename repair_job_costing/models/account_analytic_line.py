from odoo import api, fields, models


class AccountAnalyticLine(models.Model):
    _inherit = 'account.analytic.line'

    repair_id = fields.Many2one('repair.order', string="Repair Order", index='btree_not_null')

    @api.model_create_multi
    def create(self, vals_list):
        # Time logged on a repair is a timesheet of the Repairs project, posted to the
        # analytic account of the repair's sale order when there is one.
        repairs = self.env['repair.order'].browse(vals['repair_id'] for vals in vals_list if vals.get('repair_id'))
        if repairs:
            project = self.env.ref('repair_job_costing.project_repairs')
            account_by_repair = {repair.id: repair.analytic_account_id.id for repair in repairs}
            for vals in vals_list:
                if not vals.get('repair_id'):
                    continue
                if not vals.get('project_id'):
                    vals['project_id'] = project.id
                if not vals.get('account_id') and account_by_repair[vals['repair_id']]:
                    vals['account_id'] = account_by_repair[vals['repair_id']]
        return super().create(vals_list)
