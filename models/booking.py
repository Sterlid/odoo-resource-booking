from datetime import datetime, time, timedelta, timezone
from dateutil.relativedelta import relativedelta
from pytz import AmbiguousTimeError, NonExistentTimeError, timezone as get_timezone
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError, AccessError


class ResourceResource(models.Model):
    # Add booking-specific fields to Odoo's native resource model.

    _inherit = "resource.resource"

    booking_mode=fields.Selection(
        [
            ('room', 'Room'),
            ('equipment', 'Equipment')
        ],
        required=True,
        default='room'
    )
    max_advance_months = fields.Integer(
        string="Maximum Advance Booking",
        default=3,
    )
    latest_start_hour = fields.Float(
        string="Latest Start Time",
        default=20.0,
    )
    latest_end_hour = fields.Float(
        string="Latest End Time",
        default=22.0,
    )
    is_bookable = fields.Boolean(
        string="Bookable",
        default=False,
    )
    booking_description = fields.Text()
    booking_ids = fields.One2many(
        "booking.booking",
        "resource_id",
        string="Bookings",
    )


class Booking(models.Model):
    _name = "booking.booking"
    _description = "Resource Booking"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(required=True, default=lambda self: f"{self.env.user.name}'s Booking",)
    additional_notes = fields.Text()
    resource_id = fields.Many2one(
        comodel_name="resource.resource",
        required=True,
        index=True,
        ondelete="cascade",
        domain=[
            ("is_bookable", "=", True),
            ("resource_type", "=", "material"),
        ],
    )
    booking_mode = fields.Selection(
        related="resource_id.booking_mode",
    )
    booking_date = fields.Date(string="Date")
    time_slot = fields.Selection(
        selection=[
            (
                str(minute),
                "%02d:%02d–%02d:%02d" % (
                    minute // 60,
                    minute % 60,
                    (minute + 60) // 60,
                    (minute + 60) % 60,
                ),
            )
            for minute in range(0, 20 * 60 + 1, 30)
        ],
        string="Time Slot (Resource Timezone)",
    )
    calendar_id = fields.Many2one(
        comodel_name="resource.calendar",
        related="resource_id.calendar_id",
        string="Working Time",
        readonly=True,
    )
    user_id = fields.Many2one(
        comodel_name="res.users",
        string="Booked By",
        required=True,
        default=lambda self: self.env.user,
    )
    start_datetime = fields.Datetime(required=True, index=True)
    end_datetime = fields.Datetime(required=True, index=True)
    approval_status = fields.Selection(
        [
            ("pending_approval", "Pending Approval"),
            ("confirmed", "Confirmed"),
            ("cancelled", "Cancelled"),
            ("completed", "Completed"),
        ],
        required=True,
        default="pending_approval",
        index=True,
        tracking=True,
    )
    booking_rights = fields.Boolean(compute="_check_booking_rights")


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

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not self.env.user.has_group("booking.group_booking_admin"):
                if vals.get("approval_status", "pending_approval") != "pending_approval":
                    raise AccessError(_("Only Booking Admins can set approval status."))
                vals["approval_status"] = "pending_approval"
            resource = self.env["resource.resource"].browse(vals.get("resource_id"))
            start, end = self._slot_datetimes(
                resource, vals.get("booking_date"), vals.get("time_slot")
            )
            vals["start_datetime"] = start
            vals["end_datetime"] = end
            vals["name"] = self._format_booking_name(resource, start, end)
        return super().create(vals_list)

    def write(self, vals):
        slot_fields = {
            "resource_id", "booking_date", "time_slot",
            "start_datetime", "end_datetime",
        }
        if (
            {"name", "approval_status"} & vals.keys()
            and not self.env.user.has_group("booking.group_booking_admin")
        ):
            raise AccessError(_("Only Booking Admins can change the name or approval status."))
        if not slot_fields.intersection(vals):
            return super().write(vals)
        for booking in self:
            resource = self.env["resource.resource"].browse(
                vals.get("resource_id", booking.resource_id.id)
            )
            date = vals.get("booking_date", booking.booking_date)
            slot = vals.get("time_slot", booking.time_slot)
            if not date or not slot:
                raise ValidationError(
                    _("Choose a date and time slot before changing the resource.")
                )
            start, end = self._slot_datetimes(resource, date, slot)

            super(Booking, booking).write({
                **vals,
                "name": booking._format_booking_name(resource, start, end),
                "start_datetime": start,
                "end_datetime": end,
            })
        return True

    @api.constrains(
        "start_datetime",
        "end_datetime"
        )
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
                months=resource.max_advance_months
            ):
                raise ValidationError(_("The booking is too far in advance."))

            if start.hour + start.minute / 60 > resource.latest_start_hour:
                raise ValidationError(_("The booking cannot start after 8 PM."))

            if end.hour + end.minute / 60 > resource.latest_end_hour:
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
            overlap = self.search_count(
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

    def _check_booking_rights(self):
        is_admin = self.env.user.has_group("booking.group_booking_admin")
        for booking in self:
            booking.booking_rights = is_admin
