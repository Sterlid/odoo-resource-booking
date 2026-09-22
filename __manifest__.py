# -*- coding: utf-8 -*-
{
    'name': 'Booking',
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
    ],
    'demo': [],
    'installable': True,
    'application': True,
    'assets': {},
    'license': 'LGPL-3',
}
