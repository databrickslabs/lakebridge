"""Masking changes only the values each guided option targets, in every real JSON fixture.

For each option on its own, and for all options together, the generated rules are applied to every fixture. The masked
JSON must parse, keep its structure, and change only leaves that the option targets. A leaf the option targets must not
be left unchanged, so a rule that silently does nothing fails too.

The domain option needs a host name under a domain, which the fixtures lack, so for those options "localhost" is
replaced by DOMAIN_HOST first. Every other fixture value is left as it is.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from databricks.labs.lakebridge.resources.assessments.hadoop.masking import (
    USER_KEYS,
    apply_masking_rules,
    guided_rules_text,
    parse_masking_rules,
)

_FIXTURES = Path(__file__).resolve().parents[2] / "resources" / "assessments" / "hadoop"
DOMAIN = "corp.example.com"
DOMAIN_HOST = "rm1.corp.example.com"
OPTIONS = ("domains", "ips", "users", "queries", "all")

_USER_KEYS = set(USER_KEYS)
_QUERY_KEYS = {"statement", "stmt", "diagnostics"}
_IPV4 = re.compile(r"(?<![0-9A-Za-z._-])[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}(?![0-9A-Za-z_-]|\.[0-9A-Za-z_])")


@dataclass(frozen=True)
class _Leaf:
    path: tuple
    parent: Any
    original: Any
    masked: Any


def _walk(original: Any, masked: Any, path: tuple, parent: Any, problems: list[str], leaves: list[_Leaf]) -> None:
    """Walk both trees together. Structure that changed is a problem; every scalar leaf is collected."""
    if isinstance(original, dict):
        if not isinstance(masked, dict) or list(masked) != list(original):
            problems.append(f"{path}: object keys or their order changed")
            return
        for key, value in original.items():
            _walk(value, masked[key], path + (key,), original, problems, leaves)
    elif isinstance(original, list):
        if not isinstance(masked, list) or len(masked) != len(original):
            problems.append(f"{path}: list length changed")
            return
        for index, (value, changed) in enumerate(zip(original, masked)):
            _walk(value, changed, path + (index,), original, problems, leaves)
    else:
        leaves.append(_Leaf(path, parent, original, masked))


def _targets(option: str, leaf: _Leaf) -> set[str]:
    """The option classes this leaf belongs to. Empty means the option has no business with the leaf."""
    if not isinstance(leaf.original, str):
        return set()
    key = leaf.path[-1]
    text = leaf.original
    classes: set[str] = set()
    if option in {"users", "all"}:
        in_user_list = isinstance(key, int) and ("users" in leaf.path or "userList" in leaf.path)
        # A Ranger v1 service "config" is an escaped JSON string that can carry user keys.
        has_embedded_user = key == "config" and any(f'"{user_key}":' in text for user_key in _USER_KEYS)
        # A Ranger audit filter is a single-quoted string that can list users: {'users':['hive'],...}.
        has_audit_filter_users = re.search(r"'users'\s*:\s*\[", text) is not None
        if key in _USER_KEYS or in_user_list or has_embedded_user or has_audit_filter_users:
            classes.add("users")
    if option in {"queries", "all"}:
        is_application_name = key == "name" and str(leaf.parent.get("id", "")).startswith("application_")
        if key in _QUERY_KEYS or is_application_name:
            classes.add("queries")
    # A version under a *version* key keeps its value even when it has the shape of an address (2.7.0.0).
    is_version = isinstance(key, str) and "version" in key.lower()
    if option in {"ips", "all"} and _IPV4.search(text) and not is_version:
        classes.add("ips")
    if option in {"domains", "all"} and DOMAIN in text:
        classes.add("domains")
    return classes


def _rules_for(option: str) -> tuple:
    domains = [DOMAIN] if option in {"domains", "all"} else []
    return parse_masking_rules(
        guided_rules_text(
            domains,
            redact_users=option in {"users", "all"},
            redact_queries=option in {"queries", "all"},
            mask_ips=option in {"ips", "all"},
        ),
        option,
    )


def _check_fixture(name: str, option: str) -> dict[str, list[str]]:
    """Apply one option's rules to one fixture and return the changed paths per class. Fails on any out-of-scope change."""
    text = (_FIXTURES / name).read_text(encoding="utf-8")
    if option in {"domains", "all"}:
        text = text.replace("localhost", DOMAIN_HOST)
    original = json.loads(text)
    masked_text = apply_masking_rules(_rules_for(option), text)
    masked = json.loads(masked_text)  # the masked response must still be JSON

    problems: list[str] = []
    leaves: list[_Leaf] = []
    _walk(original, masked, (), None, problems, leaves)
    assert not problems, f"{name} [{option}]: {problems}"

    changed_by_class: dict[str, list[str]] = {}
    for leaf in leaves:
        classes = _targets(option, leaf)
        changed = leaf.original != leaf.masked
        if changed:
            assert (
                classes
            ), f"{name} [{option}]: out-of-scope change at {leaf.path}: {leaf.original!r} -> {leaf.masked!r}"
            for cls in classes:
                changed_by_class.setdefault(cls, []).append(_show(leaf.path))
        elif classes and leaf.original:
            pytest.fail(f"{name} [{option}]: targeted value left unchanged at {leaf.path}: {leaf.original!r}")
    return changed_by_class


def _show(path: tuple) -> str:
    return ".".join(str(part) for part in path)


@pytest.mark.parametrize("option", OPTIONS)
@pytest.mark.parametrize("name", sorted(p.name for p in _FIXTURES.glob("*.json")))
def test_each_option_changes_only_its_targets_in_every_fixture(name: str, option: str) -> None:
    _check_fixture(name, option)


def test_every_option_has_targets_somewhere_in_the_fixtures() -> None:
    """The scope check is not vacuous: each option finds something to change across the fixtures."""
    for option in ("domains", "ips", "users", "queries"):
        total = 0
        for path in _FIXTURES.glob("*.json"):
            total += sum(len(paths) for paths in _check_fixture(path.name, option).values())
        assert total > 0, option


# --- Negative cases: values that look like addresses or identifiers but are not, and must not change ---


@pytest.mark.parametrize(
    "value",
    [
        "1.2.3.4.5",  # five dotted numbers: not an address
        "10.0.0",  # three numbers
        "1.2.3.4.5.6.7.8",  # a long dotted string
        "application_1795504571115_0004",  # an application id
        "3.5.10",  # a Spark version
        "2.6.0-cdh5.7.0",  # a CDH version
        "5.1.3000.0.2609040041",  # a build number
        "2.6.1.0-129",  # an HDP build
        "1.8.0_292",  # a JVM version
        "v1.2.3.4.5",  # a prefixed version
    ],
)
def test_version_and_identifier_strings_are_not_addresses(value: str) -> None:
    text = json.dumps({"version": value, "build": value})
    masked = apply_masking_rules(_rules_for("ips"), text)

    assert masked == text


def test_port_numbers_and_numeric_fields_are_not_changed() -> None:
    text = '{"port": 8042, "progress": 0.0, "elapsed": 12.5, "ratio": 1.2, "address": "10.0.0.5:8042"}'

    masked = apply_masking_rules(_rules_for("ips"), text)

    assert json.loads(masked) == {
        "port": 8042,
        "progress": 0.0,
        "elapsed": 12.5,
        "ratio": 1.2,
        "address": "x.x.0.5:8042",
    }


def test_an_out_of_range_octet_is_masked_as_an_address_by_shape() -> None:
    """The rule checks the shape of an address, not its range: 999.1.1.1 looks like an address and is masked."""
    assert apply_masking_rules(_rules_for("ips"), '"999.1.1.1"') == '"x.x.1.1"'


def test_the_ranger_policy_resources_and_conditions_are_untouched_by_the_user_option() -> None:
    text = _fixture_text("ranger_policies_v2.json")
    original = json.loads(text)
    masked = json.loads(apply_masking_rules(_rules_for("users"), text))
    policies = list(zip(original, masked)) if isinstance(original, list) else [(original, masked)]
    assert policies

    for before, after in policies:
        for key in ("name", "resources", "conditions", "policyType", "isEnabled"):
            if key in before:
                assert before[key] == after[key], key


def _fixture_text(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")
