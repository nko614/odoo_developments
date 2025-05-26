from odoo import fields, models

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    auto_optimize_routes = fields.Boolean(
        string='Auto-optimize Delivery Routes',
        config_parameter='nko_google.auto_optimize_routes',
        help='Automatically optimize delivery routes at scheduled intervals'
    )

    optimize_route_interval = fields.Selection([
        ('hourly', 'Hourly'),
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
    ], string='Optimization Interval',
        config_parameter='nko_google.optimize_route_interval',
        default='daily',
        help='Frequency of automatic route optimization'
    ) 