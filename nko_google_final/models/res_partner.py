from odoo import models, fields, api

class ResPartner(models.Model):
    _inherit = 'res.partner'

    def get_delivery_address(self):
        """Get formatted delivery address for Google Maps API"""
        self.ensure_one()
        return f"{self.street or ''}, {self.city or ''}, {self.state_id.name or ''}, {self.zip or ''}, {self.country_id.name or ''}" 