import logging
from datetime import datetime, timedelta, timezone

from odoo import api, fields, models

from .fitness_connection import FitnessRateLimited, utcnow

_logger = logging.getLogger(__name__)

M_PER_MILE = 1609.344
FT_PER_M = 3.28084
MPS_TO_MPH = 2.2369363
PER_PAGE = 200
DETAILS_PER_RUN = 25           # detailed activity calls per sync (calories, description, device)
RATE_MARGIN_15MIN = 5          # stop when that close to the 15-minute limit
RATE_MARGIN_DAY = 20           # stop when that close to the daily limit
RECENT_RESYNC = timedelta(days=3)

SPORT_GROUPS = {
    'run': {'Run', 'TrailRun', 'VirtualRun'},
    'ride': {'Ride', 'MountainBikeRide', 'GravelRide', 'EBikeRide', 'EMountainBikeRide',
             'VirtualRide', 'Velomobile', 'Handcycle'},
    'walk': {'Walk', 'Hike'},
    'swim': {'Swim'},
}
PACE_GROUPS = {'run', 'walk'}


def fmt_duration(seconds):
    if not seconds:
        return False
    seconds = int(round(seconds))
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def parse_iso(value):
    """'2024-05-01T11:12:13Z' -> naive UTC datetime."""
    if not value:
        return False
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.replace(microsecond=0)


class StravaActivity(models.Model):
    _name = 'strava.activity'
    _description = 'Strava Activity'
    _order = 'start_date desc, id desc'

    name = fields.Char(required=True)
    strava_id = fields.Char(string='Strava ID', required=True, index=True, readonly=True)
    is_sample = fields.Boolean(string='Sample Data', readonly=True, index=True)
    sport_type = fields.Char(string='Sport', index=True)
    sport_group = fields.Selection([
        ('run', 'Run'), ('ride', 'Ride'), ('walk', 'Walk / Hike'), ('swim', 'Swim'), ('other', 'Other'),
    ], compute='_compute_sport_group', store=True, index=True)
    start_date = fields.Datetime(string='Start', index=True, help="Start time (stored in UTC)")
    start_date_local = fields.Char(string='Local Start', help="Start time in the activity's own timezone")
    activity_date = fields.Date(string='Date', index=True, help="Local calendar date of the activity")
    end_date = fields.Datetime(compute='_compute_end_date', store=True)
    timezone = fields.Char()

    # raw metric values as returned by Strava
    distance = fields.Float(string='Distance (m)')
    moving_time = fields.Integer(string='Moving Time (s)')
    elapsed_time = fields.Integer(string='Elapsed Time (s)')
    total_elevation_gain = fields.Float(string='Elevation Gain (m)')
    average_speed = fields.Float(string='Avg Speed (m/s)')
    max_speed = fields.Float(string='Max Speed (m/s)')

    # imperial / display values
    distance_mi = fields.Float(string='Distance (mi)', compute='_compute_imperial', store=True, digits=(16, 2), aggregator='sum')
    elevation_ft = fields.Float(string='Elevation (ft)', compute='_compute_imperial', store=True, digits=(16, 0), aggregator='sum')
    moving_hours = fields.Float(string='Moving Time (h)', compute='_compute_imperial', store=True, digits=(16, 2), aggregator='sum')
    avg_mph = fields.Float(string='Avg Speed (mph)', compute='_compute_imperial', store=True, digits=(16, 1), aggregator='avg')
    max_mph = fields.Float(string='Max Speed (mph)', compute='_compute_imperial', store=True, digits=(16, 1), aggregator='max')
    moving_time_display = fields.Char(string='Moving Time', compute='_compute_imperial', store=True)
    elapsed_time_display = fields.Char(string='Elapsed Time', compute='_compute_imperial', store=True)
    pace_display = fields.Char(string='Pace / Speed', compute='_compute_imperial', store=True)

    average_heartrate = fields.Float(string='Avg HR', digits=(16, 0), aggregator='avg')
    max_heartrate = fields.Float(string='Max HR', digits=(16, 0), aggregator='max')
    average_watts = fields.Float(string='Avg Watts', digits=(16, 0), aggregator='avg')
    kilojoules = fields.Float(digits=(16, 0))
    calories = fields.Float(digits=(16, 0))
    suffer_score = fields.Float(string='Relative Effort', digits=(16, 0))
    kudos_count = fields.Integer(string='Kudos')
    achievement_count = fields.Integer(string='Achievements')
    pr_count = fields.Integer(string='PRs')
    trainer = fields.Boolean(string='Indoor / Trainer')
    commute = fields.Boolean()
    private = fields.Boolean()
    gear_id = fields.Char(string='Gear ID')
    gear_name = fields.Char(string='Gear')
    device_name = fields.Char(string='Device')
    summary_polyline = fields.Text(string='Route (encoded polyline)')
    has_route = fields.Boolean(compute='_compute_has_route', store=True)
    start_lat = fields.Float(string='Start Latitude', digits=(10, 6))
    start_lng = fields.Float(string='Start Longitude', digits=(10, 6))
    description = fields.Text()
    details_fetched = fields.Boolean(readonly=True)
    strava_url = fields.Char(string='Strava Link', compute='_compute_strava_url')

    _strava_id_unique = models.Constraint('unique(strava_id)', "This Strava activity already exists.")

    @api.depends('sport_type')
    def _compute_sport_group(self):
        for act in self:
            act.sport_group = next((g for g, types in SPORT_GROUPS.items() if act.sport_type in types), 'other')

    @api.depends('start_date', 'elapsed_time')
    def _compute_end_date(self):
        for act in self:
            act.end_date = act.start_date and act.start_date + timedelta(seconds=max(act.elapsed_time or 0, 60))

    @api.depends('summary_polyline')
    def _compute_has_route(self):
        for act in self:
            act.has_route = bool(act.summary_polyline)

    @api.depends('distance', 'total_elevation_gain', 'moving_time', 'elapsed_time',
                 'average_speed', 'max_speed', 'sport_group')
    def _compute_imperial(self):
        for act in self:
            act.distance_mi = (act.distance or 0) / M_PER_MILE
            act.elevation_ft = (act.total_elevation_gain or 0) * FT_PER_M
            act.moving_hours = (act.moving_time or 0) / 3600.0
            act.avg_mph = (act.average_speed or 0) * MPS_TO_MPH
            act.max_mph = (act.max_speed or 0) * MPS_TO_MPH
            act.moving_time_display = fmt_duration(act.moving_time)
            act.elapsed_time_display = fmt_duration(act.elapsed_time)
            act.pace_display = act._pace_text()

    def _pace_text(self):
        speed = self.average_speed
        if not speed and self.distance and self.moving_time:
            speed = self.distance / self.moving_time
        if not speed:
            return False
        if self.sport_group in PACE_GROUPS:
            sec_per_mile = M_PER_MILE / speed
            minutes, seconds = divmod(int(round(sec_per_mile)), 60)
            return f"{minutes}:{seconds:02d} /mi"
        if self.sport_group == 'swim':
            sec_per_100yd = 91.44 / speed
            minutes, seconds = divmod(int(round(sec_per_100yd)), 60)
            return f"{minutes}:{seconds:02d} /100yd"
        return f"{speed * MPS_TO_MPH:.1f} mph"

    def _compute_strava_url(self):
        for act in self:
            act.strava_url = (f"https://www.strava.com/activities/{act.strava_id}"
                              if act.strava_id and not act.is_sample else False)

    def action_open_strava(self):
        self.ensure_one()
        if not self.strava_url:
            return False
        return {'type': 'ir.actions.act_url', 'url': self.strava_url, 'target': 'new'}

    def action_sync_all(self):
        return self.env['fitness.connection'].action_sync_provider('strava')

    @api.model
    def get_map_routes(self, domain=None, limit=2000):
        """Data for the all-activities map client action."""
        domain = list(domain or []) + [('has_route', '=', True)]
        acts = self.search(domain, limit=limit)
        return [{
            'id': a.id,
            'name': a.name,
            'sport_type': a.sport_type,
            'sport_group': a.sport_group,
            'date': fields.Date.to_string(a.activity_date) if a.activity_date else '',
            'distance_mi': round(a.distance_mi, 2),
            'pace': a.pace_display or '',
            'polyline': a.summary_polyline,
        } for a in acts]

    @api.model
    def get_map_filters(self):
        groups = self.env['strava.activity']._read_group(
            [('has_route', '=', True), ('activity_date', '!=', False)], ['activity_date:year'])
        years = sorted({g[0].year for g in groups if g[0]}, reverse=True)
        return {'years': years, 'sports': [list(s) for s in self._fields['sport_group'].selection]}

    # ------------------------------------------------------------------
    # sync
    # ------------------------------------------------------------------
    @api.model
    def _vals_from_strava(self, data):
        start_local = data.get('start_date_local') or ''
        latlng = data.get('start_latlng') or []
        vals = {
            'name': data.get('name') or 'Activity',
            'sport_type': data.get('sport_type') or data.get('type'),
            'start_date': parse_iso(data.get('start_date')),
            'start_date_local': start_local.replace('T', ' ').rstrip('Z')[:16] or False,
            'activity_date': start_local[:10] or False,
            'timezone': data.get('timezone'),
            'distance': data.get('distance') or 0.0,
            'moving_time': data.get('moving_time') or 0,
            'elapsed_time': data.get('elapsed_time') or 0,
            'total_elevation_gain': data.get('total_elevation_gain') or 0.0,
            'average_speed': data.get('average_speed') or 0.0,
            'max_speed': data.get('max_speed') or 0.0,
            'average_heartrate': data.get('average_heartrate') or 0.0,
            'max_heartrate': data.get('max_heartrate') or 0.0,
            'average_watts': data.get('average_watts') or 0.0,
            'kilojoules': data.get('kilojoules') or 0.0,
            'suffer_score': data.get('suffer_score') or 0.0,
            'kudos_count': data.get('kudos_count') or 0,
            'achievement_count': data.get('achievement_count') or 0,
            'pr_count': data.get('pr_count') or 0,
            'trainer': bool(data.get('trainer')),
            'commute': bool(data.get('commute')),
            'private': bool(data.get('private')),
            'gear_id': data.get('gear_id') or False,
            'summary_polyline': (data.get('map') or {}).get('summary_polyline') or False,
            'start_lat': latlng[0] if len(latlng) == 2 else 0.0,
            'start_lng': latlng[1] if len(latlng) == 2 else 0.0,
        }
        # fields only present on the detailed representation
        if 'calories' in data:
            vals['calories'] = data.get('calories') or 0.0
        if 'description' in data:
            vals['description'] = data.get('description') or False
        if 'device_name' in data:
            vals['device_name'] = data.get('device_name') or False
        if (data.get('gear') or {}).get('name'):
            vals['gear_name'] = data['gear']['name']
        return vals

    @api.model
    def _upsert_from_strava(self, data, gear_names=None):
        vals = self._vals_from_strava(data)
        if gear_names and vals.get('gear_id') in gear_names and 'gear_name' not in vals:
            vals['gear_name'] = gear_names[vals['gear_id']]
        sid = str(data['id'])
        act = self.with_context(active_test=False).search([('strava_id', '=', sid)], limit=1)
        if act:
            act.write(vals)
            return act, False
        return self.create(dict(vals, strava_id=sid)), True

    @api.model
    def _check_rate_limit(self, resp):
        """Raise FitnessRateLimited when close to Strava's (read) limits."""
        for prefix in ('X-ReadRateLimit', 'X-RateLimit'):
            limit = resp.headers.get(f'{prefix}-Limit')
            usage = resp.headers.get(f'{prefix}-Usage')
            if not limit or not usage:
                continue
            try:
                l15, lday = (int(x) for x in limit.split(',')[:2])
                u15, uday = (int(x) for x in usage.split(',')[:2])
            except ValueError:
                continue
            if u15 >= l15 - RATE_MARGIN_15MIN or uday >= lday - RATE_MARGIN_DAY:
                raise FitnessRateLimited(f"Strava rate limit nearly reached ({usage} of {limit}); "
                                         f"the rest will sync on the next run.")

    @api.model
    def _sync_from_strava(self, conn):
        """Incremental sync. Returns a summary string."""
        created = updated = details = 0
        stopped = ''
        try:
            gear_names = {}
            resp = conn._api_get('/athlete')
            self._check_rate_limit(resp)
            athlete = resp.json()
            for gear in (athlete.get('bikes') or []) + (athlete.get('shoes') or []):
                gear_names[gear.get('id')] = gear.get('name') or gear.get('nickname')

            latest = self.search([('is_sample', '=', False), ('start_date', '!=', False)],
                                 order='start_date desc', limit=1).start_date
            if latest:
                after_dt = min(latest - timedelta(seconds=1), utcnow() - RECENT_RESYNC)
                after = int(after_dt.replace(tzinfo=timezone.utc).timestamp())
            else:
                after = 0  # full history; with `after` Strava returns oldest first
            page = 1
            while True:
                resp = conn._api_get('/athlete/activities',
                                     {'per_page': PER_PAGE, 'page': page, 'after': after})
                batch = resp.json() or []
                with self.env.cr.savepoint():
                    for data in batch:
                        _act, is_new = self._upsert_from_strava(data, gear_names)
                        created += is_new
                        updated += not is_new
                self._check_rate_limit(resp)
                if len(batch) < PER_PAGE:
                    break
                page += 1

            # details (calories, description, device, gear name) for a few activities per run
            todo = self.search([('details_fetched', '=', False), ('is_sample', '=', False)],
                               order='start_date desc', limit=DETAILS_PER_RUN)
            for act in todo:
                resp = conn._api_get(f'/activities/{act.strava_id}')
                with self.env.cr.savepoint():
                    act.write(dict(self._vals_from_strava(resp.json()), details_fetched=True))
                details += 1
                self._check_rate_limit(resp)

            if athlete.get('id'):
                resp = conn._api_get(f"/athletes/{athlete['id']}/stats")
                self.env['strava.athlete.stat']._store_stats(resp.json())
                self._check_rate_limit(resp)
        except FitnessRateLimited as e:
            stopped = f" ({e})"
        return f"{created} new, {updated} updated, {details} detailed{stopped}"
