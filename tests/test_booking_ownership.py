from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


class BookingCase(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        user_env = cls.env(context=dict(cls.env.context, no_reset_password=True))
        cls.owner = new_test_user(
            user_env, login="booking_owner_test",
            groups="base.group_user,booking.group_booking_user",
        )
        cls.other = new_test_user(
            user_env, login="booking_other_test",
            groups="base.group_user,booking.group_booking_user",
        )
        # Admins may belong to both groups: the own-bookings rule must not win.
        cls.admin = new_test_user(
            user_env, login="booking_admin_test",
            groups="base.group_user,booking.group_booking_user,booking.group_booking_admin",
        )
        calendar = cls.env["resource.calendar"].create({
            "name": "Ownership test hours", "tz": "UTC",
            "attendance_ids": [Command.clear()] + [Command.create({
                "name": "Daytime", "dayofweek": str(day),
                "hour_from": 8, "hour_to": 18, "day_period": "morning",
            }) for day in range(7)],
        })
        cls.resource = cls.env["resource.resource"].create({
            "name": "Ownership test room", "resource_type": "material",
            "calendar_id": calendar.id, "tz": "UTC",
            "is_available_for_booking": True, "approval_policy": "manual",
        })
        cls.values = {
            "resource_id": cls.resource.id,
            "booking_date": fields.Date.today() + timedelta(days=8),
            "time_slot": "600",
        }
        cls.Booking = cls.env["booking.booking"].with_context(
            tracking_disable=True, mail_create_nolog=True,
        )
        cls.booking = cls.Booking.with_user(cls.owner).create(cls.values)


@tagged("post_install", "-at_install")
class TestBookingOwnership(BookingCase):
    def test_own_booking_read_and_edit(self):
        booking = self.booking.with_user(self.owner)
        booking.write({"additional_notes": "Owner update"})
        booking.write({"time_slot": "660"})
        self.assertEqual(booking.additional_notes, "Owner update")
        self.assertEqual(booking.time_slot, "660")
        self.assertFalse(booking.booking_rights)

    def test_other_booking_hidden_and_protected(self):
        other_model = self.Booking.with_user(self.other)
        self.assertFalse(other_model.search([("id", "=", self.booking.id)]))
        with self.assertRaises(AccessError):
            self.booking.with_user(self.other).read(["additional_notes"])
        with self.assertRaises(AccessError):
            self.booking.with_user(self.other).write({"additional_notes": "Forbidden"})

    def test_owner_assignment_protected(self):
        with self.assertRaises(AccessError):
            self.Booking.with_user(self.owner).create({
                **self.values, "time_slot": "720", "user_id": self.other.id,
            })
        with self.assertRaises(AccessError):
            self.booking.with_user(self.owner).write({"user_id": self.other.id})
        booking = self.Booking.with_user(self.owner).with_context(
            default_user_id=self.other.id,
        ).create({**self.values, "time_slot": "720"})
        self.assertEqual(booking.user_id, self.owner)

    def test_admin_can_assign_and_edit_all(self):
        booking = self.booking.with_user(self.admin)
        self.assertTrue(booking.booking_rights)
        booking.write({"user_id": self.other.id, "additional_notes": "Admin update"})
        self.assertEqual(booking.user_id, self.other)
        self.assertFalse(self.Booking.with_user(self.owner).search([
            ("id", "=", booking.id),
        ]))
        created = self.Booking.with_user(self.admin).create({
            **self.values, "time_slot": "720", "user_id": self.owner.id,
        })
        self.assertEqual(created.user_id, self.owner)

    def test_hidden_booking_still_blocks_overlap(self):
        for state in ("pending_approval", "confirmed"):
            self.booking.with_user(self.admin).write({"approval_status": state})
            with self.assertRaises(ValidationError), self.cr.savepoint():
                self.Booking.with_user(self.other).create(self.values)
        adjacent = self.Booking.with_user(self.other).create({
            **self.values, "time_slot": "660",
        })
        with self.assertRaises(ValidationError), self.cr.savepoint():
            adjacent.write({"time_slot": "630"})

    def test_mixed_owner_batch_is_rejected_before_editing(self):
        other_booking = self.Booking.with_user(self.other).create({
            **self.values, "time_slot": "720",
        })
        with self.assertRaises(AccessError):
            (self.booking | other_booking).with_user(self.owner).write({
                "additional_notes": "Forbidden batch",
            })
        self.assertFalse(self.booking.additional_notes)

    def test_user_cannot_delete_own_booking(self):
        with self.assertRaises(AccessError):
            self.booking.with_user(self.owner).unlink()
