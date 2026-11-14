from odoo import api, fields, models
from odoo.exceptions import UserError


class RepairOrder(models.Model):
    _name = 'repair.order'
    _inherit = ['repair.order', 'portal.mixin']
    _mail_post_access = 'read'  # customers can reply from the portal page of their repair

    stage_id = fields.Many2one(
        'repair.stage', string="Stage", index=True, tracking=True, copy=False, ondelete='restrict',
        default=lambda self: self.env['repair.stage'].search([], limit=1),
        group_expand='_read_group_stage_ids')
    analytic_account_id = fields.Many2one(
        related='sale_order_id.analytic_account_id', store=True, index='btree_not_null')
    currency_id = fields.Many2one(related='company_id.currency_id')
    photo_ids = fields.Many2many('ir.attachment', string="Photos")
    timesheet_ids = fields.One2many(
        'account.analytic.line', 'repair_id', string="Timesheets",
        groups='hr_timesheet.group_hr_timesheet_user')
    timesheet_hours = fields.Float(
        "Hours Spent", compute='_compute_timesheet_totals', groups='hr_timesheet.group_hr_timesheet_user')
    timesheet_cost = fields.Monetary(
        "Labor Cost", compute='_compute_timesheet_totals', groups='hr_timesheet.group_hr_timesheet_user')

    @api.depends('timesheet_ids.unit_amount', 'timesheet_ids.amount')
    def _compute_timesheet_totals(self):
        totals = {
            repair.id: (hours, amount)
            for repair, hours, amount in self.env['account.analytic.line']._read_group(
                [('repair_id', 'in', self.ids)], ['repair_id'], ['unit_amount:sum', 'amount:sum'])
        }
        for repair in self:
            hours, amount = totals.get(repair.id, (0.0, 0.0))
            repair.timesheet_hours = hours
            # Timesheet amounts are costs, stored as negative numbers.
            repair.timesheet_cost = -amount

    def _compute_access_url(self):
        super()._compute_access_url()
        for repair in self:
            repair.access_url = f'/my/repairs/{repair.id}'

    def _read_group_stage_ids(self, stages, domain):
        return stages.search([])

    def write(self, vals):
        res = super().write(vals)
        ready = self.env.ref('repair_job_costing.repair_stage_ready_for_pickup', raise_if_not_found=False)
        if ready and vals.get('stage_id') == ready.id:
            # Moving a repair to Ready for Pickup ends it, as End Repair does.
            repairs = self.filtered(lambda r: r.state in ('draft', 'confirmed'))
            if self.filtered(lambda r: r.state == 'cancel'):
                raise UserError(self.env._("A cancelled repair can't be ready for pickup."))
            if not repairs:
                return res
            repairs._action_repair_confirm()
            repairs.action_assign()
            if repairs.move_ids.filtered(lambda m: m.state != 'cancel' and m.quantity != m.product_uom_qty):
                raise UserError(self.env._(
                    "Some parts are not available in the quantity planned. "
                    "Use End Repair to confirm the quantities used."))
            repairs.action_repair_done()
        return res

    def action_repair_done(self):
        res = super().action_repair_done()
        ready = self.env.ref('repair_job_costing.repair_stage_ready_for_pickup', raise_if_not_found=False)
        if ready:
            self.filtered(lambda r: r.stage_id != ready).stage_id = ready
        return res
