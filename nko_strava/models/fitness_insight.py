from odoo import fields, models, tools

ZONES = [('green', 'Green'), ('yellow', 'Yellow'), ('red', 'Red')]
SPORTS = [('run', 'Run'), ('ride', 'Ride'), ('walk', 'Walk / Hike'), ('swim', 'Swim'), ('other', 'Other')]
TIMES_OF_DAY = [('morning', 'Morning'), ('afternoon', 'Afternoon'), ('evening', 'Evening')]
SLEEP_BUCKETS = [('under6', 'Under 6h'), ('6to7', '6-7h'), ('7to8', '7-8h'), ('over8', '8h+')]

# One row per WHOOP day: that morning's recovery, the night's main sleep and the day's strain.
# Zeros mean "not measured", so they become NULL and don't drag averages down.
WHOOP_DAY_SQL = """
    rec AS (
        SELECT DISTINCT ON (r.date)
               r.date, r.recovery_score, r.recovery_zone,
               NULLIF(r.hrv_rmssd, 0) AS hrv, NULLIF(r.resting_heart_rate, 0) AS rhr,
               NULLIF(c.strain, 0) AS day_strain
          FROM whoop_recovery r
          LEFT JOIN whoop_cycle c ON c.whoop_id = r.cycle_id
         WHERE r.score_state = 'SCORED' AND r.date IS NOT NULL
         ORDER BY r.date, r.created_at DESC
    ),
    slp AS (
        SELECT DISTINCT ON (s.date)
               s.date, NULLIF(s.asleep_hours, 0) AS sleep_hours, NULLIF(s.performance, 0) AS sleep_performance,
               NULLIF(s.deep_hours, 0) AS deep_hours, NULLIF(s.rem_hours, 0) AS rem_hours,
               s.disturbances, NULLIF(s.respiratory_rate, 0) AS respiratory_rate
          FROM whoop_sleep s
         WHERE NOT s.nap AND s.score_state = 'SCORED' AND s.date IS NOT NULL
         ORDER BY s.date, s.asleep_hours DESC
    ),
    day AS (
        SELECT rec.*, slp.sleep_hours, slp.sleep_performance,
               CASE WHEN slp.sleep_hours < 6 THEN 'under6' WHEN slp.sleep_hours < 7 THEN '6to7'
                    WHEN slp.sleep_hours < 8 THEN '7to8' WHEN slp.sleep_hours >= 8 THEN 'over8' END AS sleep_bucket,
               aft.sleep_hours AS night_sleep_hours, aft.sleep_performance AS night_sleep_performance,
               aft.deep_hours AS night_deep_hours, aft.rem_hours AS night_rem_hours,
               aft.disturbances AS night_disturbances, aft.respiratory_rate AS night_respiratory_rate,
               nxt.recovery_score AS next_recovery,
               nxt.recovery_score - rec.recovery_score AS recovery_change,
               nxt.hrv - rec.hrv AS hrv_change,
               nxt.rhr - rec.rhr AS rhr_change
          FROM rec
          LEFT JOIN slp ON slp.date = rec.date
          LEFT JOIN slp aft ON aft.date = rec.date + 1
          LEFT JOIN rec nxt ON nxt.date = rec.date + 1
    )
"""


class FitnessActivityInsight(models.Model):
    """Each Strava activity next to that morning's WHOOP recovery, the night before's
    sleep, the matching WHOOP workout (for heart rate and strain) and the next morning."""
    _name = 'fitness.activity.insight'
    _description = 'Activity vs. Recovery'
    _auto = False
    _order = 'activity_date desc, id desc'
    _rec_name = 'name'

    activity_id = fields.Many2one('strava.activity', string='Activity', readonly=True)
    name = fields.Char(readonly=True)
    activity_date = fields.Date(string='Date', readonly=True)
    sport_group = fields.Selection(SPORTS, string='Sport', readonly=True)
    time_of_day = fields.Selection(TIMES_OF_DAY, readonly=True)
    distance_mi = fields.Float(string='Distance (mi)', digits=(16, 2), aggregator='avg', readonly=True)
    moving_hours = fields.Float(string='Moving Time (h)', digits=(16, 2), aggregator='avg', readonly=True)
    pace_min_mi = fields.Float(string='Pace (min/mi)', aggregator='avg', readonly=True,
                               help="Runs and walks only")
    avg_mph = fields.Float(string='Speed (mph)', digits=(16, 1), aggregator='avg', readonly=True,
                           help="Rides only")
    heart_rate = fields.Float(string='Avg HR', digits=(16, 0), aggregator='avg', readonly=True,
                              help="From Strava if recorded there, otherwise from the matching WHOOP workout")
    meters_per_beat = fields.Float(string='Meters per Beat', digits=(16, 2), aggregator='avg', readonly=True,
                                   help="Distance covered per heartbeat. Higher means more efficient: "
                                        "compare it within one sport.")
    workout_strain = fields.Float(string='Workout Strain', digits=(16, 1), aggregator='avg', readonly=True)
    effort = fields.Selection([('easy', 'Easy'), ('moderate', 'Moderate'), ('hard', 'Hard')], readonly=True,
                              help="From WHOOP workout strain: easy < 8, moderate 8-12, hard 12+")
    has_whoop_workout = fields.Boolean(string='WHOOP Workout Matched', readonly=True)

    recovery_score = fields.Float(string='Recovery %', digits=(16, 0), aggregator='avg', readonly=True)
    recovery_zone = fields.Selection(ZONES, readonly=True)
    hrv = fields.Float(string='HRV (ms)', digits=(16, 1), aggregator='avg', readonly=True)
    rhr = fields.Float(string='Resting HR', digits=(16, 0), aggregator='avg', readonly=True)
    sleep_hours = fields.Float(string='Sleep Before (h)', digits=(16, 2), aggregator='avg', readonly=True)
    sleep_performance = fields.Float(string='Sleep Performance %', digits=(16, 0), aggregator='avg', readonly=True)
    sleep_bucket = fields.Selection(SLEEP_BUCKETS, string='Sleep Before', readonly=True)
    next_recovery = fields.Float(string='Next-Day Recovery %', digits=(16, 0), aggregator='avg', readonly=True)
    recovery_change = fields.Float(string='Recovery Change', digits=(16, 0), aggregator='avg', readonly=True)
    hrv_change = fields.Float(string='HRV Change (ms)', digits=(16, 1), aggregator='avg', readonly=True)
    rhr_change = fields.Float(string='Resting HR Change', digits=(16, 1), aggregator='avg', readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(f"""
            CREATE VIEW {self._table} AS
            WITH {WHOOP_DAY_SQL}
            SELECT a.id, a.id AS activity_id, a.name, a.activity_date, a.sport_group,
                   CASE WHEN a.start_date_local ~ '^\\d{{4}}-\\d{{2}}-\\d{{2}}[ T]\\d{{2}}' THEN
                        CASE WHEN substring(a.start_date_local, 12, 2)::int < 12 THEN 'morning'
                             WHEN substring(a.start_date_local, 12, 2)::int < 17 THEN 'afternoon'
                             ELSE 'evening' END
                   END AS time_of_day,
                   NULLIF(a.distance_mi, 0) AS distance_mi,
                   NULLIF(a.moving_hours, 0) AS moving_hours,
                   CASE WHEN a.sport_group IN ('run', 'walk') AND a.distance_mi > 0
                        THEN a.moving_time / 60.0 / a.distance_mi END AS pace_min_mi,
                   CASE WHEN a.sport_group = 'ride' THEN NULLIF(a.avg_mph, 0) END AS avg_mph,
                   hr.bpm AS heart_rate,
                   CASE WHEN hr.bpm > 0 AND a.moving_time > 0 AND a.distance > 0
                        THEN a.distance / (a.moving_time / 60.0 * hr.bpm) END AS meters_per_beat,
                   NULLIF(w.strain, 0) AS workout_strain,
                   CASE WHEN w.strain >= 12 THEN 'hard' WHEN w.strain >= 8 THEN 'moderate'
                        WHEN w.strain > 0 THEN 'easy' END AS effort,
                   w.id IS NOT NULL AS has_whoop_workout,
                   day.recovery_score, day.recovery_zone, day.hrv, day.rhr,
                   day.sleep_hours, day.sleep_performance, day.sleep_bucket,
                   day.next_recovery, day.recovery_change, day.hrv_change, day.rhr_change
              FROM strava_activity a
              LEFT JOIN LATERAL (
                    SELECT ww.id, ww.strain, ww.average_heart_rate
                      FROM whoop_workout ww
                     WHERE ww.start BETWEEN a.start_date - interval '30 minutes'
                                        AND a.start_date + interval '30 minutes'
                     ORDER BY abs(extract(epoch FROM ww.start - a.start_date))
                     LIMIT 1
              ) w ON TRUE
              LEFT JOIN LATERAL (
                    SELECT COALESCE(NULLIF(a.average_heartrate, 0), NULLIF(w.average_heart_rate, 0)) AS bpm
              ) hr ON TRUE
              LEFT JOIN day ON day.date = a.activity_date
             WHERE NOT COALESCE(a.is_sample, FALSE)
               AND a.activity_date >= (SELECT min(date) FROM rec)
        """)

    def action_open_activity(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'strava.activity',
            'res_id': self.activity_id.id,
            'view_mode': 'form',
        }


class FitnessDay(models.Model):
    """One row per WHOOP day with that day's Strava training and how the body
    responded the next morning."""
    _name = 'fitness.day'
    _description = 'Training Day vs. Recovery'
    _auto = False
    _order = 'date desc'
    _rec_name = 'date'

    date = fields.Date(readonly=True)
    day_type = fields.Selection([
        ('rest', 'Rest Day'), ('light', 'Light'), ('moderate', 'Moderate'), ('hard', 'Hard'),
    ], readonly=True, help="Rest = no Strava activity. Otherwise from WHOOP day strain: "
                           "light < 12, moderate 12-15, hard 15+")
    activity_count = fields.Integer(string='Activities', aggregator='sum', readonly=True)
    distance_mi = fields.Float(string='Distance (mi)', digits=(16, 2), aggregator='sum', readonly=True)
    run_mi = fields.Float(string='Run (mi)', digits=(16, 2), aggregator='sum', readonly=True)
    ride_mi = fields.Float(string='Ride (mi)', digits=(16, 2), aggregator='sum', readonly=True)
    moving_hours = fields.Float(string='Moving Time (h)', digits=(16, 2), aggregator='sum', readonly=True)
    day_strain = fields.Float(string='Day Strain', digits=(16, 1), aggregator='avg', readonly=True)
    last_session = fields.Selection(TIMES_OF_DAY + [('none', 'No Run or Ride')], string='Last Session', readonly=True,
                                    help="Time of day of the day's last run or ride (walks are ignored)")
    night_sleep_hours = fields.Float(string='Sleep That Night (h)', digits=(16, 2), aggregator='avg', readonly=True)
    night_sleep_performance = fields.Float(string='Sleep Performance That Night %', digits=(16, 0), aggregator='avg', readonly=True)
    night_deep_hours = fields.Float(string='Deep Sleep That Night (h)', digits=(16, 2), aggregator='avg', readonly=True)
    night_rem_hours = fields.Float(string='REM That Night (h)', digits=(16, 2), aggregator='avg', readonly=True)
    night_disturbances = fields.Float(string='Disturbances That Night', digits=(16, 1), aggregator='avg', readonly=True)
    night_respiratory_rate = fields.Float(string='Respiratory Rate That Night', digits=(16, 1), aggregator='avg', readonly=True)

    recovery_score = fields.Float(string='Recovery %', digits=(16, 0), aggregator='avg', readonly=True)
    recovery_zone = fields.Selection(ZONES, readonly=True)
    hrv = fields.Float(string='HRV (ms)', digits=(16, 1), aggregator='avg', readonly=True)
    rhr = fields.Float(string='Resting HR', digits=(16, 0), aggregator='avg', readonly=True)
    sleep_hours = fields.Float(string='Sleep (h)', digits=(16, 2), aggregator='avg', readonly=True)
    sleep_performance = fields.Float(string='Sleep Performance %', digits=(16, 0), aggregator='avg', readonly=True)
    next_recovery = fields.Float(string='Next-Day Recovery %', digits=(16, 0), aggregator='avg', readonly=True)
    recovery_change = fields.Float(string='Recovery Change', digits=(16, 0), aggregator='avg', readonly=True)
    hrv_change = fields.Float(string='HRV Change (ms)', digits=(16, 1), aggregator='avg', readonly=True)
    rhr_change = fields.Float(string='Resting HR Change', digits=(16, 1), aggregator='avg', readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(f"""
            CREATE VIEW {self._table} AS
            WITH {WHOOP_DAY_SQL},
            act AS (
                SELECT activity_date AS date, count(*) AS activity_count,
                       sum(distance_mi) AS distance_mi,
                       sum(distance_mi) FILTER (WHERE sport_group = 'run') AS run_mi,
                       sum(distance_mi) FILTER (WHERE sport_group = 'ride') AS ride_mi,
                       sum(moving_hours) AS moving_hours,
                       max(substring(start_date_local, 12, 2)::int)
                           FILTER (WHERE sport_group IN ('run', 'ride') AND start_date_local ~ '^\\d{{4}}-\\d{{2}}-\\d{{2}}[ T]\\d{{2}}') AS last_hour
                  FROM strava_activity
                 WHERE NOT COALESCE(is_sample, FALSE)
                 GROUP BY activity_date
            )
            SELECT row_number() OVER (ORDER BY day.date) AS id, day.date,
                   CASE WHEN act.date IS NULL THEN 'rest'
                        WHEN day.day_strain >= 15 THEN 'hard'
                        WHEN day.day_strain >= 12 THEN 'moderate'
                        ELSE 'light' END AS day_type,
                   COALESCE(act.activity_count, 0) AS activity_count,
                   COALESCE(act.distance_mi, 0) AS distance_mi,
                   COALESCE(act.run_mi, 0) AS run_mi,
                   COALESCE(act.ride_mi, 0) AS ride_mi,
                   COALESCE(act.moving_hours, 0) AS moving_hours,
                   CASE WHEN act.last_hour IS NULL THEN 'none'
                        WHEN act.last_hour < 12 THEN 'morning'
                        WHEN act.last_hour < 17 THEN 'afternoon'
                        ELSE 'evening' END AS last_session,
                   day.night_sleep_hours, day.night_sleep_performance, day.night_deep_hours,
                   day.night_rem_hours, day.night_disturbances, day.night_respiratory_rate,
                   day.day_strain, day.recovery_score, day.recovery_zone, day.hrv, day.rhr,
                   day.sleep_hours, day.sleep_performance,
                   day.next_recovery, day.recovery_change, day.hrv_change, day.rhr_change
              FROM day
              LEFT JOIN act ON act.date = day.date
        """)
