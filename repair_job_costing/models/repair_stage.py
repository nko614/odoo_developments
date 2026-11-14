from odoo import fields, models


class RepairStage(models.Model):
    _name = 'repair.stage'
    _description = "Repair Stage"
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    fold = fields.Boolean(string="Folded in Kanban")
