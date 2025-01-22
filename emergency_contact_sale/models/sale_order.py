from odoo import models, fields

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    emergency_contact_id = fields.Many2one(
        'res.partner',
        string='Emergency Contact'
    )
    
    emergency_phone = fields.Char(
        string='Emergency Phone',
        related='emergency_contact_id.phone',
        readonly=True
    ) 