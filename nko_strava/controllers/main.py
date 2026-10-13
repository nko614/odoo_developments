import logging

from odoo import http
from odoo.exceptions import UserError
from odoo.http import request

from ..models.fitness_connection import FitnessApiError

_logger = logging.getLogger(__name__)


class NkoStravaOAuth(http.Controller):

    @http.route('/nko_strava/strava/callback', type='http', auth='user', methods=['GET'])
    def strava_callback(self, code=None, state=None, error=None, scope=None, **kw):
        return self._callback('strava', code, state, error)

    @http.route('/nko_strava/whoop/callback', type='http', auth='user', methods=['GET'])
    def whoop_callback(self, code=None, state=None, error=None, error_description=None, **kw):
        return self._callback('whoop', code, state, error and f"{error}: {error_description or ''}")

    def _redirect(self, conn=None):
        if conn:
            return request.redirect(f'/odoo/action-nko_strava.fitness_connection_action/{conn.id}')
        return request.redirect('/odoo/action-nko_strava.fitness_connection_action')

    def _callback(self, provider, code, state, error):
        if not request.env.user.has_group('base.group_system'):
            raise request.not_found()
        Conn = request.env['fitness.connection'].sudo()
        conn = Conn._get_connection(provider, create=False)
        if not conn or not conn._check_oauth_state(state):
            _logger.warning("nko_strava: %s OAuth callback with invalid state", provider)
            if conn:
                conn.write({'last_error': "Authorization failed: invalid or expired state. Click Connect again."})
            return self._redirect(conn)
        if error or not code:
            conn.write({'oauth_state': False,
                        'last_error': f"Authorization was refused or cancelled: {error or 'no code returned'}"})
            return self._redirect(conn)
        try:
            conn._oauth_exchange_code(code)
        except (UserError, FitnessApiError, ValueError) as e:
            # ValueError: non-JSON token response. Show it on the record, no traceback.
            conn.write({'oauth_state': False, 'last_error': f"Authorization failed: {e}"})
            return self._redirect(conn)
        # Tokens must be durable before anything else can fail or refresh them.
        request.env.cr.commit()
        summary = conn._sync()
        request.env.cr.commit()
        _logger.info("nko_strava: %s connected as %s; first sync: %s", provider, conn.athlete_name, summary)
        conn._write_isolated({'last_message': f"Connected as {conn.athlete_name or '?'}. First sync: {summary}"})
        return self._redirect(conn)
