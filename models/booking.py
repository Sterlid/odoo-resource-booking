from datetime import timedelta, timezone
from dateutil.relativedelta import relativedelta
from pytz import timezone as get_timezone
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ResourceResource(models.Model):
    #Add booking-specific fields to Odoo's native resource model.

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

    name = fields.Char(required=True)
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
    duration_hours = fields.Float(
        default=1.0,
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
    state = fields.Selection(
        [
            ("requested", "Requested"),
            ("confirmed", "Confirmed"),
            ("cancelled", "Cancelled"),
            ("completed", "Completed"),
        ],
        required=True,
        default="requested",
        index=True,
        tracking=True,
    )

    @api.onchange("start_datetime", "duration_hours", "booking_mode")
    def _onchange_duration(self):
        if (
            self.booking_mode == "room"
            and self.start_datetime
            and self.duration_hours > 0
        ):
            self.end_datetime = (
                self.start_datetime + timedelta(hours=self.duration_hours)
            )

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
        "state",
    )
    def _check_resource_availability(self):
       #Require active bookings to fit in one available work interval.

        #Odoo stores Datetime values as naive UTC values.  The resource API
        #requires timezone-aware datetimes and converts them to the resource
        #calendar's timezone internally.  ``compute_leaves=True`` subtracts
        #both calendar closures and resource-specific leave intervals.

        active_bookings = self.filtered(
            lambda booking: (
                booking.resource_id
                and booking.start_datetime
                and booking.end_datetime
                and booking.start_datetime < booking.end_datetime
                and booking.state in ("requested", "confirmed")
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
    "duration_hours",
    "state",
    )
    def _check_booking_policy(self):
        now = fields.Datetime.now().replace(tzinfo=timezone.utc)
        for booking in self.filtered(
            lambda b: b.state in ("requested", "confirmed")
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

            if booking.booking_mode == "room" and booking.duration_hours <= 0:
                raise ValidationError(_("Duration must be greater than zero."))
