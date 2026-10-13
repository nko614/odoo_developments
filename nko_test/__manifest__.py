{
    'name': 'NFL Teams & Schedule',
    'version': '20.0.1.0.0',
    'summary': 'NFL teams, standings and game schedule pulled from ESPN',
    'author': 'NKO',
    'category': 'Extra Tools',
    'depends': ['base', 'web'],
    'external_dependencies': {'python': ['requests']},
    'data': [
        'security/ir.access.csv',
        'views/nfl_game_views.xml',
        'views/nfl_team_views.xml',
        'views/menus.xml',
        'data/ir_cron.xml',
    ],
    'application': True,
    'license': 'LGPL-3',
}
