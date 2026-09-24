from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged

from .test_booking_ownership import BookingCase


@tagged("post_install", "-at_install")
class TestBookingApproval(BookingCase):
    def test_admin_approves_pending_only(self):
        self.booking.with_user(self.admin).action_approve()
        self.assertEqual(self.booking.approval_status, "confirmed")
        with self.assertRaises(ValidationError):
            self.booking.with_user(self.admin).action_approve()

    def test_owner_cannot_approve_or_forge_status(self):
        with self.assertRaises(AccessError):
            self.booking.with_user(self.owner).action_approve()
        with self.assertRaises(AccessError):
            self.booking.with_user(self.owner).with_context(allow_approval=True).write({
                "approval_status": "confirmed",
            })
        with self.assertRaises(AccessError):
            self.Booking.with_user(self.owner).create({
                **self.values, "time_slot": "720", "approval_status": "confirmed",
            })

    def test_only_admin_can_reschedule_confirmed_booking(self):
        auto_resource = self.resource.copy({"approval_policy": "auto"})
        booking = self.Booking.with_user(self.owner).create({
            **self.values, "resource_id": auto_resource.id,
        })
        for values in (
            {"resource_id": self.resource.id},
            {"time_slot": "720"},
            {"booking_date": self.values["booking_date"] + timedelta(days=1)},
            {"start_datetime": booking.start_datetime + timedelta(hours=1)},
        ):
            with self.assertRaises(ValidationError):
                booking.with_user(self.owner).write(values)

        booking.with_user(self.admin).write({"time_slot": "720"})
        self.assertEqual(booking.approval_status, "confirmed")
        booking.with_user(self.admin).write({"resource_id": self.resource.id})
        self.assertEqual(booking.approval_status, "pending_approval")
        self.assertTrue(booking.approval_notice_sent)
        booking.with_user(self.owner).write({"additional_notes": "Still editable"})
        self.assertEqual(booking.additional_notes, "Still editable")

    def test_closed_calendar_blocks_action_and_direct_confirmation(self):
        self.resource.calendar_id.attendance_ids.unlink()
        for approve in (
            lambda: self.booking.with_user(self.admin).action_approve(),
            lambda: self.booking.with_user(self.admin).write({"approval_status": "confirmed"}),
        ):
            with self.assertRaises(ValidationError):
                approve()
            self.assertEqual(self.booking.approval_status, "pending_approval")

    def test_closed_calendar_blocks_booking_creation(self):
        self.resource.calendar_id.attendance_ids.unlink()
        with self.assertRaises(ValidationError):
            self.Booking.with_user(self.other).create({
                **self.values, "time_slot": "720",
            })

    def test_disabled_resource_blocks_confirmation(self):
        for values in (
            {"is_available_for_booking": False},
            {"is_available_for_booking": True, "active": False},
            {"active": True, "resource_type": "user"},
        ):
            self.resource.write(values)
            with self.assertRaises(ValidationError):
                self.booking.with_user(self.admin).action_approve()
            self.assertEqual(self.booking.approval_status, "pending_approval")

    def test_expired_request_cannot_be_approved(self):
        with patch.object(fields.Datetime, "now", return_value=self.booking.end_datetime + timedelta(days=1)):
            with self.assertRaises(ValidationError):
                self.booking.with_user(self.admin).action_approve()
        self.assertEqual(self.booking.approval_status, "pending_approval")

    def test_invalid_batch_rolls_back_all_confirmations(self):
        other_resource = self.resource.copy({"name": "Unavailable room"})
        other = self.Booking.with_user(self.owner).create({
            **self.values, "resource_id": other_resource.id,
        })
        other_resource.is_available_for_booking = False
        with self.assertRaises(ValidationError):
            (self.booking | other).with_user(self.admin).action_approve()
        self.assertEqual(self.booking.approval_status, "pending_approval")
        self.assertEqual(other.approval_status, "pending_approval")

    def test_final_statuses_cannot_reopen_without_a_conflict(self):
        self.booking.with_user(self.admin).write({"approval_status": "cancelled"})
        for status in ("confirmed", "pending_approval", "completed"):
            with self.assertRaises(ValidationError):
                self.booking.with_user(self.admin).write({"approval_status": status})

    def test_closed_bookings_cannot_be_rescheduled(self):
        for state, slot in (("cancelled", "720"), ("completed", "900")):
            booking = self.Booking.with_user(self.owner).create({
                **self.values, "time_slot": slot,
            })
            if state == "completed":
                booking.with_user(self.admin).action_approve()
                with patch.object(
                    fields.Datetime, "now",
                    return_value=booking.end_datetime + timedelta(seconds=1),
                ):
                    booking.with_user(self.admin).write({"approval_status": state})
            else:
                booking.with_user(self.owner).action_cancel()

            original_slot = booking.time_slot
            for user in (self.owner, self.admin):
                with self.assertRaises(ValidationError):
                    booking.with_user(user).write({"time_slot": str(int(slot) + 30)})
                self.assertEqual(booking.time_slot, original_slot)

    def test_policy_change_does_not_approve_existing_requests(self):
        self.resource.with_user(self.admin).write({"approval_policy": "auto"})
        self.assertEqual(self.booking.approval_status, "pending_approval")
        new_booking = self.Booking.with_user(self.owner).create({
            **self.values, "time_slot": "720",
        })
        self.assertEqual(new_booking.approval_status, "confirmed")

    def test_resource_edit_acl_does_not_grant_approval_policy_rights(self):
        # Simulate another installed app granting resource CRUD, as HR does.
        self.env["ir.model.access"].create({
            "name": "Test extra resource permissions",
            "model_id": self.env.ref("resource.model_resource_resource").id,
            "group_id": self.env.ref("booking.group_booking_user").id,
            "perm_read": True, "perm_write": True, "perm_create": True,
        })
        resource = self.resource.with_user(self.owner)
        resource.write({"name": "Allowed ordinary edit"})
        with self.assertRaises(AccessError):
            resource.write({"approval_policy": "auto"})
        model = self.env["resource.resource"].with_user(self.owner)
        with self.assertRaises(AccessError):
            model.create({"name": "Forbidden policy", "approval_policy": "manual"})
        with self.assertRaises(AccessError):
            model.with_context(default_approval_policy="manual").create({"name": "Forbidden default"})
        created = model.create({"name": "Ordinary resource"})
        self.assertEqual(created.approval_policy, "auto")
