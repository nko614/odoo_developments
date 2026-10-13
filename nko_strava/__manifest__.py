{
    'name': 'Strava',
    'version': '20.0.1.0.0',
    'summary': 'Your Strava activities and WHOOP recovery, sleep and strain in Odoo, with route maps',
    'author': 'NKO',
    'category': 'Extra Tools',
    'depends': ['base', 'web', 'base_setup'],
    'external_dependencies': {'python': ['requests']},
    'data': [
        'security/ir.access.csv',
        'views/fitness_connection_views.xml',
        'views/strava_activity_views.xml',
        'views/strava_athlete_stat_views.xml',
        'views/whoop_views.xml',
        'views/res_config_settings_views.xml',
        'views/menus.xml',
        'data/ir_cron.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'nko_strava/static/src/**/*',
        ],
    },
    'application': True,
    'license': 'LGPL-3',
}
