from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import tagged

from .test_booking_ownership import BookingCase


@tagged("post_install", "-at_install")
class TestBookingNotifications(BookingCase):
    def _approval_activity_domain(self):
        return [
            ("res_model", "=", "booking.booking"),
            ("res_id", "=", self.booking.id),
            ("activity_type_id", "=", self.env.ref("booking.mail_activity_booking_approval").id),
            ("user_id", "=", self.admin.id),
        ]

    def _owner_reminder_notifications(self, subject):
        messages = self.env["mail.message"].search([
            ("model", "=", "booking.booking"),
            ("res_id", "=", self.booking.id),
            ("subject", "=", subject),
        ])
        return self.env["mail.notification"].search([
            ("mail_message_id", "in", messages.ids),
            ("res_partner_id", "=", self.owner.partner_id.id),
        ])

    def test_manual_approval_activity_and_catch_up(self):
        activities = self.env["mail.activity"]
        domain = self._approval_activity_domain()
        self.assertTrue(self.booking.approval_notice_sent)
        self.assertEqual(activities.search_count(domain), 1)

        # Simulate a pending booking created before notification deployment.
        activities.search(domain).unlink()
        self.booking.sudo().write({"approval_notice_sent": False})
        self.Booking._cron_notify_pending_approvals()
        self.Booking._cron_notify_pending_approvals()
        self.assertTrue(self.booking.approval_notice_sent)
        self.assertEqual(activities.search_count(domain), 1)

        self.booking.with_user(self.admin).action_approve()
        self.assertEqual(activities.search_count(domain), 0)

    def test_confirmed_owner_gets_each_inbox_reminder_once(self):
        self.owner.notification_type = "inbox"
        start = self.booking.start_datetime
        day_subject = "Your booking starts within 24 hours"
        hour_subject = "Your booking starts within one hour"

        with patch.object(fields.Datetime, "now", return_value=start - timedelta(hours=23)):
            self.Booking._cron_send_booking_reminders()
        self.assertFalse(self.booking.reminder_day_sent)

        self.booking.with_user(self.admin).action_approve()
        with patch.object(fields.Datetime, "now", return_value=start - timedelta(hours=23)):
            self.Booking._cron_send_booking_reminders()
            self.Booking._cron_send_booking_reminders()
        day_notifications = self._owner_reminder_notifications(day_subject)
        self.assertTrue(self.booking.reminder_day_sent)
        self.assertEqual(len(day_notifications), 1)
        self.assertEqual(day_notifications.notification_type, "inbox")

        with patch.object(fields.Datetime, "now", return_value=start - timedelta(minutes=45)):
            self.Booking._cron_send_booking_reminders()
            self.Booking._cron_send_booking_reminders()
        hour_notifications = self._owner_reminder_notifications(hour_subject)
        self.assertTrue(self.booking.reminder_hour_sent)
        self.assertEqual(len(hour_notifications), 1)
        self.assertEqual(hour_notifications.notification_type, "inbox")
