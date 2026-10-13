"""OAuth connections to Strava and WHOOP, token handling and sync orchestration.

Token safety rules (both providers can rotate the refresh token, WHOOP always does
and invalidates the previous one immediately):

* the access token is refreshed only when it is expired (or expires within 60s),
  or once after the API answered 401 for a token we believed valid;
* the refresh happens in a separate cursor, with the connection row locked
  (SELECT ... FOR UPDATE) so two syncs can never refresh concurrently, and the new
  tokens are committed in that cursor *before* they are used, so a later error in
  the sync transaction cannot roll them back;
* a 400/401 from the token endpoint marks the connection as "reconnect needed" and
  stops - the token endpoint is never retried in a loop.

Because of the separate cursor, the main (sync) transaction never writes the
connection row itself: every status update goes through ``_write_isolated`` too,
otherwise the REPEATABLE READ main transaction would hit a serialization error.
"""
import logging
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import psycopg2
import requests

from odoo import api, fields, models
from odoo.modules import module as odoo_module
from odoo.exceptions import UserError
from odoo.tools import consteq

_logger = logging.getLogger(__name__)

TIMEOUT = 30
EXPIRY_MARGIN = timedelta(seconds=60)

PROVIDERS = {
    'strava': {
        'label': 'Strava',
        'authorize_url': 'https://www.strava.com/oauth/authorize',
        'token_url': 'https://www.strava.com/oauth/token',
        'api_url': 'https://www.strava.com/api/v3',
        'scope': 'read,activity:read_all,profile:read_all',
        'callback': '/nko_strava/strava/callback',
    },
    'whoop': {
        'label': 'WHOOP',
        'authorize_url': 'https://api.prod.whoop.com/oauth/oauth2/auth',
        'token_url': 'https://api.prod.whoop.com/oauth/oauth2/token',
        'api_url': 'https://api.prod.whoop.com/developer',
        'scope': 'offline read:recovery read:cycles read:sleep read:workout read:profile read:body_measurement',
        'callback': '/nko_strava/whoop/callback',
    },
}


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class FitnessReconnectNeeded(Exception):
    """The provider rejected our tokens; the user must click Connect again."""


class FitnessRateLimited(Exception):
    """The provider rate limit is (nearly) reached; stop and resume next run."""


class FitnessApiError(Exception):
    """Any other API error (network, 5xx, unexpected payload)."""


class FitnessConnection(models.Model):
    _name = 'fitness.connection'
    _description = 'Strava / WHOOP Connection'
    _order = 'provider'
    _rec_name = 'provider'

    provider = fields.Selection([('strava', 'Strava'), ('whoop', 'WHOOP')], required=True, readonly=True)
    state = fields.Selection([
        ('disconnected', 'Not connected'),
        ('connected', 'Connected'),
        ('reconnect', 'Reconnect needed'),
    ], default='disconnected', required=True, readonly=True)
    access_token = fields.Char(groups='base.group_system', copy=False)
    refresh_token = fields.Char(groups='base.group_system', copy=False)
    expires_at = fields.Datetime(string='Access Token Expires', readonly=True, copy=False)
    scope = fields.Char(readonly=True)
    athlete_id = fields.Char(string='Athlete / User ID', readonly=True)
    athlete_name = fields.Char(string='Connected As', readonly=True)
    profile_info = fields.Text(readonly=True)
    oauth_state = fields.Char(groups='base.group_system', copy=False)
    last_sync = fields.Datetime(readonly=True, copy=False)
    last_message = fields.Char(readonly=True, copy=False)
    last_error = fields.Text(readonly=True, copy=False)
    status_display = fields.Char(string='Status', compute='_compute_status_display')
    redirect_uri = fields.Char(compute='_compute_redirect_uri')

    _provider_unique = models.Constraint('unique(provider)', "There is already a connection for this provider.")

    # ------------------------------------------------------------------
    # computes / helpers
    # ------------------------------------------------------------------
    @api.depends('state', 'athlete_name', 'last_sync', 'provider')
    def _compute_status_display(self):
        for conn in self:
            conn.status_display = conn._status_text()

    def _status_text(self):
        self.ensure_one()
        label = PROVIDERS[self.provider]['label']
        if self.state == 'disconnected':
            return f"{label}: not connected"
        if self.state == 'reconnect':
            return f"{label}: reconnect needed ({self.athlete_name or 'unknown athlete'})"
        if self.last_sync:
            local = fields.Datetime.context_timestamp(self, self.last_sync)
            sync = local.strftime('%b %d, %Y %H:%M')
        else:
            sync = 'never'
        return f"Connected as {self.athlete_name or '?'} · last sync {sync}"

    @api.depends('provider')
    def _compute_redirect_uri(self):
        for conn in self:
            conn.redirect_uri = self._redirect_uri_for(conn.provider) if conn.provider else False

    @api.model
    def _redirect_uri_for(self, provider):
        base = self.env['ir.config_parameter'].sudo().get_str('web.base.url').rstrip('/')
        return base + PROVIDERS[provider]['callback']

    @api.model
    def _client_credentials(self, provider):
        icp = self.env['ir.config_parameter'].sudo()
        return (icp.get_str(f'nko_strava.{provider}_client_id').strip(),
                icp.get_str(f'nko_strava.{provider}_client_secret').strip())

    @api.model
    def _get_connection(self, provider, create=True):
        conn = self.sudo().search([('provider', '=', provider)], limit=1)
        if not conn and create:
            conn = self.sudo().create({'provider': provider})
        return conn

    def _write_isolated(self, vals):
        """Write ``vals`` on the connection in a separate, immediately committed
        transaction (see module docstring), and drop them from our cache."""
        self.ensure_one()
        with self.env.registry.cursor() as cr:
            cr.execute("SET LOCAL lock_timeout = '15s'")
            self.env(cr=cr, su=True)[self._name].browse(self.id).write(vals)
            cr.commit()
        self.invalidate_recordset(list(vals))

    # ------------------------------------------------------------------
    # OAuth
    # ------------------------------------------------------------------
    @api.model
    def _action_connect(self, provider):
        client_id, client_secret = self._client_credentials(provider)
        if not client_id or not client_secret:
            raise UserError(f"Enter the {PROVIDERS[provider]['label']} Client ID and Client Secret "
                            f"in Settings first (and save).")
        conn = self._get_connection(provider)
        state = secrets.token_urlsafe(24)
        conn.oauth_state = state
        params = {
            'client_id': client_id,
            'redirect_uri': self._redirect_uri_for(provider),
            'response_type': 'code',
            'scope': PROVIDERS[provider]['scope'],
            'state': state,
        }
        if provider == 'strava':
            params['approval_prompt'] = 'auto'
        return {
            'type': 'ir.actions.act_url',
            'url': f"{PROVIDERS[provider]['authorize_url']}?{urlencode(params)}",
            'target': 'self',
        }

    def action_connect(self):
        self.ensure_one()
        return self._action_connect(self.provider)

    def _check_oauth_state(self, state):
        self.ensure_one()
        expected = self.sudo().oauth_state
        return bool(expected and state and consteq(expected, state))

    def _oauth_exchange_code(self, code):
        """Exchange the authorization code, store tokens and the athlete/profile."""
        self.ensure_one()
        provider = self.provider
        client_id, client_secret = self._client_credentials(provider)
        data = {
            'client_id': client_id,
            'client_secret': client_secret,
            'code': code,
            'grant_type': 'authorization_code',
        }
        if provider == 'whoop':
            data['redirect_uri'] = self._redirect_uri_for(provider)
        try:
            resp = requests.post(PROVIDERS[provider]['token_url'], data=data, timeout=TIMEOUT)
        except requests.RequestException as e:
            raise UserError(f"Could not reach {PROVIDERS[provider]['label']}: {e}")
        if resp.status_code != 200:
            raise UserError(f"{PROVIDERS[provider]['label']} refused the authorization code "
                            f"(HTTP {resp.status_code}): {resp.text[:300]}")
        payload = resp.json()
        vals = self._token_vals(payload)
        vals.update({
            'state': 'connected',
            'oauth_state': False,
            'last_error': False,
            'scope': payload.get('scope') or PROVIDERS[provider]['scope'],
        })
        if provider == 'strava':
            athlete = payload.get('athlete') or {}
            vals['athlete_id'] = str(athlete.get('id') or '')
            vals['athlete_name'] = " ".join(p for p in (athlete.get('firstname'), athlete.get('lastname')) if p)
        self.sudo().write(vals)
        if provider == 'whoop':
            self._whoop_fetch_profile(vals['access_token'])
        return True

    @api.model
    def _token_vals(self, payload):
        if not payload.get('access_token'):
            raise FitnessApiError(f"Token response without access_token: {str(payload)[:200]}")
        if payload.get('expires_at'):
            expires = datetime.fromtimestamp(int(payload['expires_at']), timezone.utc).replace(tzinfo=None)
        else:
            expires = utcnow() + timedelta(seconds=int(payload.get('expires_in') or 3600))
        vals = {'access_token': payload['access_token'], 'expires_at': expires.replace(microsecond=0)}
        if payload.get('refresh_token'):
            vals['refresh_token'] = payload['refresh_token']
        return vals

    def _whoop_fetch_profile(self, access_token):
        headers = {'Authorization': f'Bearer {access_token}'}
        base = PROVIDERS['whoop']['api_url']
        # best effort: a failure here must never lose the tokens just obtained
        try:
            profile = requests.get(f'{base}/v2/user/profile/basic', headers=headers, timeout=TIMEOUT)
            body = requests.get(f'{base}/v2/user/measurement/body', headers=headers, timeout=TIMEOUT)
            p = profile.json() if profile.status_code == 200 else None
            b = body.json() if body.status_code == 200 else None
        except (requests.RequestException, ValueError) as e:
            _logger.warning("WHOOP profile fetch failed: %s", e)
            return
        vals = {}
        if p:
            vals['athlete_id'] = str(p.get('user_id') or '')
            vals['athlete_name'] = " ".join(x for x in (p.get('first_name'), p.get('last_name')) if x)
        if b:
            parts = []
            if b.get('height_meter'):
                inches = b['height_meter'] / 0.0254
                parts.append(f"Height {int(inches // 12)}' {round(inches % 12)}\"")
            if b.get('weight_kilogram'):
                parts.append(f"Weight {b['weight_kilogram'] * 2.20462:.1f} lb")
            if b.get('max_heart_rate'):
                parts.append(f"Max HR {b['max_heart_rate']} bpm")
            vals['profile_info'] = " · ".join(parts)
        if vals:
            self.sudo().write(vals)

    def action_disconnect(self):
        for conn in self.sudo():
            conn.write({
                'state': 'disconnected', 'access_token': False, 'refresh_token': False,
                'expires_at': False, 'oauth_state': False, 'last_error': False,
                'last_message': 'Disconnected. Synced data was kept.',
            })
        return True

    # ------------------------------------------------------------------
    # tokens
    # ------------------------------------------------------------------
    def _get_access_token(self, force_refresh=False, rejected_token=None):
        """Return a usable access token, refreshing it only when expired.

        ``force_refresh`` is used once after a 401 on a token we thought valid:
        if another process already rotated it (stored token != ``rejected_token``)
        we take the stored one instead of refreshing again.
        """
        self.ensure_one()
        for attempt in range(3):
            try:
                return self._get_access_token_locked(force_refresh, rejected_token)
            except (psycopg2.errors.SerializationFailure, psycopg2.errors.LockNotAvailable):
                if attempt == 2:
                    raise FitnessApiError("Another sync is refreshing the token; try again in a minute.")
        return None  # unreachable

    def _get_access_token_locked(self, force_refresh, rejected_token):
        with self.env.registry.cursor() as cr:
            cr.execute("SET LOCAL lock_timeout = '15s'")
            cr.execute(
                "SELECT state, access_token, refresh_token, expires_at FROM fitness_connection "
                "WHERE id = %s FOR UPDATE", [self.id])
            row = cr.fetchone()
            if not row:
                raise FitnessReconnectNeeded("Connection record not found.")
            state, access_token, refresh_token, expires_at = row
            if state != 'connected' or not access_token:
                raise FitnessReconnectNeeded("Not connected.")
            now = utcnow()
            if force_refresh:
                if rejected_token and access_token != rejected_token and expires_at and expires_at > now + EXPIRY_MARGIN:
                    return access_token  # somebody else already refreshed
            elif expires_at and expires_at > now + EXPIRY_MARGIN:
                return access_token
            if not refresh_token:
                self._mark_reconnect_cr(cr, "Access token expired and no refresh token is stored.")
                raise FitnessReconnectNeeded("No refresh token.")

            # --- the only place a refresh request is ever made ---
            client_id, client_secret = self._client_credentials(self.provider)
            data = {
                'client_id': client_id,
                'client_secret': client_secret,
                'grant_type': 'refresh_token',
                'refresh_token': refresh_token,
            }
            if self.provider == 'whoop':
                data['scope'] = 'offline'
            _logger.info("%s: refreshing access token (expired at %s)", self.provider, expires_at)
            try:
                resp = requests.post(PROVIDERS[self.provider]['token_url'], data=data, timeout=TIMEOUT)
            except requests.RequestException as e:
                # network error: the old refresh token is presumably still valid, keep it
                raise FitnessApiError(f"Token refresh failed (network): {e}")
            if resp.status_code in (400, 401, 403):
                msg = (f"{PROVIDERS[self.provider]['label']} rejected the refresh token "
                       f"(HTTP {resp.status_code}: {resp.text[:200]}). Click Connect again.")
                self._mark_reconnect_cr(cr, msg)
                raise FitnessReconnectNeeded(msg)
            if resp.status_code != 200:
                raise FitnessApiError(f"Token refresh failed: HTTP {resp.status_code} {resp.text[:200]}")
            vals = self._token_vals(resp.json())
            env = self.env(cr=cr, su=True)
            env[self._name].browse(self.id).write(vals)
            cr.commit()  # persisted BEFORE anything uses the new token
        self.invalidate_recordset(['access_token', 'refresh_token', 'expires_at'])
        return vals['access_token']

    def _mark_reconnect_cr(self, cr, message):
        self.env(cr=cr, su=True)[self._name].browse(self.id).write({
            'state': 'reconnect', 'last_error': message,
        })
        cr.commit()
        self.invalidate_recordset(['state', 'last_error'])

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------
    def _api_get(self, path, params=None):
        """GET on the provider API with token handling. Returns the response."""
        self.ensure_one()
        url = PROVIDERS[self.provider]['api_url'] + path
        token = self._get_access_token()
        refreshed = False
        while True:
            try:
                resp = requests.get(url, params=params, headers={'Authorization': f'Bearer {token}'},
                                    timeout=TIMEOUT)
            except requests.RequestException as e:
                raise FitnessApiError(f"GET {path} failed: {e}")
            if resp.status_code == 401:
                if refreshed:
                    msg = f"{PROVIDERS[self.provider]['label']} rejected a freshly refreshed token on {path}. Click Connect again."
                    self._write_isolated({'state': 'reconnect', 'last_error': msg})
                    raise FitnessReconnectNeeded(msg)
                token = self._get_access_token(force_refresh=True, rejected_token=token)
                refreshed = True
                continue
            if resp.status_code == 429:
                raise FitnessRateLimited(f"{PROVIDERS[self.provider]['label']} rate limit reached (HTTP 429).")
            if resp.status_code >= 400:
                raise FitnessApiError(f"GET {path}: HTTP {resp.status_code} {resp.text[:200]}")
            return resp

    # ------------------------------------------------------------------
    # sync
    # ------------------------------------------------------------------
    def action_sync(self):
        messages = []
        for conn in self.sudo():
            if conn.state != 'connected':
                raise UserError(f"{PROVIDERS[conn.provider]['label']} is not connected.")
            messages.append(conn._sync())
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Sync finished',
                'message': " / ".join(messages),
                'type': 'warning' if any('rror' in m or 'econnect' in m for m in messages) else 'success',
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }

    @api.model
    def action_sync_provider(self, provider):
        """Used by the Sync Now buttons on the data list views."""
        conn = self._get_connection(provider, create=False)
        if not conn or conn.state != 'connected':
            raise UserError(f"{PROVIDERS[provider]['label']} is not connected. "
                            f"Go to Strava > Configuration > Settings and click Connect.")
        return conn.action_sync()

    def _sync(self):
        """Sync one connection; never raises (errors are stored on the record).
        Returns a short human message."""
        self.ensure_one()
        label = PROVIDERS[self.provider]['label']
        try:
            if self.provider == 'strava':
                summary = self.env['strava.activity']._sync_from_strava(self)
            else:
                summary = self.env['whoop.cycle']._sync_from_whoop(self)
        except FitnessReconnectNeeded as e:
            # state + last_error already committed by the token code
            _logger.warning("%s sync: reconnect needed: %s", label, e)
            return f"{label}: reconnect needed"
        except Exception as e:  # noqa: BLE001 - report every failure on the record
            _logger.warning("%s sync failed: %s", label, e, exc_info=True)
            self._write_isolated({'last_error': f"{type(e).__name__}: {e}"[:2000]})
            return f"{label}: error - {e}"
        self._write_isolated({
            'last_sync': fields.Datetime.now(),
            'last_error': False,
            'last_message': summary,
        })
        return f"{label}: {summary}"

    @api.model
    def _cron_sync(self):
        for conn in self.sudo().search([('state', '=', 'connected')]):
            conn._sync()
            if not odoo_module.current_test:
                self.env.cr.commit()
