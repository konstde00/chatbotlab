"""
Django's migrate, run under a database lock.

Every backend container runs migrate on start (entrypoint.sh), so a deploy to
several Elastic Beanstalk instances applies the same migrations on all of them
at once. MariaDB has no transactional DDL: the instances that lose the race
fail on DDL another one already ran ("Duplicate column name"), and a migration
with several operations can be left half applied. A named lock makes them take
turns. The first instance applies the migrations, the others wait and then
find nothing to apply.
"""

from django.core.management.base import CommandError
from django.core.management.commands.migrate import Command as MigrateCommand
from django.db import connections

LOCK_NAME = "chatbotlab_migrate"
LOCK_TIMEOUT = 600  # seconds


class Command(MigrateCommand):
    def handle(self, *args, **options):
        connection = connections[options["database"]]
        with connection.cursor() as cursor:
            cursor.execute("SELECT GET_LOCK(%s, %s)", [LOCK_NAME, LOCK_TIMEOUT])
            if cursor.fetchone()[0] != 1:
                raise CommandError(
                    f"Timed out after {LOCK_TIMEOUT}s waiting for the {LOCK_NAME} lock"
                )
        try:
            return super().handle(*args, **options)
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT RELEASE_LOCK(%s)", [LOCK_NAME])
