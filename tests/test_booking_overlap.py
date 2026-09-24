from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from time import monotonic, sleep
from unittest.mock import patch
from uuid import uuid4

import psycopg2
from psycopg2 import sql
from psycopg2.errors import ExclusionViolation

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

from .test_booking_ownership import BookingCase


@tagged("post_install", "-at_install")
class TestBookingOverlap(BookingCase):
    def test_pending_and_confirmed_block_partial_and_exact_overlaps(self):
        for status in ("pending_approval", "confirmed"):
            self.booking.with_user(self.admin).write({"approval_status": status})
            for slot in ("570", "600", "630"):
                with self.assertRaises(ValidationError):
                    self.Booking.with_user(self.other).create({
                        **self.values, "time_slot": slot,
                    })

    def test_adjacent_and_other_resources_allowed(self):
        for slot in ("540", "660"):
            self.Booking.with_user(self.other).create({**self.values, "time_slot": slot})
        other_resource = self.resource.copy({"name": "Other test room"})
        self.Booking.with_user(self.other).create({
            **self.values, "resource_id": other_resource.id,
        })

    def test_inactive_bookings_release_slot_and_cannot_be_reactivated(self):
        for state in ("cancelled", "completed"):
            with self.cr.savepoint():
                booking = self.Booking.with_user(self.owner).create({
                    **self.values, "time_slot": "720",
                })
                if state == "completed":
                    booking.with_user(self.admin).action_approve()
                    with patch.object(
                        fields.Datetime, "now",
                        return_value=booking.end_datetime + timedelta(seconds=1),
                    ):
                        booking.with_user(self.admin).write({"approval_status": state})
                else:
                    booking.with_user(self.admin).write({"approval_status": state})
                replacement = self.Booking.with_user(self.other).create({
                    **self.values, "time_slot": "720",
                })
                for active_state in ("pending_approval", "confirmed"):
                    with self.assertRaises(ValidationError):
                        booking.with_user(self.admin).write({
                            "approval_status": active_state,
                        })
                    self.assertEqual(booking.approval_status, state)
                replacement.with_user(self.admin).unlink()

    def test_failed_reschedule_rolls_back(self):
        booking = self.Booking.with_user(self.other).create({
            **self.values, "time_slot": "720",
        })
        with self.assertRaises(ValidationError):
            booking.write({"time_slot": "630"})
        self.assertEqual(booking.time_slot, "720")

    def test_failed_resource_change_rolls_back(self):
        other_resource = self.resource.copy({"name": "Other test room"})
        booking = self.Booking.with_user(self.other).create({
            **self.values, "resource_id": other_resource.id,
        })
        with self.assertRaises(ValidationError):
            booking.write({"resource_id": self.resource.id})
        self.assertEqual(booking.resource_id, other_resource)

    def test_conflicting_bulk_create_is_atomic(self):
        model = self.Booking.with_user(self.other)
        before = model.search_count([])
        with self.assertRaises(ValidationError):
            model.create([
                {**self.values, "time_slot": "720"},
                {**self.values, "time_slot": "750"},
            ])
        self.assertEqual(model.search_count([]), before)

    def test_auto_approval_is_protected(self):
        self.resource.approval_policy = "auto"
        with self.assertRaises(ValidationError):
            self.Booking.with_user(self.other).create(self.values)
        booking = self.Booking.with_user(self.other).create({
            **self.values, "time_slot": "720",
        })
        self.assertEqual(booking.approval_status, "confirmed")

    def test_constraint_exists_and_blocks_direct_sql(self):
        self.cr.execute("""
            SELECT contype FROM pg_constraint
            WHERE conrelid = 'booking_booking'::regclass
              AND conname = 'booking_booking_no_active_overlap'
        """)
        self.assertEqual(self.cr.fetchone(), ("x",))
        with self.assertRaises(ExclusionViolation), self.cr.savepoint():
            self.cr.execute("""
                INSERT INTO booking_booking
                    (name, resource_id, user_id, start_datetime, end_datetime, approval_status)
                SELECT name, resource_id, user_id, start_datetime, end_datetime, approval_status
                FROM booking_booking WHERE id = %s
            """, [self.booking.id])


@tagged("post_install", "-at_install")
class TestBookingOverlapConcurrency(TransactionCase):
    def _race(self, winner_state, loser_state, commit_winner):
        # Clone the *installed* table/constraint into a disposable table so real
        # commits can be tested without committing any application test data.
        table = "booking_overlap_test_" + uuid4().hex
        dsn = self.cr._cnx.dsn
        control = psycopg2.connect(dsn)
        control.autocommit = True
        first = psycopg2.connect(dsn)
        second = psycopg2.connect(dsn)
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            with control.cursor() as cur:
                cur.execute(sql.SQL("CREATE TABLE {} (LIKE booking_booking INCLUDING ALL)").format(
                    sql.Identifier(table),
                ))
            for conn in (first, second):
                conn.set_session(isolation_level="REPEATABLE READ")
                with conn.cursor() as cur:
                    cur.execute("SET LOCAL statement_timeout = '10s'")
                    # Both requests see an empty table before either inserts.
                    cur.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
                    self.assertEqual(cur.fetchone()[0], 0)
            insert = sql.SQL("""
                INSERT INTO {} (id, name, resource_id, user_id,
                    start_datetime, end_datetime, approval_status)
                VALUES (%s, 'Concurrent booking', 1, 1,
                    '2030-01-01 10:00:00', '2030-01-01 11:00:00', %s)
            """).format(sql.Identifier(table))
            with first.cursor() as cur:
                cur.execute(insert, (1, winner_state))

            def contender():
                try:
                    with second.cursor() as cur:
                        cur.execute(insert, (2, loser_state))
                    second.commit()
                    return "committed"
                except ExclusionViolation:
                    second.rollback()
                    return "conflict"

            future = pool.submit(contender)
            deadline = monotonic() + 5
            waiting = False
            while monotonic() < deadline and not future.done():
                with control.cursor() as cur:
                    cur.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s",
                                [second.get_backend_pid()])
                    row = cur.fetchone()
                if row and row[0] == "Lock":
                    waiting = True
                    break
                sleep(0.02)
            self.assertTrue(waiting, "Concurrent insert did not wait on the uncommitted booking")
            if commit_winner:
                first.commit()
            else:
                first.rollback()
            self.assertEqual(future.result(timeout=10), "conflict" if commit_winner else "committed")
            with control.cursor() as cur:
                cur.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
                self.assertEqual(cur.fetchone()[0], 1)
        finally:
            first.rollback()
            pool.shutdown(wait=True)
            first.close()
            second.close()
            with control.cursor() as cur:
                cur.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(table)))
            control.close()

    def test_pending_winner_blocks_confirmed_request(self):
        self._race("pending_approval", "confirmed", True)

    def test_confirmed_winner_blocks_pending_request(self):
        self._race("confirmed", "pending_approval", True)

    def test_rolled_back_request_does_not_block_waiter(self):
        self._race("pending_approval", "confirmed", False)
