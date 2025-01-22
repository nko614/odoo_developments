from odoo import models, api, _
from odoo.exceptions import ValidationError

class ProductTemplate(models.Model):
    _inherit = 'product.template'

    @api.constrains('default_code')
    def _check_duplicate_default_code(self):
        for product in self:
            if product.default_code:
                domain = [
                    ('default_code', '=', product.default_code),
                    ('id', '!=', product.id)
                ]
                if self.search_count(domain) > 0:
                    raise ValidationError(_("A product with this Reference Number already exists"))

class ProductProduct(models.Model):
    _inherit = 'product.product'

    @api.constrains('default_code')
    def _check_duplicate_default_code(self):
        for product in self:
            if product.default_code:
                domain = [
                    ('default_code', '=', product.default_code),
                    ('id', '!=', product.id)
                ]
                if self.search_count(domain) > 0:
                    raise ValidationError(_("A product with this Reference Number already exists")) 