{
    'name': 'nko Test Module - Emergency Contact on Sale Order',
    'version': '1.0',
    'category': 'Sales',
    'summary': 'Testing the addition of emergency contact fields to sales orders',
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
        'views/product_view.xml',
    ],
    'images': [
        'static/description/icon.png',
        'static/description/cover.png',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}