"""Convert resource-local slots to UTC and build booking display names."""

from datetime import datetime, time, timedelta, timezone

from pytz import AmbiguousTimeError, NonExistentTimeError, timezone as get_timezone

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class BookingSchedule(models.Model):
    _inherit = "booking.booking"

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
