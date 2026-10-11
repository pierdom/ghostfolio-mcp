"""ACCOUNT_PROFILES / INTEREST_RULES / DEFAULT_BENCHMARK environment parsing."""

import pytest

from ghostfolio_mcp.ghostfolio_client import get_ghostfolio_config_from_env
from ghostfolio_mcp.models import AccountProfile
from ghostfolio_mcp.models import InterestRuleConfig
from ghostfolio_mcp.models import Modelo720Profile
from ghostfolio_mcp.models import TaxProfile


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (
        "ACCOUNT_PROFILES",
        "INTEREST_RULES",
        "DEFAULT_BENCHMARK",
        "HOMOGENEOUS_GROUPS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_unset_means_no_profiles_and_no_benchmark():
    config = get_ghostfolio_config_from_env()

    assert config.account_profiles == {}
    assert config.default_benchmark is None


def test_account_profiles_are_parsed_with_defaults(monkeypatch):
    monkeypatch.setenv(
        "ACCOUNT_PROFILES",
        '{"acc-a": {"interest": {"attribution": "previous_month", "expected": "monthly"},'
        ' "modelo720": {"foreign": true}}, "acc-b": {"tax": {"excluded": true}}}',
    )

    profiles = get_ghostfolio_config_from_env().account_profiles

    assert profiles == {
        "acc-a": AccountProfile(
            interest=InterestRuleConfig(
                attribution="previous_month", expected="monthly"
            ),
            modelo720=Modelo720Profile(foreign=True),
        ),
        "acc-b": AccountProfile(tax=TaxProfile(excluded=True)),
    }


def test_interest_rules_still_work_on_their_own(monkeypatch):
    monkeypatch.setenv("INTEREST_RULES", '{"acc-a": {"expected": "daily"}}')

    profiles = get_ghostfolio_config_from_env().account_profiles

    assert profiles["acc-a"].interest == InterestRuleConfig(expected="daily")


def test_interest_rules_fill_only_profiles_without_an_interest_section(monkeypatch):
    monkeypatch.setenv(
        "ACCOUNT_PROFILES",
        '{"acc-a": {"interest": {"expected": "monthly"}},'
        ' "acc-b": {"tax": {"excluded": true}}}',
    )
    monkeypatch.setenv(
        "INTEREST_RULES",
        '{"acc-a": {"expected": "daily"}, "acc-b": {"attribution": "previous_month"}}',
    )

    profiles = get_ghostfolio_config_from_env().account_profiles

    assert profiles["acc-a"].interest.expected == "monthly"
    assert profiles["acc-b"].interest.attribution == "previous_month"
    assert profiles["acc-b"].tax.excluded is True


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_values_mean_none(monkeypatch, value):
    monkeypatch.setenv("ACCOUNT_PROFILES", value)
    monkeypatch.setenv("INTEREST_RULES", value)
    monkeypatch.setenv("DEFAULT_BENCHMARK", value)

    config = get_ghostfolio_config_from_env()

    assert config.account_profiles == {}
    assert config.default_benchmark is None


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("INTEREST_RULES", '{"acc-a": {"attribution": "yearly"}}', "attribution"),
        ("ACCOUNT_PROFILES", '{"acc-a": {"tax": {"excluded": "maybe"}}}', "excluded"),
        ("ACCOUNT_PROFILES", '["acc-a"]', "JSON object"),
    ],
)
def test_invalid_values_are_rejected(monkeypatch, name, value, message):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=message):
        get_ghostfolio_config_from_env()


def test_default_benchmark_is_read(monkeypatch):
    monkeypatch.setenv("DEFAULT_BENCHMARK", " YAHOO:VWCE.DE ")

    assert get_ghostfolio_config_from_env().default_benchmark == "YAHOO:VWCE.DE"


def test_homogeneous_groups_are_read(monkeypatch):
    monkeypatch.setenv(
        "HOMOGENEOUS_GROUPS",
        '[["YAHOO:VWCE.DE", "YAHOO:VWRL.AS"], ["YAHOO:A", "YAHOO:B"]]',
    )

    assert get_ghostfolio_config_from_env().homogeneous_groups == [
        ["YAHOO:VWCE.DE", "YAHOO:VWRL.AS"],
        ["YAHOO:A", "YAHOO:B"],
    ]


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ('[["YAHOO:A"]]', "at least two"),
        ('[["YAHOO:A", "B"]]', "not DATASOURCE:SYMBOL"),
        ('[["YAHOO:A", "YAHOO:B"], ["YAHOO:B", "YAHOO:C"]]', "more than one group"),
    ],
)
def test_invalid_homogeneous_groups_are_rejected(monkeypatch, value, message):
    monkeypatch.setenv("HOMOGENEOUS_GROUPS", value)

    with pytest.raises(ValueError, match=message):
        get_ghostfolio_config_from_env()
