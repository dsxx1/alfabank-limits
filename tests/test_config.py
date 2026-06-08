import copy

import pytest

from main import validate_config

VALID = {
    "holders": ["ИВАНОВ ИВАН ИВАНОВИЧ"],
    "limits": {
        "all_operations": {"day": 100000, "month": 500000},
        "cash_withdrawal": {"day": 50000, "month": 200000},
    },
}


def test_valid_config_passes():
    validate_config(copy.deepcopy(VALID))  # не должно бросать


def test_zero_limit_is_allowed():
    cfg = copy.deepcopy(VALID)
    cfg["limits"]["cash_withdrawal"]["day"] = 0
    validate_config(cfg)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c.pop("holders"),
        lambda c: c.update(holders=[]),
        lambda c: c.update(holders="ИВАНОВ"),
        lambda c: c.update(holders=["", "  "]),
        lambda c: c.pop("limits"),
        lambda c: c.update(limits=[]),
        lambda c: c["limits"].pop("all_operations"),
        lambda c: c["limits"]["all_operations"].pop("day"),
        lambda c: c["limits"]["cash_withdrawal"].update(month=-1),
        lambda c: c["limits"]["all_operations"].update(day="много"),
        lambda c: c["limits"]["all_operations"].update(day=True),
    ],
)
def test_invalid_config_raises(mutate):
    cfg = copy.deepcopy(VALID)
    mutate(cfg)
    with pytest.raises(ValueError):
        validate_config(cfg)


def test_non_dict_root_raises():
    with pytest.raises(ValueError):
        validate_config(["not", "a", "dict"])
