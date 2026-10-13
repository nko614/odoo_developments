import logging
import re
from datetime import timedelta

from odoo import api, fields, models

from .fitness_connection import FitnessRateLimited, utcnow
from .strava_activity import FT_PER_M, M_PER_MILE, parse_iso

_logger = logging.getLogger(__name__)

KJ_TO_KCAL = 0.239006
MS_PER_HOUR = 3600000.0
PAGE_LIMIT = 25
FIRST_SYNC_DAYS = 365
OVERLAP = timedelta(days=2)    # re-fetch the last 2 days so pending scores get updated
MAX_PAGES = 200                # hard stop against a pagination loop
WATERMARK_PARAM = 'nko_strava.whoop_synced_at'  # start of the last complete WHOOP sync (UTC)

SCORE_STATES = [('SCORED', 'Scored'), ('PENDING_SCORE', 'Pending'), ('UNSCORABLE', 'Unscorable')]


def local_date(dt, offset):
    """Local calendar date of a naive-UTC datetime given WHOOP's '-05:00' offset."""
    if not dt:
        return False
    m = re.match(r'^([+-])(\d{2}):?(\d{2})$', offset or '')
    if m:
        delta = timedelta(hours=int(m.group(2)), minutes=int(m.group(3)))
        dt = dt + delta if m.group(1) == '+' else dt - delta
    return dt.date()


def hours(milli):
    return (milli or 0) / MS_PER_HOUR


class WhoopMixin(models.AbstractModel):
    _name = 'whoop.mixin'
    _description = 'WHOOP record'

    whoop_id = fields.Char(string='WHOOP ID', required=True, index=True, readonly=True)
    is_sample = fields.Boolean(string='Sample Data', readonly=True, index=True)
    date = fields.Date(index=True)
    score_state = fields.Selection(SCORE_STATES)
    timezone_offset = fields.Char()

    @api.model
    def _whoop_vals(self, rec):
        raise NotImplementedError

    @api.model
    def _upsert_whoop(self, rec):
        vals = self._whoop_vals(rec)
        wid = str(rec['id'])
        existing = self.search([('whoop_id', '=', wid)], limit=1)
        if existing:
            existing.write(vals)
            return existing, False
        return self.create(dict(vals, whoop_id=wid)), True

    def action_sync_all(self):
        return self.env['fitness.connection'].action_sync_provider('whoop')

    @api.model
    def _whoop_since(self):
        """Start of the window to fetch. WHOOP returns records newest first, so an
        interrupted pass (rate limit, network error) only stored the newest pages:
        the window is therefore based on the start of the last *complete* pass
        (WATERMARK_PARAM, committed together with the data), never on the newest
        stored record, otherwise the older, never-fetched pages would be skipped forever."""
        mark = self.env['ir.config_parameter'].sudo().get_str(WATERMARK_PARAM)
        if mark:
            return fields.Datetime.to_datetime(mark) - OVERLAP
        return utcnow() - timedelta(days=FIRST_SYNC_DAYS)

    @api.model
    def _whoop_sync_collection(self, conn, path, start=None):
        start = start or self._whoop_since()
        params = {'limit': PAGE_LIMIT, 'start': start.strftime('%Y-%m-%dT%H:%M:%S.000Z')}
        created = updated = 0
        seen_tokens = set()
        for _page in range(MAX_PAGES):
            data = conn._api_get(path, params).json() or {}
            with self.env.cr.savepoint():
                for rec in data.get('records') or []:
                    _r, is_new = self._upsert_whoop(rec)
                    created += is_new
                    updated += not is_new
            token = data.get('next_token') or data.get('nextToken')
            if not token or token in seen_tokens:
                break
            seen_tokens.add(token)
            params = dict(params, nextToken=token)
        return created, updated



class WhoopCycle(models.Model):
    _name = 'whoop.cycle'
    _inherit = ['whoop.mixin']
    _description = 'WHOOP Cycle (Strain)'
    _order = 'start desc, id desc'
    _rec_name = 'date'

    start = fields.Datetime(index=True)
    end = fields.Datetime()
    calendar_end = fields.Datetime(compute='_compute_calendar_end', store=True)
    strain = fields.Float(digits=(16, 1), aggregator='avg')
    kilojoule = fields.Float(digits=(16, 0))
    calories = fields.Float(string='Calories (kcal)', digits=(16, 0), aggregator='avg')
    average_heart_rate = fields.Integer(string='Avg HR', aggregator='avg')
    max_heart_rate = fields.Integer(string='Max HR', aggregator='max')

    _whoop_id_unique = models.Constraint('unique(whoop_id)', "This WHOOP cycle already exists.")

    @api.depends('start', 'end')
    def _compute_calendar_end(self):
        for rec in self:
            rec.calendar_end = rec.end or (rec.start and rec.start + timedelta(hours=1))

    @api.model
    def _whoop_vals(self, rec):
        score = rec.get('score') or {}
        start = parse_iso(rec.get('start'))
        kj = score.get('kilojoule') or 0.0
        return {
            'start': start,
            'end': parse_iso(rec.get('end')),
            'timezone_offset': rec.get('timezone_offset'),
            'date': local_date(start, rec.get('timezone_offset')),
            'score_state': rec.get('score_state'),
            'strain': score.get('strain') or 0.0,
            'kilojoule': kj,
            'calories': kj * KJ_TO_KCAL,
            'average_heart_rate': score.get('average_heart_rate') or 0,
            'max_heart_rate': score.get('max_heart_rate') or 0,
        }

    # ------------------------------------------------------------------
    # sync orchestration (all four collections)
    # ------------------------------------------------------------------
    @api.model
    def _sync_from_whoop(self, conn):
        parts = []
        started = utcnow().replace(microsecond=0)
        since = self._whoop_since()
        try:
            for model, path, label in (
                ('whoop.cycle', '/v2/cycle', 'cycles'),
                ('whoop.recovery', '/v2/recovery', 'recoveries'),
                ('whoop.sleep', '/v2/activity/sleep', 'sleeps'),
                ('whoop.workout', '/v2/activity/workout', 'workouts'),
            ):
                created, updated = self.env[model]._whoop_sync_collection(conn, path, since)
                parts.append(f"{created} new / {updated} updated {label}")
        except FitnessRateLimited as e:
            parts.append(str(e))
        else:
            # same transaction as the data: only advances when everything was stored
            self.env['ir.config_parameter'].sudo().set_str(WATERMARK_PARAM, fields.Datetime.to_string(started))
        return ", ".join(parts)


class WhoopRecovery(models.Model):
    _name = 'whoop.recovery'
    _inherit = ['whoop.mixin']
    _description = 'WHOOP Recovery'
    _order = 'date desc, id desc'
    _rec_name = 'date'

    cycle_id = fields.Char(string='Cycle ID', index=True)
    sleep_id = fields.Char(string='Sleep ID')
    recovery_score = fields.Float(string='Recovery %', digits=(16, 0), aggregator='avg')
    recovery_zone = fields.Selection([('green', 'Green'), ('yellow', 'Yellow'), ('red', 'Red')],
                                     compute='_compute_recovery_zone', store=True)
    hrv_rmssd = fields.Float(string='HRV (ms)', digits=(16, 1), aggregator='avg')
    resting_heart_rate = fields.Float(string='Resting HR', digits=(16, 0), aggregator='avg')
    spo2 = fields.Float(string='SpO2 %', digits=(16, 1), aggregator='avg')
    skin_temp_c = fields.Float(string='Skin Temp (°C)', digits=(16, 1), aggregator='avg')
    skin_temp_f = fields.Float(string='Skin Temp (°F)', digits=(16, 1), aggregator='avg',
                               compute='_compute_skin_temp_f', store=True)
    user_calibrating = fields.Boolean(string='Calibrating')
    created_at = fields.Datetime(string='Recorded At')
    calendar_end = fields.Datetime(compute='_compute_calendar_end', store=True)

    _whoop_id_unique = models.Constraint('unique(whoop_id)', "This WHOOP recovery already exists.")

    @api.depends('recovery_score', 'score_state')
    def _compute_recovery_zone(self):
        for rec in self:
            if rec.score_state and rec.score_state != 'SCORED':
                rec.recovery_zone = False
            elif rec.recovery_score >= 67:
                rec.recovery_zone = 'green'
            elif rec.recovery_score >= 34:
                rec.recovery_zone = 'yellow'
            else:
                rec.recovery_zone = 'red'

    @api.depends('skin_temp_c')
    def _compute_skin_temp_f(self):
        for rec in self:
            rec.skin_temp_f = rec.skin_temp_c * 9 / 5 + 32 if rec.skin_temp_c else 0.0

    @api.depends('created_at')
    def _compute_calendar_end(self):
        for rec in self:
            rec.calendar_end = rec.created_at and rec.created_at + timedelta(minutes=30)

    @api.model
    def _upsert_whoop(self, rec):
        # recoveries have no own id: they are keyed by cycle
        rec = dict(rec, id=rec.get('id') or rec.get('cycle_id'))
        return super()._upsert_whoop(rec)

    @api.model
    def _whoop_vals(self, rec):
        score = rec.get('score') or {}
        cycle = self.env['whoop.cycle'].search([('whoop_id', '=', str(rec.get('cycle_id')))], limit=1)
        created = parse_iso(rec.get('created_at'))
        offset = cycle.timezone_offset if cycle else False
        return {
            'cycle_id': str(rec.get('cycle_id') or ''),
            'sleep_id': rec.get('sleep_id') or False,
            'created_at': created,
            'timezone_offset': offset,
            'date': local_date(created, offset) or (cycle.date if cycle else False),
            'score_state': rec.get('score_state'),
            'recovery_score': score.get('recovery_score') or 0.0,
            'hrv_rmssd': score.get('hrv_rmssd_milli') or 0.0,
            'resting_heart_rate': score.get('resting_heart_rate') or 0.0,
            'spo2': score.get('spo2_percentage') or 0.0,
            'skin_temp_c': score.get('skin_temp_celsius') or 0.0,
            'user_calibrating': bool(score.get('user_calibrating')),
        }


class WhoopSleep(models.Model):
    _name = 'whoop.sleep'
    _inherit = ['whoop.mixin']
    _description = 'WHOOP Sleep'
    _order = 'start desc, id desc'
    _rec_name = 'date'

    cycle_id = fields.Char(string='Cycle ID')
    start = fields.Datetime(index=True)
    end = fields.Datetime()
    nap = fields.Boolean()
    performance = fields.Float(string='Performance %', digits=(16, 0), aggregator='avg')
    efficiency = fields.Float(string='Efficiency %', digits=(16, 0), aggregator='avg')
    consistency = fields.Float(string='Consistency %', digits=(16, 0), aggregator='avg')
    respiratory_rate = fields.Float(digits=(16, 1), aggregator='avg')
    in_bed_hours = fields.Float(string='In Bed (h)', digits=(16, 2), aggregator='avg')
    asleep_hours = fields.Float(string='Asleep (h)', digits=(16, 2), aggregator='avg')
    light_hours = fields.Float(string='Light (h)', digits=(16, 2), aggregator='avg')
    deep_hours = fields.Float(string='Deep / SWS (h)', digits=(16, 2), aggregator='avg')
    rem_hours = fields.Float(string='REM (h)', digits=(16, 2), aggregator='avg')
    awake_hours = fields.Float(string='Awake (h)', digits=(16, 2), aggregator='avg')
    disturbances = fields.Integer(aggregator='avg')
    sleep_cycles = fields.Integer(string='Sleep Cycles', aggregator='avg')
    sleep_need_hours = fields.Float(string='Sleep Need (h)', digits=(16, 2), aggregator='avg')

    _whoop_id_unique = models.Constraint('unique(whoop_id)', "This WHOOP sleep already exists.")

    @api.model
    def _whoop_vals(self, rec):
        score = rec.get('score') or {}
        stages = score.get('stage_summary') or {}
        need = score.get('sleep_needed') or {}
        start = parse_iso(rec.get('start'))
        end = parse_iso(rec.get('end'))
        light = hours(stages.get('total_light_sleep_time_milli'))
        deep = hours(stages.get('total_slow_wave_sleep_time_milli'))
        rem = hours(stages.get('total_rem_sleep_time_milli'))
        return {
            'cycle_id': str(rec.get('cycle_id') or ''),
            'start': start,
            'end': end,
            'timezone_offset': rec.get('timezone_offset'),
            'date': local_date(end or start, rec.get('timezone_offset')),
            'nap': bool(rec.get('nap')),
            'score_state': rec.get('score_state'),
            'performance': score.get('sleep_performance_percentage') or 0.0,
            'efficiency': score.get('sleep_efficiency_percentage') or 0.0,
            'consistency': score.get('sleep_consistency_percentage') or 0.0,
            'respiratory_rate': score.get('respiratory_rate') or 0.0,
            'in_bed_hours': hours(stages.get('total_in_bed_time_milli')),
            'asleep_hours': light + deep + rem,
            'light_hours': light,
            'deep_hours': deep,
            'rem_hours': rem,
            'awake_hours': hours(stages.get('total_awake_time_milli')),
            'disturbances': stages.get('disturbance_count') or 0,
            'sleep_cycles': stages.get('sleep_cycle_count') or 0,
            'sleep_need_hours': hours(sum(need.get(k) or 0 for k in (
                'baseline_milli', 'need_from_sleep_debt_milli',
                'need_from_recent_strain_milli', 'need_from_recent_nap_milli'))),
        }


class WhoopWorkout(models.Model):
    _name = 'whoop.workout'
    _inherit = ['whoop.mixin']
    _description = 'WHOOP Workout'
    _order = 'start desc, id desc'
    _rec_name = 'sport_name'

    sport_name = fields.Char(string='Sport')
    sport_id = fields.Integer(string='Sport ID')
    start = fields.Datetime(index=True)
    end = fields.Datetime()
    duration_display = fields.Char(string='Duration', compute='_compute_duration', store=True)
    strain = fields.Float(digits=(16, 1), aggregator='avg')
    average_heart_rate = fields.Integer(string='Avg HR', aggregator='avg')
    max_heart_rate = fields.Integer(string='Max HR', aggregator='max')
    kilojoule = fields.Float(digits=(16, 0))
    calories = fields.Float(string='Calories (kcal)', digits=(16, 0), aggregator='sum')
    percent_recorded = fields.Float(digits=(16, 0))
    distance_meter = fields.Float(string='Distance (m)')
    distance_mi = fields.Float(string='Distance (mi)', digits=(16, 2), aggregator='sum')
    altitude_gain_meter = fields.Float(string='Altitude Gain (m)')
    altitude_gain_ft = fields.Float(string='Altitude Gain (ft)', digits=(16, 0), aggregator='sum')
    zone0_min = fields.Float(string='Zone 0 (min)', digits=(16, 1))
    zone1_min = fields.Float(string='Zone 1 (min)', digits=(16, 1))
    zone2_min = fields.Float(string='Zone 2 (min)', digits=(16, 1))
    zone3_min = fields.Float(string='Zone 3 (min)', digits=(16, 1))
    zone4_min = fields.Float(string='Zone 4 (min)', digits=(16, 1))
    zone5_min = fields.Float(string='Zone 5 (min)', digits=(16, 1))

    _whoop_id_unique = models.Constraint('unique(whoop_id)', "This WHOOP workout already exists.")

    @api.depends('start', 'end')
    def _compute_duration(self):
        for rec in self:
            if rec.start and rec.end:
                secs = int((rec.end - rec.start).total_seconds())
                rec.duration_display = f"{secs // 3600}:{secs % 3600 // 60:02d}:{secs % 60:02d}"
            else:
                rec.duration_display = False

    @api.model
    def _whoop_vals(self, rec):
        score = rec.get('score') or {}
        zones = score.get('zone_durations') or {}
        start = parse_iso(rec.get('start'))
        kj = score.get('kilojoule') or 0.0
        dist = score.get('distance_meter') or 0.0
        alt = score.get('altitude_gain_meter') or 0.0
        names = ('zero', 'one', 'two', 'three', 'four', 'five')
        vals = {
            'sport_name': (rec.get('sport_name') or 'workout').replace('-', ' ').title(),
            'sport_id': rec.get('sport_id') or 0,
            'start': start,
            'end': parse_iso(rec.get('end')),
            'timezone_offset': rec.get('timezone_offset'),
            'date': local_date(start, rec.get('timezone_offset')),
            'score_state': rec.get('score_state'),
            'strain': score.get('strain') or 0.0,
            'average_heart_rate': score.get('average_heart_rate') or 0,
            'max_heart_rate': score.get('max_heart_rate') or 0,
            'kilojoule': kj,
            'calories': kj * KJ_TO_KCAL,
            'percent_recorded': score.get('percent_recorded') or 0.0,
            'distance_meter': dist,
            'distance_mi': dist / M_PER_MILE,
            'altitude_gain_meter': alt,
            'altitude_gain_ft': alt * FT_PER_M,
        }
        for i, n in enumerate(names):
            vals[f'zone{i}_min'] = (zones.get(f'zone_{n}_milli') or 0) / 60000.0
        return vals
