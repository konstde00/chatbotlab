"""
Tests for the migrate override that serialises migrations across instances.

Every instance runs migrate when its container starts, so the command has to
wait while another session holds the migration lock, and only then run.

Coverage:
  - migrate waits for a lock held by another session, then runs and releases it
  - migrate gives up with a CommandError once the lock timeout passes
"""

import threading
import time

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection

from chatbot.management.commands import migrate


@pytest.fixture
def other_session(db):
    """A second database session, standing in for another instance."""
    session = connection.get_new_connection(connection.get_connection_params())
    yield session
    session.close()


def run(session, sql):
    cursor = session.cursor()
    cursor.execute(sql, [migrate.LOCK_NAME])
    return cursor.fetchone()[0]


def test_migrate_waits_for_the_lock(other_session):
    assert run(other_session, "SELECT GET_LOCK(%s, 0)") == 1
    release = threading.Timer(1, run, [other_session, "SELECT RELEASE_LOCK(%s)"])
    release.start()

    started = time.monotonic()
    call_command("migrate", verbosity=0)

    assert time.monotonic() - started >= 1
    assert run(other_session, "SELECT IS_FREE_LOCK(%s)") == 1


def test_migrate_times_out_while_the_lock_is_held(other_session, monkeypatch):
    monkeypatch.setattr(migrate, "LOCK_TIMEOUT", 1)
    assert run(other_session, "SELECT GET_LOCK(%s, 0)") == 1

    with pytest.raises(CommandError, match="waiting for the chatbotlab_migrate lock"):
        call_command("migrate", verbosity=0)
