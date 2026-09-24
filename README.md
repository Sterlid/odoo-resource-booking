# Resource Booking & Reservation

An installable Odoo 18 module for internal users to reserve rooms and equipment. It builds on Odoo's `resource` calendars for working hours and `mail` for booking history, approval activities, and reminders.

## What it does

- **Bookings and permissions:** Booking Users can create, view, and edit their own bookings. Booking Admins can view and manage all bookings and configure resources. Users cancel a booking instead of deleting it; deletion is reserved for admins.
- **Availability and conflicts:** Bookings use one-hour slots starting every 30 minutes in the resource's timezone. The slot picker excludes calendar closures, time off, and occupied slots. Server validation checks the full slot again on create or update. A PostgreSQL exclusion constraint prevents simultaneous pending or confirmed bookings from overlapping, including concurrent requests.
- **Approval and lifecycle:** Each resource uses automatic or manual approval. Manual requests start pending; admins approve them. Bookings can be cancelled, and a scheduled job marks confirmed bookings completed after they end. Closed bookings release their slot and are archived after 30 days rather than deleted.
- **Useful follow-through:** Manual requests create an approval activity for Booking Admins. Confirmed bookings receive owner reminders within 24 hours and within one hour of starting. Admin activity catch-up, reminders, and completion run every five minutes. Owners can check in from 15 minutes before to 15 minutes after the start.

## Design decisions and trade-offs

- Bookings are fixed at one hour, with 30-minute start increments, to make resource availability easy to understand. Arbitrary durations and recurring reservations are intentionally out of scope.
- Booking Users need at least 168 hours of notice. Booking Admins can book from the next day in their timezone. This gives staff time to review requests; both rules are enforced on the server.
- Changing a confirmed booking's time or resource is an admin action. A move to a manually approved resource returns it to pending approval because the old approval did not cover the new reservation.
- Pending requests hold a slot to avoid competing approvals. Only pending and confirmed bookings block availability; cancelling or completing one releases it.
- Notifications follow each Odoo user's **Handle in Odoo** or **Handle by Emails** preference. Email delivery requires an outgoing mail server configured in Odoo. Scheduled jobs must be running; reminders can arrive up to five minutes after entering their window. No SMS, push app, or AI feature is included. The exercise requires at least one additional-feature category; Odoo integration and scheduled automation cover two.
- No separate calendar event is created. The resource's existing Odoo calendar supplies working hours and time off; the booking record is the source of truth.

## Install and use

1. Add this module's parent directory to Odoo 18's `addons_path`, then install **Resource Booking & Reservation** (`booking`) from Apps. Restart Odoo and upgrade the module after code changes.
2. Assign internal users to **Booking User** or **Booking Admin** under Settings. Create bookable Resources with a working calendar and choose an **Approval Type**.
3. Users book from **Resource Booking & Reservation → My Bookings**. Admins review **All Bookings → Pending Approval** or their Odoo Activities. Booking lists open by default.
4. To receive in-app reminders, set the user's notification preference to **Handle in Odoo**. For email reminders, configure an outgoing mail server.

## Tests

Run the module tests against a disposable PostgreSQL database:

```bash
./odoo-bin -c /path/to/odoo.conf -d booking_test -i booking \
  --without-demo=all --test-enable --test-tags /booking \
  --stop-after-init --http-port=8079
```

The tests cover ownership and access, approval, calendar availability, overlap and concurrent requests, plus approval notifications and one-time owner reminders. The test database should be discarded after the run.
