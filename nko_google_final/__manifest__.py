{
    'name': 'NKO Google Maps Route Optimization',
    'version': '1.0',
    'category': 'Inventory',
    'summary': 'Optimize delivery routes using Google Maps',
    'description': """
        Optimize delivery routes using Google Maps API:
        - Integrates with Google Maps API for route optimization
        - Calculates optimal delivery sequence
        - Updates delivery order sequence automatically
        - Manual optimization trigger available
    """,
    'depends': [
        'base',
        'stock',
        'google_integration',
    ],
    'data': [
        'views/stock_picking_views.xml',
        'views/res_config_settings_views.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
    'license': 'LGPL-3',
}
