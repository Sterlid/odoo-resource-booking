"""Owner-only check-in within fifteen minutes of a confirmed booking's start."""

from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError


class BookingCheckin(models.Model):
    _inherit = "booking.booking"

    checked_in_at = fields.Datetime(readonly=True, copy=False, tracking=True)
    checked_in_by = fields.Many2one("res.users", readonly=True, copy=False, tracking=True)
    can_check_in = fields.Boolean(compute="_compute_can_check_in")

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [dict(vals) for vals in vals_list]
        for vals in vals_list:
            if vals.get("checked_in_at") or vals.get("checked_in_by"):
                raise AccessError(_("Use the Check In button to record attendance."))
            # Context defaults must not fabricate attendance on new bookings.
            vals.update(checked_in_at=False, checked_in_by=False)
        return super().create(vals_list)

    def write(self, vals):
        if {"checked_in_at", "checked_in_by"}.intersection(vals):
            raise AccessError(_("Use the Check In button to record attendance."))
        return super().write(vals)

    @api.depends("approval_status", "active", "user_id", "start_datetime", "checked_in_at")
    @api.depends_context("uid")
    def _compute_can_check_in(self):
        now = fields.Datetime.now()
        for booking in self:
            booking.can_check_in = bool(
                booking.active
                and booking.approval_status == "confirmed"
                and not booking.checked_in_at
                and booking.user_id == self.env.user
                and booking.start_datetime
                and booking.start_datetime - timedelta(minutes=15)
                <= now <= booking.start_datetime + timedelta(minutes=15)
            )

    def action_check_in(self):
        self.check_access("write")
        if any(booking.user_id != self.env.user for booking in self):
            raise AccessError(_("Only the booking owner can check in."))
        # Evaluate time on every invocation, even when an open form is stale.
        self._compute_can_check_in()
        if any(not booking.can_check_in for booking in self):
            raise ValidationError(_(
                "Only the owner can check in a confirmed booking, "
                "from 15 minutes before until 15 minutes after its start. "
                "Bookings already checked in cannot be checked in again."
            ))
        return super().write({
            "checked_in_at": fields.Datetime.now(),
            "checked_in_by": self.env.uid,
        })
