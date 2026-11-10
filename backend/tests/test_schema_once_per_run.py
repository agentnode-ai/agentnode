"""Task #13: the database schema is built once per run, and every test still starts empty.

The `engine` fixture in conftest.py used to drop every table and create the whole schema again
before EACH test, and drop it again after. That is most of the time a backend run spends on a
database. It now builds the schema only when it is not all there -- on the first test of a run, or
after a module with its own fixture dropped it -- and otherwise empties the tables. What must not
change is what a test sees: no row left from the test before and the seeded capability taxonomy
present. (Identities: every primary key in this schema is a UUID, and the one sequence,
support_ticket_number_seq, ran on across tests before this change as well.)
"""

import pytest
from sqlalchemy import func, select

from app.auth.models import User
from app.packages.models import CapabilityTaxonomy
from tests import conftest

pytestmark = pytest.mark.asyncio


async def test_a_first_test_leaves_a_row_behind(session):
    session.add(
        User(
            email="schema-once-a@example.test",
            username="schema_once_a",
            password_hash="x",
        )
    )
    await session.commit()
    assert (
        await session.execute(select(func.count()).select_from(User))
    ).scalar_one() == 1


async def test_b_the_next_test_starts_empty_and_seeded(session):
    users = (await session.execute(select(func.count()).select_from(User))).scalar_one()
    assert users == 0, "a row from the previous test is still there"
    seeded = (
        await session.execute(select(func.count()).select_from(CapabilityTaxonomy))
    ).scalar_one()
    assert seeded == len(conftest.SEED_CAPABILITY_IDS)


async def test_c_the_schema_was_not_rebuilt_between_the_two(session):
    # A run may build it once, or not at all when an earlier local run left it in place; what it
    # must not do is build it for every test.
    assert conftest.SCHEMA_BUILDS_THIS_RUN < conftest.ENGINE_SETUPS_THIS_RUN, (
        "the schema was built for every test (%d builds for %d tests)"
        % (conftest.SCHEMA_BUILDS_THIS_RUN, conftest.ENGINE_SETUPS_THIS_RUN)
    )
