"""Manual cancellation and scheduled completion of bookings."""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError


class BookingLifecycle(models.Model):
    _inherit = "booking.booking"

    can_cancel = fields.Boolean(compute="_compute_can_cancel")

    @api.depends("approval_status", "user_id", "start_datetime")
    @api.depends_context("uid")
    def _compute_can_cancel(self):
        now = fields.Datetime.now()
        is_admin = self.env.su or self.env.user.has_group("booking.group_booking_admin")
        for booking in self:
            booking.can_cancel = (
                booking.approval_status in ("pending_approval", "confirmed")
                and (
                    is_admin
                    or (
                        booking.user_id == self.env.user
                        and booking.start_datetime
                        and booking.start_datetime > now
                    )
                )
            )

    def action_cancel(self):
        self.check_access("write")
        if any(
            booking.approval_status not in ("pending_approval", "confirmed")
            for booking in self
        ):
            raise ValidationError(_("Only pending or confirmed bookings can be cancelled."))
        return self.write({"approval_status": "cancelled"})

    def _check_lifecycle_change(self, status):
        # Called by write(), so imports and RPC calls follow the button's rules.
        is_admin = self.env.su or self.env.user.has_group("booking.group_booking_admin")
        if not is_admin and status != "cancelled":
            raise AccessError(_("Only Booking Admins can change this booking status."))
        now = fields.Datetime.now()
        for booking in self:
            if status == "cancelled" and not is_admin:
                if booking.user_id != self.env.user:
                    raise AccessError(_("You can only cancel your own bookings."))
                if not booking.start_datetime or booking.start_datetime <= now:
                    raise ValidationError(_(
                        "You can only cancel a booking before it starts. "
                        "Contact a Booking Admin for assistance."
                    ))
            if status == "completed" and (
                not booking.end_datetime or booking.end_datetime > now
            ):
                raise ValidationError(_("A booking can only be completed after it ends."))

    @api.model
    def _cron_complete_bookings(self):
        # UTC timestamps match Odoo's stored datetimes. Bounded batches keep
        # each cron transaction small; progress reporting schedules any backlog.
        domain = [
            ("approval_status", "=", "confirmed"),
            ("end_datetime", "<=", fields.Datetime.now()),
        ]
        bookings = self.search(domain, order="end_datetime, id", limit=200)
        bookings.write({"approval_status": "completed"})
        self.env["ir.cron"]._notify_progress(
            done=len(bookings), remaining=self.search_count(domain),
        )
