"""Consent resolution: every combination of recorded choice and tenant mode."""

import pytest

from personalization.consent import (
    DENIED,
    GRANTED,
    OPT_IN,
    OPT_OUT,
    REASON_NO_CONSENT,
    is_eligible,
)


@pytest.mark.parametrize(
    "status,mode,eligible",
    [
        # A recorded choice always wins over the tenant's mode.
        (GRANTED, OPT_IN, True),
        (GRANTED, OPT_OUT, True),
        (DENIED, OPT_IN, False),
        (DENIED, OPT_OUT, False),
        # No recorded choice: the tenant's mode decides.
        (None, OPT_IN, False),
        (None, OPT_OUT, True),
    ],
)
def test_resolution_table(status, mode, eligible):
    assert is_eligible(status, mode).eligible is eligible


def test_a_visitor_who_is_not_eligible_carries_the_default_reason():
    for status, mode in [(DENIED, OPT_OUT), (None, OPT_IN)]:
        decision = is_eligible(status, mode)
        assert decision.eligible is False
        assert decision.reason == REASON_NO_CONSENT == "default_no_consent"


def test_an_eligible_visitor_has_no_reason():
    assert is_eligible(GRANTED, OPT_IN).reason is None
    assert is_eligible(None, OPT_OUT).reason is None


def test_anything_unrecognised_is_treated_as_not_eligible():
    assert not is_eligible("maybe", OPT_OUT).eligible
    assert not is_eligible(None, "something-else").eligible  # unknown mode behaves as opt-in
