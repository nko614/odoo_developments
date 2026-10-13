from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    nko_strava_client_id = fields.Char(string='Strava Client ID', config_parameter='nko_strava.strava_client_id')
    nko_strava_client_secret = fields.Char(string='Strava Client Secret', config_parameter='nko_strava.strava_client_secret')
    nko_whoop_client_id = fields.Char(string='WHOOP Client ID', config_parameter='nko_strava.whoop_client_id')
    nko_whoop_client_secret = fields.Char(string='WHOOP Client Secret', config_parameter='nko_strava.whoop_client_secret')

    nko_strava_redirect_uri = fields.Char(string='Strava Redirect URI', compute='_compute_nko_fitness')
    nko_whoop_redirect_uri = fields.Char(string='WHOOP Redirect URI', compute='_compute_nko_fitness')
    nko_strava_callback_domain = fields.Char(string='Strava Callback Domain', compute='_compute_nko_fitness')
    nko_strava_status = fields.Char(string='Strava Status', compute='_compute_nko_fitness')
    nko_whoop_status = fields.Char(string='WHOOP Status', compute='_compute_nko_fitness')
    nko_strava_state = fields.Char(compute='_compute_nko_fitness')
    nko_whoop_state = fields.Char(compute='_compute_nko_fitness')
    nko_strava_error = fields.Text(compute='_compute_nko_fitness')
    nko_whoop_error = fields.Text(compute='_compute_nko_fitness')

    @api.depends('company_id')
    def _compute_nko_fitness(self):
        Conn = self.env['fitness.connection'].sudo()
        conns = {c.provider: c for c in Conn.search([])}
        for rec in self:
            for provider in ('strava', 'whoop'):
                conn = conns.get(provider)
                uri = Conn._redirect_uri_for(provider)
                rec[f'nko_{provider}_redirect_uri'] = uri
                rec[f'nko_{provider}_state'] = conn.state if conn else 'disconnected'
                rec[f'nko_{provider}_status'] = (conn._status_text() if conn else
                                                 f"{'Strava' if provider == 'strava' else 'WHOOP'}: not connected")
                rec[f'nko_{provider}_error'] = conn.last_error if conn else False
            rec.nko_strava_callback_domain = rec.nko_strava_redirect_uri.split('://', 1)[-1].split('/', 1)[0].split(':')[0]

    def _nko_conn(self, provider):
        return self.env['fitness.connection']._get_connection(provider)

    def action_nko_strava_connect(self):
        return self.env['fitness.connection']._action_connect('strava')

    def action_nko_whoop_connect(self):
        return self.env['fitness.connection']._action_connect('whoop')

    def action_nko_strava_disconnect(self):
        self._nko_conn('strava').action_disconnect()
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_nko_whoop_disconnect(self):
        self._nko_conn('whoop').action_disconnect()
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_nko_strava_sync(self):
        return self.env['fitness.connection'].action_sync_provider('strava')

    def action_nko_whoop_sync(self):
        return self.env['fitness.connection'].action_sync_provider('whoop')
