"""Insights: the two SQL views pair Strava activities with WHOOP recovery and sleep."""
from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged

from .test_nko_strava import NkoStravaCase


@tagged('post_install', '-at_install', 'nko_strava')
class TestInsight(NkoStravaCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Act = cls.env['strava.activity']
        Act._nko_load_sample_data()
        # the views leave sample activities out; treat them as real ones here
        Act.search([('is_sample', '=', True)]).write({'is_sample': False})
        cls.env.flush_all()

    def test_activity_rows(self):
        rows = self.env['fitness.activity.insight'].search([])
        first_day = min(self.env['whoop.recovery'].search([]).mapped('date'))
        acts = self.env['strava.activity'].search([('activity_date', '>=', first_day)])
        self.assertTrue(rows)
        self.assertEqual(rows.activity_id, acts)
        self.assertTrue(all(r.recovery_zone and r.recovery_score for r in rows))
        self.assertTrue(rows.filtered('has_whoop_workout'))
        run = rows.filtered(lambda r: r.sport_group == 'run')[:1]
        self.assertGreater(run.pace_min_mi, 0)
        self.assertGreater(run.meters_per_beat, 0)
        self.assertEqual(run.action_open_activity()['res_id'], run.activity_id.id)

    def test_day_rows(self):
        days = self.env['fitness.day'].search([])
        self.assertTrue(days.filtered(lambda d: d.day_type != 'rest'))
        self.assertEqual(len(days), len(set(self.env['whoop.recovery'].search([]).mapped('date'))))
        trained = set(self.env['strava.activity'].search([]).mapped('activity_date'))
        for day in days:
            self.assertEqual(day.day_type == 'rest', day.date not in trained, day.date)
        self.assertTrue(days.filtered('next_recovery'))

    def test_actions_load(self):
        """Every Insights action's views load and their groupings run."""
        for xmlid in ('fitness_activity_insight_action', 'fitness_day_action', 'fitness_day_trend_action',
                      'fitness_day_sleep_action', 'fitness_activity_sleep_action'):
            action = self.env.ref(f'nko_strava.{xmlid}')
            Model = self.env[action.res_model]
            views = [(v.view_id.id, v.view_mode) for v in action.view_ids] or \
                    [(action.view_id.id if mode == action.view_id.type else False, mode)
                     for mode in action.view_mode.split(',')]
            Model.get_views(views + [(False, 'search')])
            measures = [f'{name}:{f.aggregator}' for name, f in Model._fields.items()
                        if f.type in ('float', 'integer') and f.aggregator and name != 'id']
            for name, field in Model._fields.items():
                if field.type == 'selection':
                    self.assertTrue(Model._read_group([], [name], measures), name)

    def test_plain_user_reads_only(self):
        user = new_test_user(self.env, login='nko_insight_user', groups='base.group_user')
        for model in ('fitness.activity.insight', 'fitness.day'):
            recs = self.env[model].with_user(user).search([])
            self.assertTrue(recs.read(['recovery_score']))
            with self.assertRaises(AccessError):
                recs[:1].write({'recovery_score': 1})
