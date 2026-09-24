"""Booking date, calendar, policy and overlap validation."""

from contextlib import contextmanager
from datetime import datetime, time, timedelta, timezone

from dateutil.relativedelta import relativedelta
from psycopg2.errors import ExclusionViolation
from pytz import timezone as get_timezone

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class BookingValidation(models.Model):
    _inherit = "booking.booking"

    @api.model
    def _minimum_booking_start(self):
        now = fields.Datetime.now()
        if self.env.user.has_group("booking.group_booking_admin"):
            tz = get_timezone(self.env.user.tz or "UTC")
            tomorrow = now.replace(tzinfo=timezone.utc).astimezone(tz).date() + timedelta(days=1)
            return tz.localize(datetime.combine(tomorrow, time.min)).astimezone(
                timezone.utc
            ).replace(tzinfo=None)
        return now + timedelta(days=7)

    @api.model
    def _check_minimum_notice(self, start):
        # Apply when creating/rescheduling, not when approving an older request.
        if start < self._minimum_booking_start():
            if self.env.user.has_group("booking.group_booking_admin"):
                raise ValidationError(_("Booking Admins can book from tomorrow onwards."))
            raise ValidationError(_("Bookings must start at least 7 days (168 hours) in advance."))

    @contextmanager
    def _protect_booking_overlap(self):
        # Flush inside the savepoint so deferred ORM writes are checked before
        # returning. Roll back the whole batch if any booking conflicts.
        try:
            with self.env.cr.savepoint():
                yield
        except ExclusionViolation as exc:
            if exc.diag.constraint_name != "booking_booking_no_active_overlap":
                raise
            raise ValidationError(_(
                "This resource already has a pending or confirmed booking during "
                "this period. Please choose another time or resource."
            )) from None

    @api.constrains("start_datetime", "end_datetime")
    def _check_booking_dates(self):
        for booking in self:
            if (
                booking.start_datetime
                and booking.end_datetime
                and booking.start_datetime >= booking.end_datetime
            ):
                raise ValidationError(
                    _("The booking end time must be later than its start time.")
                )

    @api.constrains(
        "resource_id",
        "start_datetime",
        "end_datetime",
        "approval_status",
    )
    def _check_resource_availability(self):
        # Require active bookings to fit in one available work interval.

        # Odoo stores Datetime values as naive UTC values.  The resource API
        # requires timezone-aware datetimes and converts them to the resource
        # calendar's timezone internally.  ``compute_leaves=True`` subtracts
        # both calendar closures and resource-specific leave intervals.

        active_bookings = self.filtered(
            lambda booking: (
                booking.resource_id
                and booking.start_datetime
                and booking.end_datetime
                and booking.start_datetime < booking.end_datetime
                and booking.approval_status in ("pending_approval", "confirmed")
            )
        )

        for booking in active_bookings:
            if (
                not booking.resource_id.active
                or not booking.resource_id.is_available_for_booking
                or booking.resource_id.resource_type != "material"
            ):
                raise ValidationError(_("This resource is not available for booking."))
            start = fields.Datetime.to_datetime(booking.start_datetime).replace(
                tzinfo=timezone.utc
            )
            end = fields.Datetime.to_datetime(booking.end_datetime).replace(
                tzinfo=timezone.utc
            )

            intervals_by_resource, _calendar_intervals = (
                booking.resource_id._get_valid_work_intervals(
                    start,
                    end,
                    compute_leaves=True,
                )
            )
            intervals = intervals_by_resource[booking.resource_id.id]

            fits_inside_one_interval = any(
                interval_start <= start and interval_end >= end
                for interval_start, interval_end, _metadata in intervals
            )
            if not fits_inside_one_interval:
                raise ValidationError(
                    _(
                        "%(resource)s is not available for the complete "
                        "booking period according to its working time.",
                        resource=booking.resource_id.display_name,
                    )
                )

    @api.constrains(
        "resource_id",
        "start_datetime",
        "end_datetime",
        "booking_date",
        "time_slot",
        "approval_status",
    )
    def _check_booking_policy(self):
        now = fields.Datetime.now().replace(tzinfo=timezone.utc)
        for booking in self.filtered(
            lambda b: b.approval_status in ("pending_approval", "confirmed")
        ):
            resource = booking.resource_id
            tz = get_timezone(resource.tz or "UTC")

            start = booking.start_datetime.replace(
                tzinfo=timezone.utc
            ).astimezone(tz)

            end = booking.end_datetime.replace(
                tzinfo=timezone.utc
            ).astimezone(tz)

            if booking.start_datetime >= booking.end_datetime:
                raise ValidationError(_("End must be later than start."))

            if start < now.astimezone(tz):
                raise ValidationError(_("The booking cannot start in the past."))

            if start > now.astimezone(tz) + relativedelta(
                months=resource.advance_booking_limit
            ):
                raise ValidationError(_("The booking is too far in advance."))

            if start.hour + start.minute / 60 > 20.0:
                raise ValidationError(_("The booking cannot start after 8 PM."))

            if end.hour + end.minute / 60 > 21.0:
                raise ValidationError(_("The booking cannot end after 10 PM."))

    @api.constrains(
        "resource_id",
        "start_datetime",
        "end_datetime",
        "approval_status"
    )
    def _check_booking_overlap(self):
        for booking in self.filtered(
            lambda b: (
                b.resource_id
                and b.start_datetime
                and b.end_datetime
                and b.approval_status in ("pending_approval", "confirmed")
            )
        ):
            # Check all reservations, including those hidden by ownership rules.
            # Only a count is used; no other booker's details are returned.
            overlap = self.sudo().search_count(
                [
                    ("id", "!=", booking.id),
                    ("resource_id", "=", booking.resource_id.id),
                    ("approval_status", "in", ("pending_approval", "confirmed")),
                    ("start_datetime", "<", booking.end_datetime),
                    ("end_datetime", ">", booking.start_datetime),
                ],
                limit=1,
            )
            if overlap:
                raise ValidationError(
                    _(
                        "%(resource)s is already booked during this period.",
                        resource=booking.resource_id.display_name,
                    )
                )
