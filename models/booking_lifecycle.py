"""Cancellation, completion and reversible cleanup of booking history."""

from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError


class BookingLifecycle(models.Model):
    _inherit = "booking.booking"

    can_cancel = fields.Boolean(compute="_compute_can_cancel")
    active = fields.Boolean(default=True)
    closed_at = fields.Datetime(readonly=True, copy=False, index=True)

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [dict(vals) for vals in vals_list]
        for vals in vals_list:
            if vals.get("closed_at"):
                raise AccessError(_("The closing time is recorded automatically."))
            vals["closed_at"] = False
        return super().create(vals_list)

    def write(self, vals):
        if "closed_at" in vals:
            raise AccessError(_("The closing time is recorded automatically."))
        if vals.get("approval_status") in ("cancelled", "completed"):
            # Repeated writes must not postpone automatic archiving.
            vals = dict(vals)
            with self.env.cr.savepoint():
                for booking in self:
                    values = dict(vals)
                    if booking.approval_status != vals["approval_status"]:
                        values["closed_at"] = fields.Datetime.now()
                    super(BookingLifecycle, booking).write(values)
            return True
        return super().write(vals)

    @api.constrains("active", "approval_status")
    def _check_archive_status(self):
        if any(
            not booking.active and booking.approval_status not in ("cancelled", "completed")
            for booking in self
        ):
            raise ValidationError(_("Only cancelled or completed bookings can be archived."))

    @api.model
    def _cron_archive_bookings(self):
        cutoff = fields.Datetime.now() - timedelta(days=30)
        domain = [
            ("active", "=", True),
            ("approval_status", "in", ("cancelled", "completed")),
            "|", ("closed_at", "<=", cutoff),
            "&", ("closed_at", "=", False),
            "&", ("end_datetime", "<=", cutoff), ("write_date", "<=", cutoff),
        ]
        # Legacy records have no closing timestamp: require both the scheduled
        # end and last modification to be old enough before archiving them.
        bookings = self.search(domain, order="id", limit=200)
        bookings.write({"active": False})
        self.env["ir.cron"]._notify_progress(
            done=len(bookings), remaining=self.search_count(domain),
        )

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
