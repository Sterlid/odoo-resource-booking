"""Admin approval activities and reminders for confirmed bookings."""

from datetime import timedelta

from markupsafe import escape

from odoo import _, api, fields, models
from odoo.exceptions import AccessError


APPROVAL_ACTIVITY = "booking.mail_activity_booking_approval"


class BookingNotifications(models.Model):
    _inherit = "booking.booking"

    approval_notice_sent = fields.Boolean(readonly=True, copy=False, default=False)
    reminder_day_sent = fields.Boolean(readonly=True, copy=False, default=False)
    reminder_hour_sent = fields.Boolean(readonly=True, copy=False, default=False)

    @api.model_create_multi
    def create(self, vals_list):
        self._check_reminder_fields(vals_list)
        bookings = super().create(vals_list)
        for booking in bookings.filtered(
            lambda record: record.approval_status == "pending_approval"
        ):
            if booking._schedule_approval_activities():
                booking.sudo().write({"approval_notice_sent": True})
        return bookings

    def write(self, vals):
        self._check_reminder_fields([vals])
        previous_status = {booking.id: booking.approval_status for booking in self}
        values = dict(vals)
        if {"resource_id", "booking_date", "time_slot", "user_id"}.intersection(values):
            values.update(reminder_day_sent=False, reminder_hour_sent=False)
        result = super().write(values)
        for booking in self:
            if (
                previous_status[booking.id] == "confirmed"
                and booking.approval_status == "pending_approval"
            ):
                sent = booking._schedule_approval_activities()
                booking.sudo().write({"approval_notice_sent": sent})
            if previous_status[booking.id] != "pending_approval":
                continue
            if booking.approval_status == "confirmed":
                booking.sudo().activity_feedback(
                    [APPROVAL_ACTIVITY], feedback=_("Booking approved.")
                )
            elif booking.approval_status == "cancelled":
                booking.sudo().activity_unlink([APPROVAL_ACTIVITY])
        return result

    def _check_reminder_fields(self, vals_list):
        if self.env.su:
            return
        if any(
            {"approval_notice_sent", "reminder_day_sent", "reminder_hour_sent"}.intersection(vals)
            for vals in vals_list
        ):
            raise AccessError(_("Booking notification status is managed automatically."))

    def _schedule_approval_activities(self):
        self.ensure_one()
        admin_group = self.env.ref("booking.group_booking_admin")
        admins = admin_group.users.filtered(lambda user: user.active and not user.share)
        for admin in admins:
            self.sudo().activity_schedule(
                APPROVAL_ACTIVITY,
                user_id=admin.id,
                summary=_("Approve booking"),
                note=_("Review this booking and approve it if appropriate."),
            )
        return bool(admins)

    @api.model
    def _cron_notify_pending_approvals(self):
        domain = [
            ("active", "=", True),
            ("approval_status", "=", "pending_approval"),
            ("approval_notice_sent", "=", False),
            ("resource_id.approval_policy", "=", "manual"),
        ]
        bookings = self.search(domain, order="id", limit=200)
        for booking in bookings:
            if booking._schedule_approval_activities():
                booking.sudo().write({"approval_notice_sent": True})
        self.env["ir.cron"]._notify_progress(
            done=len(bookings), remaining=self.search_count(domain),
        )

    @api.model
    def _cron_send_booking_reminders(self):
        now = fields.Datetime.now()
        day_domain = [
            ("active", "=", True),
            ("approval_status", "=", "confirmed"),
            ("reminder_day_sent", "=", False),
            ("start_datetime", ">=", now + timedelta(hours=1)),
            ("start_datetime", "<=", now + timedelta(days=1)),
        ]
        hour_domain = [
            ("active", "=", True),
            ("approval_status", "=", "confirmed"),
            ("reminder_hour_sent", "=", False),
            ("start_datetime", ">", now),
            ("start_datetime", "<", now + timedelta(hours=1)),
        ]
        day_bookings = self.search(day_domain, order="start_datetime, id", limit=200)
        for booking in day_bookings:
            booking._send_owner_reminder(_("Your booking starts within 24 hours"), "reminder_day_sent")

        hour_bookings = self.search(hour_domain, order="start_datetime, id", limit=200)
        for booking in hour_bookings:
            booking._send_owner_reminder(_("Your booking starts within one hour"), "reminder_hour_sent")

        remaining = self.search_count(day_domain) + self.search_count(hour_domain)
        self.env["ir.cron"]._notify_progress(
            done=len(day_bookings) + len(hour_bookings), remaining=remaining,
        )

    def _send_owner_reminder(self, subject, sent_field):
        self.ensure_one()
        if self.user_id.active and self.user_id.partner_id:
            self.sudo().message_notify(
                partner_ids=[self.user_id.partner_id.id],
                subject=subject,
                body=_("Upcoming booking: %(booking)s", booking=escape(self.name)),
            )
        self.sudo().write({sent_field: True})
