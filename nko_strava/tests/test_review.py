"""Review tests: OAuth callback CSRF/state handling over real HTTP, access rights for
non-admin users, unit/timezone edge cases and interrupted WHOOP syncs.
All provider HTTP is mocked; nothing leaves the machine."""
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlparse

from odoo.exceptions import AccessError
from odoo.tests import HttpCase, new_test_user, tagged

from .test_nko_strava import (
    STRAVA_API, WHOOP_API, FakeHttp, FakeResponse, NkoStravaCase, strava_summary, utcnow,
)


@tagged('post_install', '-at_install', 'nko_strava')
class TestOAuthCallbackHttp(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        icp = cls.env['ir.config_parameter'].sudo()
        icp.set_str('web.base.url', cls.base_url())
        icp.set_str('nko_strava.strava_client_id', 'strava-id')
        icp.set_str('nko_strava.strava_client_secret', 'strava-secret')
        Conn = cls.env['fitness.connection'].sudo()
        Conn.search([]).unlink()
        cls.conn = Conn.create({'provider': 'strava'})
        cls.user = new_test_user(cls.env, login='nko_plain', password='nko_plain_pw', groups='base.group_user')

    def start_connect(self):
        action = self.env['fitness.connection']._action_connect('strava')
        return parse_qs(urlparse(action['url']).query)['state'][0]

    def callback(self, **params):
        return self.url_open('/nko_strava/strava/callback', params=params, allow_redirects=False)

    def tokens(self):
        self.env.invalidate_all()
        c = self.conn.sudo()
        return c.state, c.access_token, c.refresh_token, c.oauth_state, c.last_error

    def http(self):
        def post(url, data):
            self.assertEqual(data['code'], 'good-code')
            return FakeResponse(200, {
                'access_token': 'AT-cb', 'refresh_token': 'RT-cb', 'expires_at': 4102444800,
                'athlete': {'id': 7, 'firstname': 'Test', 'lastname': 'User'}})

        def get(url, params, headers):
            path = url[len(STRAVA_API):]
            if path == '/athlete':
                return FakeResponse(200, {'id': 7})
            if path == '/athlete/activities':
                return FakeResponse(200, [])
            if path == '/athletes/7/stats':
                return FakeResponse(200, {})
            raise AssertionError(url)
        return FakeHttp(get, post)

    def test_state_missing_wrong_reused(self):
        self.authenticate('admin', 'admin')
        state = self.start_connect()
        http = self.http()
        with http.patch():
            # missing state
            r = self.callback(code='good-code')
            self.assertEqual(r.status_code, 303)
            # wrong state
            self.callback(code='good-code', state='attacker-state')
            # prefix of the right state is still wrong
            self.callback(code='good-code', state=state[:-1])
            self.assertEqual(http.posts(), [], "token endpoint must not be called without a valid state")
            st, access, *_rest, err = self.tokens()
            self.assertEqual(st, 'disconnected')
            self.assertFalse(access)
            self.assertIn('invalid or expired state', err)

            # valid state -> connected
            r = self.callback(code='good-code', state=state)
            self.assertEqual(r.status_code, 303)
            self.assertEqual(len(http.posts()), 1)
            st, access, refresh, oauth_state, _err = self.tokens()
            self.assertEqual((st, access, refresh), ('connected', 'AT-cb', 'RT-cb'))
            self.assertFalse(oauth_state, "state must be single use")

            # replaying the same callback (or another code with the old state) is rejected
            self.callback(code='other-code', state=state)
            self.assertEqual(len(http.posts()), 1)
            st, access, *_rest = self.tokens()
            self.assertEqual((st, access), ('connected', 'AT-cb'))

    def test_error_param_clears_state(self):
        self.authenticate('admin', 'admin')
        state = self.start_connect()
        http = self.http()
        with http.patch():
            self.callback(error='access_denied', state=state)
        self.assertEqual(http.calls, [])
        st, _a, _r, oauth_state, err = self.tokens()
        self.assertEqual(st, 'disconnected')
        self.assertFalse(oauth_state)
        self.assertIn('access_denied', err)

    def test_non_admin_cannot_use_callback(self):
        state = self.start_connect()
        self.authenticate('nko_plain', 'nko_plain_pw')
        http = self.http()
        with http.patch():
            r = self.callback(code='good-code', state=state)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(http.calls, [])
        self.assertEqual(self.tokens()[0], 'disconnected')

    def test_anonymous_callback_redirects_to_login(self):
        http = self.http()
        with http.patch():
            r = self.callback(code='good-code', state=self.start_connect())
        # login redirect, or 404 when no database is bound to the anonymous session
        self.assertIn(r.status_code, (302, 303, 404))
        if r.status_code != 404:
            self.assertIn('/web/login', r.headers.get('Location', ''))
        self.assertEqual(http.calls, [])

    def test_bad_token_response_is_clean(self):
        """A 200 token response without access_token used to escape as a 500 traceback."""
        self.authenticate('admin', 'admin')
        state = self.start_connect()
        http = FakeHttp(post_handler=lambda url, data: FakeResponse(200, {'message': 'weird'}))
        with http.patch():
            r = self.callback(code='good-code', state=state)
        self.assertEqual(r.status_code, 303)
        st, access, _r, _s, err = self.tokens()
        self.assertEqual(st, 'disconnected')
        self.assertFalse(access)
        self.assertIn('access_token', err)


@tagged('post_install', '-at_install', 'nko_strava')
class TestReviewAccess(NkoStravaCase):

    def test_plain_user_cannot_read_secrets(self):
        self.connect(self.strava)
        user = new_test_user(self.env, login='nko_plain2', groups='base.group_user')
        env = self.env(user=user)
        with self.assertRaises(AccessError):
            env['fitness.connection'].search([])
        with self.assertRaises(AccessError):
            env['fitness.connection'].browse(self.strava.id).read(['access_token'])
        with self.assertRaises(AccessError):
            env['ir.config_parameter'].search([('key', 'like', 'nko_strava.%')]).read(['value'])
        with self.assertRaises(AccessError):
            env['res.config.settings'].create({})
        # the data itself is readable (personal app, shared with internal users by design)
        env['strava.activity'].search([])
        env['whoop.recovery'].search([])

    def test_admin_settings_does_not_leak_tokens(self):
        fields_ = self.env['res.config.settings']._fields
        self.assertFalse([f for f in fields_ if f.startswith('nko_') and 'token' in f])


@tagged('post_install', '-at_install', 'nko_strava')
class TestReviewConversions(NkoStravaCase):

    def test_strava_units_timezone_and_zero_division(self):
        Act = self.env['strava.activity']
        start = datetime(2024, 5, 2, 2, 30)
        run = strava_summary(1, start)
        run.update({'distance': 1609.344, 'moving_time': 480, 'average_speed': 1609.344 / 480,
                    'total_elevation_gain': 100.0, 'start_date_local': '2024-05-01T22:30:00Z'})
        ride = strava_summary(2, start)
        ride.update({'sport_type': 'Ride', 'average_speed': 8.9408, 'max_speed': 13.4112, 'kilojoules': 1000.0})
        indoor = strava_summary(3, start, with_map=False)
        indoor.update({'sport_type': 'Workout', 'distance': 0.0, 'moving_time': 0, 'elapsed_time': 0,
                       'average_speed': 0.0, 'average_heartrate': None, 'max_heartrate': None,
                       'trainer': True, 'start_latlng': None})
        treadmill = strava_summary(4, start, with_map=False)
        treadmill.update({'distance': 0.0, 'average_speed': 0.0, 'moving_time': 1800})
        swim = strava_summary(5, start, with_map=False)
        swim.update({'sport_type': 'Swim', 'distance': 914.4, 'moving_time': 900, 'average_speed': 914.4 / 900})
        acts = {d['id']: Act._upsert_from_strava(d)[0] for d in (run, ride, indoor, treadmill, swim)}
        r, b, i, t, s = (acts[d['id']] for d in (run, ride, indoor, treadmill, swim))

        self.assertAlmostEqual(r.distance_mi, 1.0)
        self.assertEqual(r.pace_display, '8:00 /mi')
        self.assertAlmostEqual(r.elevation_ft, 328.0)  # stored with 0 digits
        self.assertEqual(r.start_date, start)  # UTC kept as UTC
        self.assertEqual(r.activity_date, date(2024, 5, 1))  # local day, not the UTC day
        self.assertEqual(r.start_date_local, '2024-05-01 22:30')

        self.assertEqual(b.pace_display, '20.0 mph')
        self.assertAlmostEqual(b.max_mph, 30.0, places=1)

        self.assertFalse(i.pace_display)
        self.assertFalse(i.moving_time_display)
        self.assertEqual((i.average_heartrate, i.distance_mi), (0.0, 0.0))
        self.assertEqual(i.sport_group, 'other')
        self.assertFalse(i.has_route)
        self.assertFalse(t.pace_display)
        self.assertEqual(s.pace_display, '1:30 /100yd')

    def test_whoop_unscored_and_offsets(self):
        Cycle, Rec, Sleep, Work = (self.env[m] for m in ('whoop.cycle', 'whoop.recovery', 'whoop.sleep', 'whoop.workout'))
        c, _new = Cycle._upsert_whoop({'id': 77, 'start': '2024-05-02T02:00:00.000Z', 'end': None,
                                      'timezone_offset': '-04:00', 'score_state': 'PENDING_SCORE'})
        self.assertEqual(c.date, date(2024, 5, 1))
        self.assertEqual((c.strain, c.calories), (0.0, 0.0))
        self.assertTrue(c.calendar_end)
        c2, _new = Cycle._upsert_whoop({'id': 78, 'start': '2024-05-01T22:30:00.000Z', 'timezone_offset': '+0530',
                                       'score_state': 'SCORED', 'score': {'kilojoule': 1000.0, 'strain': 5}})
        self.assertEqual(c2.date, date(2024, 5, 2))
        self.assertAlmostEqual(c2.calories, 239.0)  # stored with 0 digits

        rec, _new = Rec._upsert_whoop({'cycle_id': 77, 'sleep_id': None, 'score_state': 'UNSCORABLE',
                                      'created_at': '2024-05-02T11:00:00.000Z', 'score': None})
        self.assertEqual(rec.whoop_id, '77')
        self.assertFalse(rec.recovery_zone)
        self.assertEqual(rec.date, date(2024, 5, 2))
        rec2, new = Rec._upsert_whoop({'cycle_id': 77, 'score_state': 'SCORED', 'created_at': '2024-05-02T11:00:00.000Z',
                                      'score': {'recovery_score': 70}})
        self.assertFalse(new)
        self.assertEqual(rec2.recovery_zone, 'green')

        sl, _new = Sleep._upsert_whoop({'id': 'uuid-1', 'cycle_id': 77, 'start': '2024-05-02T03:00:00.000Z',
                                       'end': None, 'timezone_offset': '-04:00', 'score_state': 'PENDING_SCORE',
                                       'score': None})
        self.assertEqual((sl.asleep_hours, sl.performance), (0.0, 0.0))
        self.assertEqual(sl.date, date(2024, 5, 1))
        w, _new = Work._upsert_whoop({'id': 'uuid-w', 'sport_name': None, 'start': '2024-05-02T03:00:00.000Z',
                                     'end': '2024-05-02T03:45:30.000Z', 'timezone_offset': '-04:00',
                                     'score_state': 'UNSCORABLE', 'score': None})
        self.assertEqual(w.sport_name, 'Workout')
        self.assertEqual(w.duration_display, '0:45:30')
        self.assertEqual(w.distance_mi, 0.0)


@tagged('post_install', '-at_install', 'nko_strava')
class TestReviewWhoopResume(NkoStravaCase):

    def test_interrupted_first_sync_does_not_skip_older_pages(self):
        """WHOOP lists newest first. If the first sync dies after page 1, the next run
        must go back to the full window, not start from the newest stored record."""
        self.connect(self.whoop)
        now = utcnow()

        def iso(dt):
            return dt.strftime('%Y-%m-%dT%H:%M:%S.000Z')

        def cyc(days_ago):
            s = now - timedelta(days=days_ago)
            return {'id': 5000 - days_ago, 'start': iso(s), 'timezone_offset': '-04:00', 'score_state': 'SCORED',
                    'score': {'strain': 8.0, 'kilojoule': 9000.0}}

        fail = {'on': True}

        def get(url, params, headers):
            path = url[len(WHOOP_API):]
            if path == '/v2/cycle':
                if params.get('nextToken') == 'p2':
                    if fail['on']:
                        return FakeResponse(503, {'message': 'unavailable'})
                    return FakeResponse(200, {'records': [cyc(200), cyc(201)]})
                return FakeResponse(200, {'records': [cyc(1), cyc(2)], 'next_token': 'p2'})
            return FakeResponse(200, {'records': []})

        http = FakeHttp(get)
        with http.patch():
            msg = self.whoop._sync()
        self.assertIn('error', msg)
        self.assertEqual(self.env['whoop.cycle'].search_count([]), 2)

        fail['on'] = False
        http2 = FakeHttp(get)
        with http2.patch():
            msg = self.whoop._sync()
        start2 = datetime.strptime(http2.gets('/v2/cycle')[0][2]['start'], '%Y-%m-%dT%H:%M:%S.000Z')
        self.assertGreater((now - start2).days, 300, "second run must re-cover the full first-sync window")
        self.assertEqual(self.env['whoop.cycle'].search_count([]), 4)

        http3 = FakeHttp(get)
        with http3.patch():
            self.whoop._sync()
        start3 = datetime.strptime(http3.gets('/v2/cycle')[0][2]['start'], '%Y-%m-%dT%H:%M:%S.000Z')
        self.assertLess((now - start3).days, 3, "after a complete pass, syncs are incremental")


@tagged('post_install', '-at_install', 'nko_strava')
class TestReviewMap(NkoStravaCase):

    def test_map_routes_capped(self):
        Act = self.env['strava.activity']
        start = datetime(2024, 1, 1, 12, 0)
        for i in range(5):
            Act._upsert_from_strava(strava_summary(100 + i, start + timedelta(days=i)))
        self.assertEqual(len(Act.get_map_routes([], limit=3)), 3)
        routes = Act.get_map_routes([('sport_group', '=', 'run')])
        self.assertEqual(len(routes), 5)
        self.assertEqual(routes[0]['date'], '2024-01-05')  # newest first, so a cap drops the oldest
