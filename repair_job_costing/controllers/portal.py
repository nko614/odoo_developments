from odoo import http
from odoo.exceptions import AccessError, MissingError
from odoo.http import request

from odoo.addons.portal.controllers import portal
from odoo.addons.portal.controllers.portal import pager as portal_pager


class CustomerPortal(portal.CustomerPortal):

    def _prepare_portal_counter_values(self, counter):
        if counter == 'repair_count':
            return 'repair.order', [], 'read'
        return super()._prepare_portal_counter_values(counter)

    def _repair_stages(self):
        # Stages are shared configuration (names and order only); portal users have no access rows on them.
        return request.env['repair.stage'].sudo().search([])

    @http.route(['/my/repairs', '/my/repairs/page/<int:page>'], type='http', auth='user', website=True)
    def portal_my_repairs(self, page=1, **kw):
        Repair = request.env['repair.order']
        pager = portal_pager(url='/my/repairs', total=Repair.search_count([]), page=page, step=self._items_per_page)
        repairs = Repair.search([], limit=self._items_per_page, offset=pager['offset'])
        request.session['my_repairs_history'] = repairs.ids[:100]
        values = self._prepare_portal_layout_values()
        values.update({
            # Access was checked by the search above; sudo only to display the product and
            # serial number, which portal users can't read directly.
            'repairs': repairs.sudo(),
            'repair_stages': self._repair_stages(),
            'page_name': 'repair',
            'pager': pager,
        })
        return request.render('repair_job_costing.portal_my_repairs', values)

    @http.route(['/my/repairs/<int:repair_id>'], type='http', auth='public', website=True)
    def portal_my_repair(self, repair_id, access_token=None, **kw):
        try:
            repair_sudo = self._document_check_access('repair.order', repair_id, access_token=access_token)
        except (AccessError, MissingError):
            return request.redirect('/my')
        values = self._get_page_view_values(
            repair_sudo, access_token,
            {'repair': repair_sudo, 'repair_stages': self._repair_stages(), 'page_name': 'repair'},
            'my_repairs_history', False, **kw)
        return request.render('repair_job_costing.portal_my_repair', values)
