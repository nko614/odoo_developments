{
    'name': 'Emergency Contact on Sale Order',
    'version': '1.0',
    'category': 'Sales',
    'summary': 'Adding Emergency Contact to Sale Order',
    'description': '''
        This module adds emergency contact information to sales orders:
        - Emergency Contact
        - Emergency Phone Number
    ''',
    'description_html': 'static/description/description.html',
    'author': 'Nicholas Kosinski (nko)',
    'website': 'https://odoo.com',
    'depends': ['sale', 'base'],
    'data': [
        'views/sale_order_views.xml',
    ],
    'images': [
        'static/description/icon.png',
        'static/description/cover.png',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}