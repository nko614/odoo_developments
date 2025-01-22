{
    'name': 'NKO Test',
    'version': '1.0',
    'category': 'Sales',
    'summary': 'Add emergency contact fields to sales orders',
    'description': '''
        Adds emergency contact information to sales orders:
        - Emergency Contact
        - Emergency Phone Number
    ''',
    'depends': ['sale'],
    'data': [
        'views/sale_order_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
} 