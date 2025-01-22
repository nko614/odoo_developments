{
    'name': 'Prevent Duplicate SKUs',
    'version': '1.0',
    'category': 'Tools',
    'summary': 'Ensure unique SKUs in your product catalog',
    'description': """
        Prevent duplicate SKUs (default_code) in Odoo by validating that each product has a unique Reference Number.
        This ensures data integrity and avoids conflicts during product creation or updates.
    """,
    'description_html': 'static/description/description.html',
    'author': 'Nicholas Kosinski (nko)',
    'website': 'https://odoo.com',
    'depends': ['base'],
    'data': ['views/product_view.xml'],
    'images': [
        'static/description/icon.png',
        'static/description/cover.png',
    ],
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
