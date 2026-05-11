{
    'name': 'Foreign Purchase Management',
    'version': '1.0',
    'category': 'Purchase',
    'summary': 'Manage Foreign Purchases, LC/CAD and Shipments',
    'description': """
        This module extends Odoo Purchase to support Foreign/Import purchase workflows.
        - LC/CAD management
        - Cost accumulation at LC/CAD level
    """,
    'author': 'Swenetix Tech Plc',
    'depends': ['purchase', 'account'],
    'data': [
        'security/ir.model.access.csv',
        'data/sequence_data.xml',
        'data/cost_type_data.xml',
        'views/purchase_order_views.xml',
        'views/cost_type_views.xml',
        'views/shipment_views.xml',
        'views/lc_cad_views.xml',
        'views/stock_picking_views.xml',
        'views/menu_views.xml',
        'reports/cost_build_up_template.xml',
        'reports/cost_build_up_report.xml',
    ],
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
