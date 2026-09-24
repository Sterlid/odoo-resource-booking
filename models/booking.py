"""Booking fields, ownership, creation, updates and approval workflow."""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError


class Booking(models.Model):
    _name = "booking.booking"
    _description = "Resource Booking"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    # Singleton integer ranges provide resource equality with PostgreSQL's
    # built-in GiST operators, without requiring the btree_gist extension.
    # [start, end) permits consecutive bookings sharing an endpoint.
    _sql_constraints = [
        (
            "no_active_overlap",
            """EXCLUDE USING GIST (
                int4range(resource_id, resource_id, '[]') WITH =,
                tsrange(start_datetime, end_datetime, '[)') WITH &&
            ) WHERE (approval_status IN ('pending_approval', 'confirmed'))""",
            "This resource already has a pending or confirmed booking during "
            "this period. Please choose another time or resource.",
        ),
    ]

    name = fields.Char(required=True, default=lambda self: f"{self.env.user.name}'s Booking",)
    additional_notes = fields.Text()
    resource_id = fields.Many2one(
        comodel_name="resource.resource",
        required=True,
        index=True,
        ondelete="cascade",
        domain=[
            ("is_available_for_booking", "=", True),
            ("resource_type", "=", "material"),
        ],
    )
    resource_category = fields.Selection(
        related="resource_id.resource_category",
    )
    booking_date = fields.Date(
        string="Date", help="Booking Admins can book from tomorrow; other users need at least 7 days (168 hours) of notice.",
    )
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
        string="Booking Hours",
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

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [dict(vals) for vals in vals_list]
        for vals in vals_list:
            if not self.env.user.has_group("booking.group_booking_admin"):
                if vals.get("user_id", self.env.uid) != self.env.uid:
                    raise AccessError(_("You can only create bookings for yourself."))
                # Explicitly override defaults supplied through RPC/action context.
                vals["user_id"] = self.env.uid
                if vals.get("approval_status", "pending_approval") != "pending_approval":
                    raise AccessError(_("Only Booking Admins can set approval status."))
                vals["approval_status"] = "pending_approval"
            resource = self.env["resource.resource"].browse(vals.get("resource_id"))
            start, end = self._slot_datetimes(
                resource, vals.get("booking_date"), vals.get("time_slot")
            )
            self._check_minimum_notice(start)
            vals["start_datetime"] = start
            vals["end_datetime"] = end
            vals["name"] = self._format_booking_name(resource, start, end)
            vals["approval_status"] = (
                "confirmed"
                if resource.approval_policy == "auto"
                else "pending_approval"
            )
        with self._protect_booking_overlap():
            return super().create(vals_list)

    def write(self, vals):
        with self._protect_booking_overlap():
            return self._write_booking_values(vals)

    def action_approve(self):
        self.check_access("write")
        if not self.env.user.has_group("booking.group_booking_admin"):
            raise AccessError(_("Only Booking Admins can approve bookings."))
        if any(booking.approval_status != "pending_approval" for booking in self):
            raise ValidationError(_("Only pending bookings can be approved."))
        return self.write({"approval_status": "confirmed"})

    def _write_booking_values(self, vals):
        self.check_access("write")
        is_admin = self.env.su or self.env.user.has_group("booking.group_booking_admin")
        if not is_admin:
            if "user_id" in vals:
                raise AccessError(_("Only Booking Admins can change Booked By."))
            if any(booking.user_id != self.env.user for booking in self):
                raise AccessError(_("You can only change your own bookings."))
        slot_fields = {
            "resource_id", "booking_date", "time_slot",
            "start_datetime", "end_datetime",
        }
        if not is_admin and slot_fields.intersection(vals) and any(
            booking.approval_status == "confirmed" for booking in self
        ):
            raise ValidationError(_(
                "The resource, date and time of a confirmed booking cannot be changed. "
                "Ask a Booking Admin to cancel it, then create a new booking."
            ))
        if "name" in vals and not is_admin:
            raise AccessError(_("Only Booking Admins can change the booking name."))
        if "approval_status" in vals:
            self._check_lifecycle_change(vals["approval_status"])
            transitions = {
                "pending_approval": {"confirmed", "cancelled"},
                "confirmed": {"cancelled", "completed"},
                "cancelled": set(),
                "completed": set(),
            }
            for booking in self:
                if (
                    vals["approval_status"] != booking.approval_status
                    and vals["approval_status"] not in transitions[booking.approval_status]
                ):
                    raise ValidationError(_("This booking status transition is not allowed."))
        # ORM constraints recheck hours, dates and conflicts even when confirmation
        # comes from an import or RPC write instead of the Approve button.
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
            self._check_minimum_notice(start)

            values = dict(vals)
            schedule_changed = (
                resource != booking.resource_id
                or start != booking.start_datetime
                or end != booking.end_datetime
            )
            if schedule_changed and values.get(
                "approval_status", booking.approval_status
            ) in ("pending_approval", "confirmed"):
                # Approval applies to the reservation that was reviewed.
                values["approval_status"] = (
                    "pending_approval"
                    if resource.approval_policy == "manual"
                    else "confirmed"
                )

            super(Booking, booking).write({
                **values,
                "name": booking._format_booking_name(resource, start, end),
                "start_datetime": start,
                "end_datetime": end,
            })
        return True

    @api.depends_context("uid")
    def _check_booking_rights(self):
        is_admin = self.env.user.has_group("booking.group_booking_admin")
        for booking in self:
            booking.booking_rights = is_admin
