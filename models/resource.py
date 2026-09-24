"""Booking settings and approval-policy permissions for native resources."""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError


class ResourceResource(models.Model):
    # Add booking-specific fields to Odoo's native resource model.

    _inherit = "resource.resource"

    resource_category = fields.Selection(
        [
            ("room", "Room"),
            ("equipment", "Equipment"),
        ],
        required=True,
        default="room",
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

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and not self.env.user.has_group("booking.group_booking_admin"):
            default_policy = self.default_get(["approval_policy"]).get("approval_policy")
            if any(vals.get("approval_policy", default_policy) != "auto" for vals in vals_list):
                raise AccessError(_("Only Booking Admins can configure booking approval."))
        return super().create(vals_list)

    def write(self, vals):
        if (
            "approval_policy" in vals
            and not self.env.su
            and not self.env.user.has_group("booking.group_booking_admin")
            and any(resource.approval_policy != vals["approval_policy"] for resource in self)
        ):
            raise AccessError(_("Only Booking Admins can change booking approval."))
        return super().write(vals)
