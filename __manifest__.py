# -*- coding: utf-8 -*-
{
    'name': 'Resource Booking & Reservation',
    'version': '1.0',
    'summary': 'Booking system for company resources such as rooms, facilites, and equipment',
    'sequence': 10,
    'description': """
Resource booking system module for Odoo
""",
    'category': 'Booking/Booking',
    'website': 'https://jonathan-gladwin.com',
    'author': 'Jonathan Gladwin',
    'depends': ["base", "resource", "mail"],
    'data': [
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/booking_views.xml",
        "views/resource_views.xml",
        "views/booking_menus.xml",
        "data/booking_cron.xml",
        "data/booking_activity.xml",
    ],
    'demo': [],
    'installable': True,
    'application': True,
    'assets': {
        'web.assets_backend': [
            'booking/static/src/views/booking_date_field.js',
            'booking/static/src/views/booking_time_slot_field.js',
            'booking/static/src/views/save_return_form.js',
            'booking/static/src/views/save_return_form.xml',
        ],
    },
    'license': 'LGPL-3',
}
