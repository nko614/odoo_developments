"""Tests for nko_strava. All HTTP is mocked: no request ever leaves the machine."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import requests

from odoo.tests import TransactionCase, tagged

from odoo.addons.nko_strava.models import fitness_connection as fc
from odoo.addons.nko_strava.models.sample_data import (
    ROUTES, decode_polyline, densify, encode_polyline,
)

STRAVA_TOKEN = fc.PROVIDERS['strava']['token_url']
WHOOP_TOKEN = fc.PROVIDERS['whoop']['token_url']
STRAVA_API = fc.PROVIDERS['strava']['api_url']
WHOOP_API = fc.PROVIDERS['whoop']['api_url']


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class FakeResponse:
    def __init__(self, status=200, payload=None, headers=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}
        self.text = str(self._payload)

    def json(self):
        return self._payload


class FakeHttp:
    """Routes requests.get / requests.post to handlers and records every call."""

    def __init__(self, get_handler=None, post_handler=None):
        self.get_handler = get_handler
        self.post_handler = post_handler
        self.calls = []  # (method, url, params_or_data)

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(('GET', url, dict(params or {}), headers))
        if not self.get_handler:
            raise AssertionError(f"unexpected GET {url}")
        return self.get_handler(url, dict(params or {}), headers or {})

    def post(self, url, data=None, timeout=None, **kw):
        self.calls.append(('POST', url, dict(data or {}), None))
        if not self.post_handler:
            raise AssertionError(f"unexpected POST {url}")
        return self.post_handler(url, dict(data or {}))

    def patch(self):
        return patch.multiple(requests, get=self.get, post=self.post)

    def posts(self):
        return [c for c in self.calls if c[0] == 'POST']

    def gets(self, suffix=None):
        return [c for c in self.calls if c[0] == 'GET' and (suffix is None or c[1].endswith(suffix))]


def strava_summary(i, start, with_map=True):
    data = {
        'id': 9000000000 + i,  # > 2^31: ids must not be stored as int4
        'name': f'Activity {i}',
        'sport_type': 'Run',
        'type': 'Run',
        'start_date': start.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'start_date_local': (start - timedelta(hours=4)).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'timezone': '(GMT-05:00) America/New_York',
        'distance': 5000.0,
        'moving_time': 1650,
        'elapsed_time': 1700,
        'total_elevation_gain': 30.0,
        'average_speed': 3.0303,
        'max_speed': 4.5,
        'average_heartrate': 150.0,
        'max_heartrate': 172.0,
        'kudos_count': 2,
        'achievement_count': 1,
        'pr_count': 0,
        'trainer': False,
        'commute': False,
        'private': False,
        'gear_id': 'g1',
        'start_latlng': [42.93, -78.87],
    }
    if with_map:
        data['map'] = {'summary_polyline': encode_polyline([(42.93, -78.87), (42.94, -78.86), (42.95, -78.85)])}
    return data


@tagged('post_install', '-at_install', 'nko_strava')
class NkoStravaCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # separate cursors (token refresh / isolated writes) must share the test transaction
        cls.enterClassContext(cls.registry_test_mode())
        icp = cls.env['ir.config_parameter'].sudo()
        icp.set_str('web.base.url', 'http://localhost:8070')
        for provider in ('strava', 'whoop'):
            icp.set_str(f'nko_strava.{provider}_client_id', f'{provider}-id')
            icp.set_str(f'nko_strava.{provider}_client_secret', f'{provider}-secret')
        icp.search([('key', '=', 'nko_strava.whoop_synced_at')]).unlink()
        Conn = cls.env['fitness.connection'].sudo()
        Conn.search([]).unlink()
        cls.env['strava.activity'].search([]).unlink()
        for model in ('whoop.recovery', 'whoop.sleep', 'whoop.cycle', 'whoop.workout', 'strava.athlete.stat'):
            cls.env[model].search([]).unlink()
        cls.strava = Conn.create({'provider': 'strava'})
        cls.whoop = Conn.create({'provider': 'whoop'})

    def connect(self, conn, expires_in=7200, access='AT-old', refresh='RT-old'):
        conn.sudo().write({
            'state': 'connected',
            'access_token': access,
            'refresh_token': refresh,
            'expires_at': utcnow() + timedelta(seconds=expires_in),
            'athlete_name': 'Test Athlete',
            'athlete_id': '42',
        })
        self.env.flush_all()

    def db_tokens(self, conn):
        self.env.flush_all()
        self.env.cr.execute(
            "SELECT state, access_token, refresh_token, last_error FROM fitness_connection WHERE id = %s",
            [conn.id])
        return self.env.cr.fetchone()


@tagged('post_install', '-at_install', 'nko_strava')
class TestOAuth(NkoStravaCase):

    def test_connect_builds_authorize_url_with_state(self):
        action = self.env['fitness.connection']._action_connect('strava')
        url = urlparse(action['url'])
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        self.assertEqual(f'{url.scheme}://{url.netloc}{url.path}', 'https://www.strava.com/oauth/authorize')
        self.assertEqual(q['client_id'], 'strava-id')
        self.assertEqual(q['redirect_uri'], 'http://localhost:8070/nko_strava/strava/callback')
        self.assertEqual(q['response_type'], 'code')
        self.assertEqual(q['approval_prompt'], 'auto')
        self.assertEqual(q['scope'], 'read,activity:read_all,profile:read_all')
        self.assertGreaterEqual(len(q['state']), 8)
        self.assertTrue(self.strava._check_oauth_state(q['state']))
        self.assertFalse(self.strava._check_oauth_state('forged-state-value'))
        self.assertFalse(self.strava._check_oauth_state(None))

        action = self.env['fitness.connection']._action_connect('whoop')
        q = {k: v[0] for k, v in parse_qs(urlparse(action['url']).query).items()}
        self.assertIn('offline', q['scope'].split())
        self.assertEqual(q['redirect_uri'], 'http://localhost:8070/nko_strava/whoop/callback')
        self.assertGreaterEqual(len(q['state']), 8)

    def test_connect_requires_credentials(self):
        self.env['ir.config_parameter'].sudo().set_str('nko_strava.strava_client_id', '')
        with self.assertRaises(Exception):
            self.env['fitness.connection']._action_connect('strava')

    def test_strava_code_exchange(self):
        expires = int((utcnow() + timedelta(hours=6)).replace(tzinfo=timezone.utc).timestamp())

        def post(url, data):
            self.assertEqual(url, STRAVA_TOKEN)
            self.assertEqual(data['grant_type'], 'authorization_code')
            self.assertEqual(data['code'], 'the-code')
            self.assertEqual(data['client_secret'], 'strava-secret')
            return FakeResponse(200, {
                'token_type': 'Bearer', 'access_token': 'AT-1', 'refresh_token': 'RT-1',
                'expires_at': expires, 'athlete': {'id': 777, 'firstname': 'Test', 'lastname': 'User'},
            })
        http = FakeHttp(post_handler=post)
        self.env['fitness.connection']._action_connect('strava')
        with http.patch():
            self.strava._oauth_exchange_code('the-code')
        self.assertEqual(self.strava.state, 'connected')
        self.assertEqual(self.strava.sudo().access_token, 'AT-1')
        self.assertEqual(self.strava.sudo().refresh_token, 'RT-1')
        self.assertFalse(self.strava.sudo().oauth_state)
        self.assertEqual(self.strava.athlete_name, 'Test User')
        self.assertEqual(self.strava.athlete_id, '777')
        self.assertIn('Connected as Test User', self.strava.status_display)
        self.assertEqual(len(http.posts()), 1)

    def test_whoop_code_exchange_fetches_profile(self):
        def post(url, data):
            self.assertEqual(url, WHOOP_TOKEN)
            self.assertEqual(data['redirect_uri'], 'http://localhost:8070/nko_strava/whoop/callback')
            return FakeResponse(200, {'access_token': 'W-AT', 'refresh_token': 'W-RT', 'expires_in': 3600})

        def get(url, params, headers):
            self.assertEqual(headers['Authorization'], 'Bearer W-AT')
            if url.endswith('/v2/user/profile/basic'):
                return FakeResponse(200, {'user_id': 55555, 'first_name': 'Test', 'last_name': 'User'})
            if url.endswith('/v2/user/measurement/body'):
                return FakeResponse(200, {'height_meter': 1.83, 'weight_kilogram': 80.0, 'max_heart_rate': 190})
            raise AssertionError(url)
        http = FakeHttp(get, post)
        with http.patch():
            self.whoop._oauth_exchange_code('code')
        self.assertEqual(self.whoop.state, 'connected')
        self.assertEqual(self.whoop.athlete_name, 'Test User')
        self.assertEqual(self.whoop.athlete_id, '55555')
        self.assertIn('176.4 lb', self.whoop.profile_info)
        self.assertGreater(self.whoop.expires_at, utcnow() + timedelta(minutes=55))

    def test_code_exchange_error(self):
        http = FakeHttp(post_handler=lambda url, data: FakeResponse(400, {'message': 'Bad Request'}))
        with http.patch(), self.assertRaises(Exception):
            self.strava._oauth_exchange_code('bad')
        self.assertNotEqual(self.strava.state, 'connected')

    def test_redirect_uris_in_settings(self):
        settings = self.env['res.config.settings'].create({})
        self.assertEqual(settings.nko_strava_redirect_uri, 'http://localhost:8070/nko_strava/strava/callback')
        self.assertEqual(settings.nko_whoop_redirect_uri, 'http://localhost:8070/nko_strava/whoop/callback')
        self.assertEqual(settings.nko_strava_callback_domain, 'localhost')


@tagged('post_install', '-at_install', 'nko_strava')
class TestTokens(NkoStravaCase):

    def test_no_refresh_when_token_valid(self):
        self.connect(self.strava, expires_in=3600)
        http = FakeHttp()
        with http.patch():
            token = self.strava._get_access_token()
        self.assertEqual(token, 'AT-old')
        self.assertEqual(http.posts(), [])

    def test_refresh_when_expired_and_within_margin(self):
        for expires_in in (-10, 30):  # already expired, and expiring within 60s
            self.connect(self.whoop, expires_in=expires_in)

            def post(url, data):
                self.assertEqual(url, WHOOP_TOKEN)
                self.assertEqual(data['grant_type'], 'refresh_token')
                self.assertEqual(data['refresh_token'], 'RT-old')
                self.assertEqual(data['scope'], 'offline')
                return FakeResponse(200, {'access_token': 'AT-new', 'refresh_token': 'RT-new', 'expires_in': 3600})
            http = FakeHttp(post_handler=post)
            with http.patch():
                self.assertEqual(self.whoop._get_access_token(), 'AT-new')
                # second call: token is fresh now, no new refresh
                self.assertEqual(self.whoop._get_access_token(), 'AT-new')
            self.assertEqual(len(http.posts()), 1)
            self.assertEqual(self.whoop.sudo().refresh_token, 'RT-new')
            self.assertEqual(self.db_tokens(self.whoop)[2], 'RT-new')

    def test_rotated_token_persisted_even_if_sync_raises(self):
        self.connect(self.strava, expires_in=-60)
        events = []

        def post(url, data):
            events.append('refresh')
            return FakeResponse(200, {'access_token': 'AT-new', 'refresh_token': 'RT-rotated',
                                      'expires_at': int(utcnow().replace(tzinfo=timezone.utc).timestamp()) + 21600})

        def get(url, params, headers):
            events.append('get')
            self.assertEqual(headers['Authorization'], 'Bearer AT-new')
            return FakeResponse(500, {'message': 'boom'})

        orig_cursor = self.registry.cursor

        def spy_cursor(*args, **kwargs):
            cr = orig_cursor(*args, **kwargs)
            orig_commit = cr.commit

            def commit():
                events.append('commit')
                return orig_commit()
            cr.commit = commit
            return cr

        http = FakeHttp(get, post)
        with http.patch(), patch.object(self.registry, 'cursor', spy_cursor):
            msg = self.strava._sync()
        self.assertIn('error', msg)
        # the rotated token was committed in its own cursor before it was used
        self.assertEqual(events[0], 'refresh')
        self.assertIn('get', events)
        self.assertLess(events.index('commit'), events.index('get'))
        state, access, refresh, last_error = self.db_tokens(self.strava)
        self.assertEqual((state, access, refresh), ('connected', 'AT-new', 'RT-rotated'))
        self.assertTrue(last_error)

        # an exception raised straight out of a data sync must not lose the token either
        # (plain try/except: Odoo's assertRaises would roll back to a savepoint, which in
        # tests also undoes the "separate" cursor since it shares the test transaction)
        self.connect(self.strava, expires_in=-60, access='AT-x', refresh='RT-x')
        raised = None
        with http.patch():
            try:
                self.env['strava.activity']._sync_from_strava(self.strava)
            except fc.FitnessApiError as e:
                raised = e
        self.assertTrue(raised)
        self.assertEqual(self.db_tokens(self.strava)[2], 'RT-rotated')

    def test_token_endpoint_400_marks_reconnect_without_retry(self):
        self.connect(self.whoop, expires_in=-60)
        http = FakeHttp(
            get_handler=lambda *a: (_ for _ in ()).throw(AssertionError('no API call expected')),
            post_handler=lambda url, data: FakeResponse(400, {'error': 'invalid_grant'}))
        with http.patch():
            msg = self.whoop._sync()
            self.assertIn('reconnect', msg)
            self.env['fitness.connection']._cron_sync()  # skipped: not connected anymore
        self.assertEqual(len(http.posts()), 1)
        self.assertEqual(http.gets(), [])
        state, _a, refresh, last_error = self.db_tokens(self.whoop)
        self.assertEqual(state, 'reconnect')
        self.assertEqual(refresh, 'RT-old')
        self.assertIn('invalid_grant', last_error)

    def test_401_triggers_one_refresh_then_reconnect(self):
        self.connect(self.strava, expires_in=3600)
        http = FakeHttp(
            get_handler=lambda url, params, headers: FakeResponse(401, {'message': 'Authorization Error'}),
            post_handler=lambda url, data: FakeResponse(200, {
                'access_token': 'AT-2', 'refresh_token': 'RT-2',
                'expires_at': int(utcnow().replace(tzinfo=timezone.utc).timestamp()) + 21600}))
        with http.patch():
            msg = self.strava._sync()
        self.assertIn('reconnect', msg)
        self.assertEqual(len(http.posts()), 1)
        self.assertEqual(len(http.gets()), 2)
        state, access, refresh, _e = self.db_tokens(self.strava)
        self.assertEqual((state, access, refresh), ('reconnect', 'AT-2', 'RT-2'))

    def test_network_error_on_refresh_keeps_tokens(self):
        self.connect(self.strava, expires_in=-60)

        def post(url, data):
            raise requests.ConnectionError('offline')
        http = FakeHttp(post_handler=post)
        with http.patch():
            msg = self.strava._sync()
        self.assertIn('error', msg)
        state, access, refresh, _e = self.db_tokens(self.strava)
        self.assertEqual((state, access, refresh), ('connected', 'AT-old', 'RT-old'))

    def test_unconnected_providers_skipped_by_cron(self):
        http = FakeHttp()
        with http.patch():
            self.env['fitness.connection']._cron_sync()
        self.assertEqual(http.calls, [])


@tagged('post_install', '-at_install', 'nko_strava')
class TestStravaSync(NkoStravaCase):

    def _handler(self, pages, extra=()):
        index = {a['id']: a for page in pages.values() for a in page}
        index.update({a['id']: a for a in extra})

        def get(url, params, headers):
            path = url[len(STRAVA_API):]
            if path == '/athlete':
                return FakeResponse(200, {'id': 42, 'bikes': [], 'shoes': [{'id': 'g1', 'name': 'Pegasus 41'}]})
            if path == '/athlete/activities':
                return FakeResponse(200, pages.get(params['page'], []))
            if path.startswith('/activities/'):
                base = dict(index[int(path.rsplit('/', 1)[1])])
                base.update({'calories': 412.0, 'description': 'detail text', 'device_name': 'Garmin',
                             'gear': {'id': 'g1', 'name': 'Pegasus 41'}})
                return FakeResponse(200, base)
            if path == '/athletes/42/stats':
                return FakeResponse(200, {'ytd_run_totals': {'count': 3, 'distance': 16093.44,
                                                             'moving_time': 5400, 'elevation_gain': 100}})
            raise AssertionError(url)
        return get

    def test_full_then_incremental_sync_with_pagination(self):
        self.connect(self.strava)
        base = utcnow() - timedelta(days=400)
        all_acts = [strava_summary(i, base + timedelta(days=i)) for i in range(203)]
        pages = {1: all_acts[:200], 2: all_acts[200:]}
        http = FakeHttp(self._handler(pages))
        with http.patch():
            msg = self.strava._sync()
        self.assertIn('203 new', msg, msg)
        list_calls = http.gets('/athlete/activities')
        self.assertEqual([c[2]['page'] for c in list_calls], [1, 2])
        self.assertTrue(all(c[2]['after'] == 0 and c[2]['per_page'] == 200 for c in list_calls))
        self.assertEqual(http.posts(), [])  # token valid: never refreshed

        Act = self.env['strava.activity']
        self.assertEqual(Act.search_count([('is_sample', '=', False)]), 203)
        first = Act.search([('strava_id', '=', '9000000000')])
        self.assertEqual(first.sport_group, 'run')
        self.assertAlmostEqual(first.distance_mi, 3.11, places=2)
        self.assertEqual(first.moving_time_display, '0:27:30')
        self.assertEqual(first.pace_display, '8:51 /mi')
        self.assertEqual(first.gear_name, 'Pegasus 41')
        self.assertTrue(first.has_route)
        self.assertEqual(first.strava_url, 'https://www.strava.com/activities/9000000000')
        # details pulled for a limited number of most recent activities
        self.assertEqual(Act.search_count([('details_fetched', '=', True)]), 25)
        newest = Act.search([], limit=1)
        self.assertEqual(newest.calories, 412.0)
        self.assertEqual(newest.description, 'detail text')
        stat = self.env['strava.athlete.stat'].search([('period', '=', 'ytd'), ('sport', '=', 'run')])
        self.assertAlmostEqual(stat.distance_mi, 10.0, places=1)
        self.assertIn('Connected as Test Athlete', self.strava.status_display)
        self.assertTrue(self.strava.last_sync)

        # incremental: `after` = latest start - 1s (latest is older than the 3-day resync window)
        latest = Act.search([], order='start_date desc', limit=1).start_date
        new_act = strava_summary(500, utcnow() - timedelta(hours=2))
        http = FakeHttp(self._handler({1: [all_acts[-1], new_act]}, extra=all_acts))
        with http.patch():
            msg = self.strava._sync()
        list_calls = http.gets('/athlete/activities')
        self.assertEqual(len(list_calls), 1)
        expected_after = int((latest - timedelta(seconds=1)).replace(tzinfo=timezone.utc).timestamp())
        self.assertEqual(list_calls[0][2]['after'], expected_after)
        self.assertIn('1 new, 1 updated', msg)
        self.assertEqual(Act.search_count([('is_sample', '=', False)]), 204)

        # now the newest activity is 2h old -> after = now - 3 days (re-fetch recent edits)
        http = FakeHttp(self._handler({1: []}, extra=all_acts + [new_act]))
        with http.patch():
            self.strava._sync()
        after = http.gets('/athlete/activities')[0][2]['after']
        now_epoch = utcnow().replace(tzinfo=timezone.utc).timestamp()
        self.assertAlmostEqual(after, now_epoch - 3 * 86400, delta=120)

    def test_rate_limit_stops_sync(self):
        self.connect(self.strava)
        handler = self._handler({})

        def get(url, params, headers):
            resp = handler(url, params, headers)
            if url.endswith('/athlete/activities'):
                resp = FakeResponse(200, [strava_summary(i, utcnow() - timedelta(days=300 - i)) for i in range(200)],
                                    headers={'X-ReadRateLimit-Limit': '100,1000', 'X-ReadRateLimit-Usage': '97,300'})
            return resp
        http = FakeHttp(get)
        with http.patch():
            msg = self.strava._sync()
        self.assertIn('rate limit', msg)
        self.assertEqual(len(http.gets('/athlete/activities')), 1)  # stopped after page 1
        self.assertEqual(self.env['strava.activity'].search_count([]), 200)  # page 1 kept

    def test_activity_without_polyline(self):
        self.connect(self.strava)
        indoor = strava_summary(1, utcnow() - timedelta(days=1), with_map=False)
        indoor.update({'sport_type': 'VirtualRide', 'trainer': True, 'start_latlng': [],
                       'average_speed': 8.0, 'max_speed': 12.0})
        empty_map = strava_summary(2, utcnow() - timedelta(days=2), with_map=False)
        empty_map['map'] = {'id': 'a2', 'summary_polyline': ''}
        http = FakeHttp(self._handler({1: [indoor, empty_map]}))
        with http.patch():
            self.strava._sync()
        Act = self.env['strava.activity']
        acts = Act.search([('strava_id', 'in', ['9000000001', '9000000002'])])
        self.assertEqual(len(acts), 2)
        self.assertFalse(any(acts.mapped('summary_polyline')))
        self.assertFalse(any(acts.mapped('has_route')))
        ride = acts.filtered(lambda a: a.sport_type == 'VirtualRide')
        self.assertEqual(ride.sport_group, 'ride')
        self.assertEqual(ride.pace_display, '17.9 mph')
        self.assertEqual((ride.start_lat, ride.start_lng), (0.0, 0.0))
        self.assertFalse(Act.get_map_routes([('id', 'in', acts.ids)]))
        # the form (with the map widget) still renders for it
        Act.get_views([(False, 'form')])
        acts[0].read(['summary_polyline', 'pace_display'])


@tagged('post_install', '-at_install', 'nko_strava')
class TestWhoopSync(NkoStravaCase):

    def test_whoop_sync_with_next_token(self):
        self.connect(self.whoop)
        start = utcnow() - timedelta(days=3)

        def iso(dt):
            return dt.strftime('%Y-%m-%dT%H:%M:%S.000Z')

        def cycle(i):
            s = start + timedelta(days=i)
            return {'id': 1000 + i, 'start': iso(s), 'end': iso(s + timedelta(days=1)), 'timezone_offset': '-04:00',
                    'score_state': 'SCORED',
                    'score': {'strain': 10.5 + i, 'kilojoule': 10000.0, 'average_heart_rate': 66, 'max_heart_rate': 170}}

        def get(url, params, headers):
            path = url[len(WHOOP_API):]
            self.assertLessEqual(params['limit'], 25)
            self.assertIn('start', params)
            if path == '/v2/cycle':
                if params.get('nextToken') == 'page2':
                    return FakeResponse(200, {'records': [cycle(2)], 'next_token': None})
                return FakeResponse(200, {'records': [cycle(0), cycle(1)], 'next_token': 'page2'})
            if path == '/v2/recovery':
                return FakeResponse(200, {'records': [{
                    'cycle_id': 1000 + i, 'sleep_id': f'sl-{i}', 'score_state': 'SCORED',
                    'created_at': iso(start + timedelta(days=i, hours=8)),
                    'score': {'user_calibrating': False, 'recovery_score': score, 'resting_heart_rate': 52,
                              'hrv_rmssd_milli': 61.2, 'spo2_percentage': 96.5, 'skin_temp_celsius': 33.7}}
                    for i, score in enumerate((80, 50, 20))]})
            if path == '/v2/activity/sleep':
                return FakeResponse(200, {'records': [{
                    'id': 'ecfc6a15-4661-442f-a9a4-f160dd7afae8', 'cycle_id': 1000,
                    'start': iso(start - timedelta(hours=1)), 'end': iso(start + timedelta(hours=7)),
                    'timezone_offset': '-04:00', 'nap': False, 'score_state': 'SCORED',
                    'score': {'stage_summary': {
                        'total_in_bed_time_milli': 8 * 3600000, 'total_awake_time_milli': 1800000,
                        'total_light_sleep_time_milli': 4 * 3600000, 'total_slow_wave_sleep_time_milli': 5400000,
                        'total_rem_sleep_time_milli': 5400000, 'sleep_cycle_count': 4, 'disturbance_count': 9},
                        'sleep_needed': {'baseline_milli': 27000000, 'need_from_sleep_debt_milli': 0,
                                         'need_from_recent_strain_milli': 0, 'need_from_recent_nap_milli': 0},
                        'respiratory_rate': 15.1, 'sleep_performance_percentage': 91,
                        'sleep_consistency_percentage': 80, 'sleep_efficiency_percentage': 93.75}}]})
            if path == '/v2/activity/workout':
                return FakeResponse(200, {'records': [{
                    'id': 'w-1', 'sport_name': 'running', 'sport_id': 0, 'start': iso(start + timedelta(hours=10)),
                    'end': iso(start + timedelta(hours=11)), 'timezone_offset': '-04:00', 'score_state': 'SCORED',
                    'score': {'strain': 11.2, 'average_heart_rate': 150, 'max_heart_rate': 180, 'kilojoule': 2500,
                              'percent_recorded': 100, 'distance_meter': 10000, 'altitude_gain_meter': 50,
                              'zone_durations': {'zone_two_milli': 1200000, 'zone_three_milli': 1800000}}}]})
            raise AssertionError(url)

        http = FakeHttp(get)
        with http.patch():
            msg = self.whoop._sync()
        self.assertIn('3 new / 0 updated cycles', msg, msg)
        cycle_calls = http.gets('/v2/cycle')
        self.assertEqual(len(cycle_calls), 2)
        self.assertNotIn('nextToken', cycle_calls[0][2])
        self.assertEqual(cycle_calls[1][2]['nextToken'], 'page2')
        first_start = datetime.strptime(cycle_calls[0][2]['start'], '%Y-%m-%dT%H:%M:%S.000Z')
        self.assertAlmostEqual((utcnow() - first_start).days, 365, delta=1)
        self.assertEqual(http.posts(), [])

        Cycle, Rec, Sleep, Work = (self.env[m] for m in ('whoop.cycle', 'whoop.recovery', 'whoop.sleep', 'whoop.workout'))
        self.assertEqual(Cycle.search_count([]), 3)
        self.assertAlmostEqual(Cycle.search([('whoop_id', '=', '1000')]).calories, 2390.0)
        self.assertEqual(Rec.search([], order='recovery_score desc').mapped('recovery_zone'), ['green', 'yellow', 'red'])
        sleep = Sleep.search([])
        self.assertAlmostEqual(sleep.asleep_hours, 7.0)
        self.assertAlmostEqual(sleep.deep_hours, 1.5)
        self.assertAlmostEqual(sleep.sleep_need_hours, 7.5)
        work = Work.search([])
        self.assertEqual(work.sport_name, 'Running')
        self.assertAlmostEqual(work.distance_mi, 6.21, places=2)
        self.assertEqual(work.zone3_min, 30.0)

        # incremental: start = start of the last complete sync - 2 days; rows updated, not duplicated
        http2 = FakeHttp(get)
        with http2.patch():
            msg = self.whoop._sync()
        self.assertIn('0 new / 3 updated cycles', msg, msg)
        start2 = datetime.strptime(http2.gets('/v2/cycle')[0][2]['start'], '%Y-%m-%dT%H:%M:%S.000Z')
        self.assertAlmostEqual((utcnow() - start2).total_seconds(), 2 * 86400, delta=120)

    def test_whoop_pagination_loop_guard(self):
        self.connect(self.whoop)
        http = FakeHttp(lambda url, params, headers: FakeResponse(200, {'records': [], 'next_token': 'same'}))
        with http.patch():
            self.whoop._sync()
        # the repeated token is detected: 2 calls per collection, not an endless loop
        self.assertEqual(len(http.gets('/v2/cycle')), 2)


@tagged('post_install', '-at_install', 'nko_strava')
class TestMisc(NkoStravaCase):

    def test_polyline_roundtrip(self):
        pts = densify(ROUTES['delaware_park'])
        decoded = decode_polyline(encode_polyline(pts))
        self.assertEqual(len(decoded), len(pts))
        for (a, b), (c, d) in zip(pts, decoded):
            self.assertAlmostEqual(a, c, delta=1e-5)
            self.assertAlmostEqual(b, d, delta=1e-5)
        # the canonical example from Google's documentation
        self.assertEqual(encode_polyline([(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]),
                         '_p~iF~ps|U_ulLnnqC_mqNvxq`@')

    def test_sample_data_load_and_remove(self):
        Act = self.env['strava.activity']
        n = Act._nko_load_sample_data()
        self.assertEqual(n, 11)
        samples = Act.search([('is_sample', '=', True)])
        self.assertTrue(all(name.startswith('[sample]') for name in samples.mapped('name')))
        self.assertEqual(len(samples.filtered('has_route')), 10)
        self.assertTrue(self.env['whoop.recovery'].search_count([('is_sample', '=', True)]) >= 28)
        self.assertTrue(Act.get_map_routes([('is_sample', '=', True)]))
        self.assertTrue({a.activity_date.year for a in samples} <= set(Act.get_map_filters()['years']))
        counts = Act._nko_remove_sample_data()
        self.assertEqual(counts['strava.activity'], 11)
        for model in Act._nko_sample_models():
            self.assertEqual(self.env[model].search_count([('is_sample', '=', True)]), 0)

    def test_all_views_load(self):
        views = {
            'strava.activity': ['list', 'form', 'calendar', 'graph', 'pivot', 'search'],
            'strava.athlete.stat': ['list', 'search'],
            'whoop.recovery': ['list', 'form', 'graph', 'pivot', 'calendar', 'search'],
            'whoop.sleep': ['list', 'form', 'graph', 'calendar', 'search'],
            'whoop.cycle': ['list', 'form', 'graph', 'calendar', 'search'],
            'whoop.workout': ['list', 'form', 'graph', 'calendar', 'search'],
            'fitness.connection': ['list', 'form'],
            'res.config.settings': ['form'],
        }
        for model, types in views.items():
            result = self.env[model].get_views([(False, t) for t in types])
            self.assertEqual(set(result['views']), set(types), model)
        arch = self.env['res.config.settings'].get_views([(False, 'form')])['views']['form']['arch']
        self.assertIn('action_nko_strava_connect', arch)
        self.assertIn('nko_whoop_redirect_uri', arch)
