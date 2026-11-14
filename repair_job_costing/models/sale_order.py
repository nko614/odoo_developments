from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    analytic_currency_id = fields.Many2one(related='analytic_account_id.currency_id', string="Analytic Account Currency")
    analytic_balance = fields.Monetary(
        "Gross Margin", related='analytic_account_id.balance', currency_field='analytic_currency_id',
        groups='analytic.group_analytic_accounting')

    def _action_confirm(self):
        res = super()._action_confirm()
        for order in self:
            if 'repair' in order.order_line.product_template_id.mapped('service_tracking'):
                order._get_or_create_analytic_account(False)
        return res

    def action_view_analytic_lines(self):
        self.ensure_one()
        # Same action as the Gross Margin button of the analytic account form.
        action = self.env['ir.actions.act_window']._for_xml_id('analytic.account_analytic_line_action')
        action['domain'] = [('auto_account_id', '=', self.analytic_account_id.id)]
        action['context'] = {'group_by': ['category'], 'default_auto_account_id': self.analytic_account_id.id}
        return action
