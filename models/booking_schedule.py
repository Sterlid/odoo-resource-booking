"""Convert resource-local slots to UTC and build booking display names."""

from datetime import datetime, time, timedelta, timezone

from pytz import AmbiguousTimeError, NonExistentTimeError, timezone as get_timezone

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class BookingSchedule(models.Model):
    _inherit = "booking.booking"

    @api.model
    def get_available_time_slots(self, resource_id, booking_date, booking_id=False):
        """Return slots inside the resource calendar and outside existing bookings."""
        resource = self.env["resource.resource"].browse(resource_id).exists()
        date = fields.Date.to_date(booking_date) if booking_date else False
        if not resource or not date:
            return []
        resource.check_access("read")
        if (
            not resource.active
            or not resource.is_available_for_booking
            or resource.resource_type != "material"
        ):
            return []

        minimum_start = self._minimum_booking_start()
        candidates = []
        for minute in range(0, 20 * 60 + 1, 30):
            try:
                start, end = self._slot_datetimes(resource, date, str(minute))
            except ValidationError:
                continue
            if start < minimum_start:
                continue
            candidates.append((str(minute), start, end))
        if not candidates:
            return []

        start_utc = candidates[0][1].replace(tzinfo=timezone.utc)
        end_utc = candidates[-1][2].replace(tzinfo=timezone.utc)
        work_intervals, _ = resource._get_valid_work_intervals(
            start_utc, end_utc, compute_leaves=True,
        )
        intervals = work_intervals[resource.id]
        domain = [
            ("resource_id", "=", resource.id),
            ("approval_status", "in", ("pending_approval", "confirmed")),
            ("start_datetime", "<", candidates[-1][2]),
            ("end_datetime", ">", candidates[0][1]),
        ]
        if booking_id:
            current = self.browse(booking_id).exists()
            current.check_access("read")
            domain.append(("id", "!=", booking_id))
        occupied = self.sudo().search(domain)
        return [
            slot for slot, start, end in candidates
            if any(
                interval_start <= start.replace(tzinfo=timezone.utc)
                and interval_end >= end.replace(tzinfo=timezone.utc)
                for interval_start, interval_end, _metadata in intervals
            )
            and not any(
                other.start_datetime < end and other.end_datetime > start
                for other in occupied
            )
        ]

    @api.model
    def _slot_datetimes(self, resource, booking_date, time_slot):
        """Convert a one-hour local slot to Odoo's naive UTC datetimes."""
        date = fields.Date.to_date(booking_date)
        if not resource or not date or time_slot is None or time_slot is False:
            raise ValidationError(_("Choose a resource, date, and time slot."))
        try:
            minute = int(time_slot)
        except (TypeError, ValueError):
            raise ValidationError(_("Choose a valid time slot.")) from None
        if minute not in range(0, 20 * 60 + 1, 30):
            raise ValidationError(_("Choose a valid time slot."))

        local_start = datetime.combine(date, time.min) + timedelta(minutes=minute)
        try:
            start = get_timezone(resource.tz or "UTC").localize(
                local_start, is_dst=None
            )
        except (AmbiguousTimeError, NonExistentTimeError):
            raise ValidationError(
                _("This time slot does not exist uniquely in the resource timezone.")
            ) from None
        start_utc = start.astimezone(timezone.utc)
        end_utc = start_utc + timedelta(hours=1)
        return (
            start_utc.replace(tzinfo=None),
            end_utc.replace(tzinfo=None),
        )

    @api.model
    def _format_booking_name(self, resource, start, end):
        """Format the UTC booking interval in the resource's local timezone."""
        tz = get_timezone(resource.tz or "UTC")
        start = start.replace(tzinfo=timezone.utc).astimezone(tz)
        end = end.replace(tzinfo=timezone.utc).astimezone(tz)
        months = (
            "Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
        )
        start_period = "AM" if start.hour < 12 else "PM"
        end_period = "AM" if end.hour < 12 else "PM"
        start_label = f"{start.hour % 12 or 12}:{start.minute:02d}"
        end_label = f"{end.hour % 12 or 12}:{end.minute:02d} {end_period}"
        if start_period != end_period:
            start_label += f" {start_period}"
        return (
            f"{resource.name} – {start.day} {months[start.month - 1]}, "
            f"{start_label}–{end_label}"
        )

    @api.onchange("resource_id", "booking_date", "time_slot")
    def _onchange_time_slot(self):
        for booking in self:
            if booking.resource_id and booking.booking_date and booking.time_slot:
                booking.start_datetime, booking.end_datetime = (
                    booking._slot_datetimes(
                        booking.resource_id, booking.booking_date, booking.time_slot
                    )
                )
