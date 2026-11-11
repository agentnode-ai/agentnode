"""The wording check of task #29 itself: it must catch each way of getting the claim wrong."""
from __future__ import annotations

from tests import two_machine_claim as claim

RIGHT = ("Run `remote-worker-cross-host-r3` passed X1-X10 on 2026-10-02 for build df821f65. "
         "The current build has not been accepted by r4.")


def test_the_right_wording_passes():
    assert claim.problems(RIGHT) == []


def test_r3_alone_is_not_enough():
    assert claim.problems(RIGHT.split(". ")[0]) == ["missing: " + claim.CURRENT_NOT_ACCEPTED]


def test_the_current_build_alone_is_not_enough():
    assert claim.problems("The current build has not been accepted by r4.") == [
        "missing: " + claim.R3_PASSED]


def test_another_build_or_date_is_not_r3():
    assert claim.problems(RIGHT.replace("df821f65", "c8c725f5"))
    assert claim.problems(RIGHT.replace("2026-10-02", "2026-09-30"))


def test_each_way_of_carrying_r3_over_is_caught():
    for wrong in claim.FORBIDDEN:
        assert claim.problems(RIGHT + " " + wrong.capitalize() + ".") == ["forbidden: " + wrong]
