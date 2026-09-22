"""Booking settings and approval-policy permissions for native resources."""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError


class ResourceResource(models.Model):
    # Add booking-specific fields to Odoo's native resource model.

    _inherit = "resource.resource"

    resource_category=fields.Selection(
        [
            ('room', 'Room'),
            ('equipment', 'Equipment')
        ],
        required=True,
        default='room'
    )
    advance_booking_limit = fields.Integer(
        string="Advance Booking Limit (Months)",
        default=3,
    )
    is_available_for_booking = fields.Boolean(
        string="Available for Booking",
        default=False,
    )
    booking_description = fields.Text()
    booking_ids = fields.One2many(
        "booking.booking",
        "resource_id",
        string="Bookings",
    )
    approval_policy = fields.Selection(
        [
            ("manual", "Manual Approval"),
            ("auto", "Automatic Approval"),
        ],
        string="Booking Approval",
        required=True,
        default="auto",
)
