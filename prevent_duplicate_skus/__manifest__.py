{
    'name': 'Prevent Duplicate SKUs',
    'version': '1.0',
    'category': 'Inventory',
    'summary': 'Prevent duplicate product reference numbers',
    'description': '''
        Prevents products from having duplicate reference numbers (SKUs):
        - Checks both product.template and product.product
        - Raises warning if duplicate default_code is found
    ''',
    'depends': ['product'],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
} 