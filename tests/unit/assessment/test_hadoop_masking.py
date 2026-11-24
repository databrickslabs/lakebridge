import json
import re
from pathlib import Path

import pytest

from databricks.labs.lakebridge.resources.assessments.hadoop.masking import (
    USER_KEYS,
    MaskingRulesError,
    apply_masking_rules,
    default_rules_file,
    guided_rules_text,
    load_masking_rules,
    parse_masking_rules,
    write_rules_file,
)

_FIXTURES = Path(__file__).resolve().parents[2] / "resources" / "assessments" / "hadoop"
# The file a customer uses in practice: a JSON-aware user redaction. It tolerates the spacing of YARN, Spark and CM JSON.
_USER_RULE = r's/"\(user\|sparkUser\)" *: *"[^"]*"/"\1":"redacted"/g'
_HELP = "each line must look like s/<text to find>/<replacement>/g, for example s/corp\\.example\\.com/masked.example/g"


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def _apply(rules_text: str, text: str) -> str:
    return apply_masking_rules(parse_masking_rules(rules_text), text)


# --- The original sed.txt, verbatim and with its commented example lines uncommented ---

# The example sed.txt shipped with the original Hadoop profiler (Profiler/sed.txt), verbatim.
_ORIGINAL_SED_TXT = """\
# example sed command file, replace original_text and new_text with relevant values
# you can add more sed commands in this file to do further output modifications
s/original_text/new_text/g
s/internal.cloudapp/rginternal.cloud/g
# Uncommenting the filters belong may impact the analysis of the Impala workloads.
#s/"connected_user" : "[a-zA-Z0-9/@._\\-]*"/"connected_user" : "redacted"/g
#s/"original_user" : "[a-zA-Z0-9/@._\\-]*"/"original_user" : "redacted"/g
#s/"user" : "[a-zA-Z0-9/@._\\-]*"/"user" : "redacted"/g
"""
# The same file with its commented example rules switched on, as a user would do.
_ORIGINAL_SED_TXT_UNCOMMENTED = "\n".join(
    line[1:] if line.startswith("#s/") else line for line in _ORIGINAL_SED_TXT.split("\n")
)


def test_original_sed_file_has_its_two_active_rules() -> None:
    rules = parse_masking_rules(_ORIGINAL_SED_TXT, "sed.txt")

    assert len(rules) == 2
    assert _apply(_ORIGINAL_SED_TXT, "host.internal.cloudapp.net original_text") == (
        "host.rginternal.cloud.net new_text"
    )


@pytest.mark.parametrize(
    "name", ["yarn_applications_vanilla.json", "spark_applications_vanilla.json", "impala_queries.json"]
)
def test_original_sed_file_leaves_the_fixtures_alone(name: str) -> None:
    """Its active rules match nothing in these responses, and the commented ones are inert until uncommented."""
    text = _fixture(name)

    assert _apply(_ORIGINAL_SED_TXT, text) == text


def test_uncommented_original_redacts_the_cm_impala_users_in_their_spaced_form() -> None:
    rules_text = _ORIGINAL_SED_TXT_UNCOMMENTED
    assert len(parse_masking_rules(rules_text, "sed.txt")) == 5
    text = _fixture("cm_impala_queries.json")

    masked = _apply(rules_text, text)

    for key in ("connected_user", "original_user", "user"):
        assert not re.search(rf'"{key}" : "root"', masked)
    assert masked.count('"user" : "redacted"') == text.count('"user" : "root"') > 0
    assert masked.count('"connected_user" : "redacted"') == text.count('"connected_user" : "root"') > 0


def test_uncommented_original_does_not_match_yarn_compact_json() -> None:
    """The original's patterns need the spaces CM writes around the colon, so YARN's "user":"root" passes through."""
    text = _fixture("yarn_applications_vanilla.json")

    assert _apply(_ORIGINAL_SED_TXT_UNCOMMENTED, text) == text


# --- The user redaction rule against real YARN, Spark and CM JSON shapes ---


@pytest.mark.parametrize(
    ("name", "key_pattern"),
    [
        ("yarn_applications_vanilla.json", r'"user":"[^"]*"'),
        ("spark_applications_vanilla.json", r'"sparkUser" : "[^"]*"'),
        ("cm_impala_queries.json", r'"user" : "[^"]*"'),
    ],
)
def test_user_redaction_rule_masks_every_user_value_in_each_service_shape(name: str, key_pattern: str) -> None:
    text = _fixture(name)
    values = re.findall(key_pattern, text)
    assert values, f"{name} has no user values to mask"

    masked = _apply(_USER_RULE, text)

    assert not re.search(r'"(user|sparkUser)"\s*:\s*"root"', masked)
    assert masked.count('"redacted"') == len(values)


def test_user_redaction_rule_keeps_the_json_valid() -> None:
    spark = _apply(_USER_RULE, _fixture("spark_applications_vanilla.json"))
    yarn = _apply(_USER_RULE, _fixture("yarn_applications_vanilla.json"))

    assert json.loads(spark)[0]["attempts"][0]["sparkUser"] == "redacted"
    assert json.loads(yarn)["apps"]["app"][0]["user"] == "redacted"


def test_without_g_only_the_first_match_in_each_line_is_replaced() -> None:
    """Like sed: without g a rule changes the first match of every line, with g every match of every line."""
    rule_without_g = "s/a/X/"

    assert _apply(rule_without_g, "a a\na a") == "X a\nX a"
    assert _apply("s/a/X/g", "a a\na a") == "X X\nX X"


def test_rules_work_line_by_line_like_sed() -> None:
    """A rule never sees past the end of a line, and ^ and $ anchor to each line, exactly as sed -f does."""
    assert _apply("s/^/>/", "one\ntwo") == ">one\n>two"
    assert _apply("s/$/;/", "one\ntwo") == "one;\ntwo;"
    assert _apply("s/one.two/X/", "one\ntwo") == "one\ntwo"


def test_spark_application_names_are_redacted_in_the_pretty_printed_history_json() -> None:
    rules = parse_masking_rules(guided_rules_text([], redact_users=False, redact_queries=True))

    masked = apply_masking_rules(rules, _fixture("spark_applications_vanilla.json"))

    names = {app["name"] for app in json.loads(masked)}
    assert names == {"redacted"}


# --- Syntax ---


@pytest.mark.parametrize(
    ("rule", "text", "expected"),
    [
        ("s|a|b|g", "a-a", "b-b"),
        ("s#a#b#g", "a-a", "b-b"),
        ("s,a,b,g", "a-a", "b-b"),
        ("s:a:b:g", "a-a", "b-b"),
        ("s/a\\/b/X/g", "a/b a", "X a"),
        ("s|a\\|b|X|g", "a|b a", "X a"),
        ("s|a\\|b|X|g", "a|b", "X"),
        ("s/a/b/", "aaa", "baa"),
        ("s/a/b/g", "aaa", "bbb"),
        ("s/hello/x/Ig", "Hello HELLO", "x x"),
        ("s/hello/x/I", "Hello HELLO", "x HELLO"),
        ("s/\\(ab\\)\\{2\\}/[\\1]/g", "ababab abab", "[ab]ab [ab]"),
        ("s/\\(a\\)\\(b\\)/\\2\\1/g", "ab ba", "ba ba"),
        ("s/f(x)+/Y/g", "f(x)+ fx", "Y fx"),
        ("s/cat\\|dog/pet/g", "cat dog bird", "pet pet bird"),
        ("s/x/[&]/g", "x", "[x]"),
        ("s/x/\\&/g", "x", "&"),
        ("s/a\\.b/Q/g", "a.b axb", "Q axb"),
        ("s/a.c/X/g", "abc a.c", "X X"),
        ("s/^root/R/", "root root", "R root"),
        ("s/a$/Z/", "aa", "aZ"),
        ("s/a$b/Q/g", "a$b", "Q"),
        ("s/*x/Y/g", "*x", "Y"),
        ("s/\\<cat\\>/dog/g", "cat concat cat", "dog concat dog"),
        ("s/a\\nb/X/", "a\nb", "a\nb"),  # sed works line by line, so a newline never matches
        ("s/a/\\n/", "xay", "x\ny"),
        ("s/[[:digit:]]\\+/#/g", "a12b3", "a#b#"),
        ("s/[a-z\\-]\\+/_/g", "ab-cd!", "_!"),
        ("s/[a-zA-Z0-9/@._\\-]\\+/_/g", "user-1@x.y/z!", "_!"),
        ("s/[/]/_/g", "a/b", "a_b"),
        ("s/[]x]/_/g", "a]x", "a__"),
        ("s/[^a-z]/_/g", "aB1", "a__"),
        ("s/a**/x/", "aaa", "x"),
    ],
)
def test_substitution_syntax(rule: str, text: str, expected: str) -> None:
    assert _apply(rule, text) == expected


def test_comments_blank_lines_and_crlf_endings_are_skipped() -> None:
    rules = parse_masking_rules("# a comment\n\n   # indented comment\n  s/a/b/g\r\n\ts/c/d/\r\n", "f.sed")

    assert len(rules) == 2
    assert apply_masking_rules(rules, "ac") == "bd"


def test_a_utf8_byte_order_mark_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "rules.sed"
    path.write_bytes(b"\xef\xbb\xbfs/a/b/g\n")

    assert apply_masking_rules(load_masking_rules(path), "aa") == "bb"


def test_an_empty_file_masks_nothing(tmp_path: Path) -> None:
    path = tmp_path / "rules.sed"
    path.write_text("# only comments\n", encoding="utf-8")

    assert not load_masking_rules(path)


# --- Errors name the file, the line and the expected form ---


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        ("hello", "not a substitution; it must start with s"),
        ("s", "not a substitution; it must start with s"),
        ("s1a1b1", "the character after s must be the delimiter"),
        ("s\\a\\b\\", "the character after s must be the delimiter"),
        ("s/a", "the text to find has no closing /"),
        ("s/a/b", "the replacement has no closing /"),
        ("s//x/", "the text to find is empty"),
        ("s/a/b/p", "unsupported flag 'p'"),
        ("s/a/b/gg", "unsupported flag 'g'"),
        ("s/a/b/2", "unsupported flag '2'"),
        ("s/\\(a/b/", "\\( has no matching \\)"),
        ("s/a\\)/b/", "\\) has no matching \\("),
        ("s/\\(a\\1\\)/x/", "refers to a group the text to find does not define yet"),
        ("s/a/\\2/", "\\2 refers to a group the text to find does not define"),
        ("s/a\\{x\\}/b/", "\\{x\\} is not a valid interval"),
        ("s/a\\{2/b/", "\\{ has no matching \\}"),
        ("s/\\{2\\}/b/", "\\{ follows nothing to repeat"),
        ("s/a\\q/x/", "\\q is not supported"),
        ("s/[a/b/", "the bracket expression [ ... ] is not closed"),
        ("s/[[:foo:]]/x/", "[:foo:] is not a supported character class"),
        ("s/[[.a.]]/x/", "collating elements"),
        ("s/a\\+\\+/x/", "a repeat operator follows another repeat operator"),
    ],
)
def test_an_unsupported_line_is_reported_with_file_line_and_form(line: str, reason: str) -> None:
    text = f"# header\n\ns/ok/fine/g\n{line}\n"

    with pytest.raises(MaskingRulesError) as caught:
        parse_masking_rules(text, "/etc/lakebridge/mask.sed")

    message = str(caught.value)
    assert message.startswith("/etc/lakebridge/mask.sed, line 4: ")
    assert reason in message
    assert f'("{line}")' in message
    assert message.endswith(_HELP)


def test_the_error_names_the_first_bad_line_only() -> None:
    with pytest.raises(MaskingRulesError, match=r"f\.sed, line 2: not a substitution"):
        parse_masking_rules("s/a/b/g\nnope\ns/c\n", "f.sed")


def test_a_missing_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(MaskingRulesError, match="masking rules file not found: .*missing.sed"):
        load_masking_rules(tmp_path / "missing.sed")


def test_a_directory_is_reported_as_unreadable(tmp_path: Path) -> None:
    with pytest.raises(MaskingRulesError, match="cannot be read"):
        load_masking_rules(tmp_path)


def test_a_file_that_is_not_utf8_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "rules.sed"
    path.write_bytes(b"s/caf\xe9/cafe/g\n")

    with pytest.raises(MaskingRulesError, match="is not UTF-8 text"):
        load_masking_rules(path)


def test_a_leading_tilde_is_expanded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "rules.sed").write_text("s/a/b/g\n", encoding="utf-8")

    assert len(load_masking_rules("~/rules.sed")) == 1


# --- The rules file the guided configuration writes ---

_USER_KEYS = set(USER_KEYS)
_QUERY_KEYS = {"statement", "stmt", "diagnostics"}


def _guided(domains: tuple[str, ...] = (), *, users: bool = True, queries: bool = True) -> tuple:
    return parse_masking_rules(guided_rules_text(domains, redact_users=users, redact_queries=queries), "guided")


def _is_targeted_value(key: object, value: object, is_application: bool) -> bool:
    if not (isinstance(value, str) and value):
        return False
    return key in _USER_KEYS or key in _QUERY_KEYS or (key == "name" and is_application)


def _is_user_list(key: object, value: object) -> bool:
    """Ranger v2 "users" and the legacy v1 "userList"."""
    return (
        key in {"users", "userList"}
        and isinstance(value, list)
        and bool(value)
        and all(isinstance(v, str) for v in value)
    )


_AUDIT_FILTER_USERS = re.compile(r"'users'\s*:\s*\[[^\]]*\]")


def _audit_users_redacted(value: object) -> object:
    """A Ranger audit filter string with its single-quoted user lists redacted; any other value unchanged."""
    return _AUDIT_FILTER_USERS.sub("'users':['redacted']", value) if isinstance(value, str) else value


def _check_audit_filter_users(value: str, changed: object, path: str, problems: list[str]) -> None:
    """Only the single-quoted user lists of a Ranger audit filter may change, each to ['redacted']."""
    if changed != _audit_users_redacted(value):
        problems.append(f"{path}: audit filter users not redacted, or other text changed")


def _embedded_users(key: object, value: object) -> dict | None:
    """A Ranger v1 service "config": an escaped JSON string. Returns it parsed when it carries user keys."""
    if key != "config" or not isinstance(value, str):
        return None
    try:
        inner = json.loads(value)
    except ValueError:
        return None
    return inner if isinstance(inner, dict) and _USER_KEYS & inner.keys() else None


def _check_embedded_users(inner: dict, changed: object, path: str, problems: list[str]) -> None:
    """Only the user keys of an embedded JSON string may change, each to "redacted"."""
    expected = {k: ("redacted" if k in _USER_KEYS else _audit_users_redacted(v)) for k, v in inner.items()}
    if not isinstance(changed, str) or json.loads(changed) != expected:
        problems.append(f"{path}: embedded user keys not redacted, or other keys changed")


def _check_member(
    key: str, value: object, changed: object, path: str, is_application: bool, problems: list[str]
) -> None:
    inner = _embedded_users(key, value)
    if inner is not None:
        _check_embedded_users(inner, changed, path, problems)
    elif _is_targeted_value(key, value, is_application) and changed != "redacted":
        problems.append(f"{path}: not redacted")
    elif _is_user_list(key, value) and changed != ["redacted"]:
        problems.append(f"{path}: users not redacted")
    elif isinstance(value, str) and _AUDIT_FILTER_USERS.search(value):
        _check_audit_filter_users(value, changed, path, problems)
    elif not _is_targeted_value(key, value, is_application) and not _is_user_list(key, value):
        _targets_are_redacted(value, changed, path, problems)


def _targets_are_redacted(original: object, masked: object, where: str, problems: list[str]) -> None:
    """Walk the original and masked JSON together: a targeted value must change, anything else must stay the same."""
    if isinstance(original, dict):
        if not isinstance(masked, dict) or masked.keys() != original.keys():
            problems.append(f"{where}: keys changed")
            return
        # An object whose "name" is targeted: an application.
        is_application = str(original.get("id", "")).startswith("application_")
        for key, value in original.items():
            _check_member(key, value, masked[key], f"{where}.{key}", is_application, problems)
    elif isinstance(original, list):
        if not isinstance(masked, list) or len(masked) != len(original):
            problems.append(f"{where}: list length changed")
            return
        for index, (before, after) in enumerate(zip(original, masked)):
            _targets_are_redacted(before, after, f"{where}[{index}]", problems)
    elif original != masked:
        problems.append(f"{where}: changed from {original!r} to {masked!r}")


@pytest.mark.parametrize("name", sorted(p.name for p in _FIXTURES.glob("*.json")))
def test_guided_rules_change_only_the_targeted_values_in_every_json_fixture(name: str) -> None:
    text = _fixture(name)
    try:
        original = json.loads(text)
    except ValueError:
        pytest.skip(f"{name} is not JSON")

    masked_text = _apply(guided_rules_text((), redact_users=True, redact_queries=True), text)

    problems: list[str] = []
    _targets_are_redacted(original, json.loads(masked_text), name, problems)
    assert not problems


def test_guided_rules_keep_escaped_quotes_inside_a_redacted_value() -> None:
    doc = {"queries": [{"stmt": 'select "a\\"b" from t', "stmt_type": "QUERY", "effective_user": "alice"}]}

    masked = json.loads(apply_masking_rules(_guided(queries=True, users=True), json.dumps(doc)))

    assert masked == {"queries": [{"stmt": "redacted", "stmt_type": "QUERY", "effective_user": "redacted"}]}


def test_guided_rules_leave_empty_values_alone() -> None:
    text = '{"diagnostics": "", "user": ""}'

    assert _apply(guided_rules_text((), redact_users=True, redact_queries=True), text) == text


def test_guided_rules_with_no_answers_mask_nothing() -> None:
    text = _fixture("yarn_applications_vanilla.json")

    assert not _guided((), users=False, queries=False)
    assert _apply(guided_rules_text((), redact_users=False, redact_queries=False), text) == text


def test_guided_domain_rules_give_each_host_its_domains_number() -> None:
    rules = _guided(("corp.example.com", "eu.corp.example.com"), users=False, queries=False)
    text = "rm1.corp.example.com rm1.eu.corp.example.com corp.example.com"

    assert apply_masking_rules(rules, text) == "rm1.masked-1 rm1.masked-2 masked-1"


def test_guided_domain_rules_do_not_touch_longer_names() -> None:
    rules = _guided(("corp.example.com",), users=False, queries=False)
    text = "corp.example.community xcorp.example.com"

    assert apply_masking_rules(rules, text) == text


def test_guided_domain_rules_are_applied_to_the_yarn_tracking_url() -> None:
    text = _fixture("yarn_applications_vanilla.json").replace("localhost", "rm1.corp.example.com")

    masked = _apply(guided_rules_text(("corp.example.com",), redact_users=False, redact_queries=False), text)

    assert "rm1.masked-1" in masked
    assert "corp.example.com" not in masked
    json.loads(masked)


def test_guided_rules_file_is_parsed_and_states_what_it_does() -> None:
    text = guided_rules_text(("corp.example.com",), redact_users=True, redact_queries=True)

    assert text.startswith("# Hadoop profiler masking rules.")
    assert "every REST response before anything is saved to the extract, line by line like sed -f" in text
    assert len(parse_masking_rules(text, "guided")) == 2 + 4 + 4  # a domain, four user rules, four query rules


def test_write_rules_file_is_owner_only_and_creates_its_directory(tmp_path: Path) -> None:
    path = tmp_path / "new" / "dir" / "hadoop-masking-rules.txt"

    write_rules_file(path, "s/a/b/g\n", overwrite=False)

    assert path.read_text(encoding="utf-8") == "s/a/b/g\n"
    assert path.stat().st_mode & 0o777 == 0o600


def test_write_rules_file_refuses_to_overwrite_unless_asked(tmp_path: Path) -> None:
    path = tmp_path / "rules.txt"
    path.write_text("s/keep/me/g\n", encoding="utf-8")

    with pytest.raises(FileExistsError):
        write_rules_file(path, "s/x/y/g\n", overwrite=False)
    assert path.read_text(encoding="utf-8") == "s/keep/me/g\n"

    write_rules_file(path, "s/x/y/g\n", overwrite=True)
    assert path.read_text(encoding="utf-8") == "s/x/y/g\n"
    assert path.stat().st_mode & 0o777 == 0o600


def test_default_rules_file_sits_next_to_the_lakebridge_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    assert default_rules_file() == tmp_path / ".databricks" / "labs" / "lakebridge" / "hadoop-masking-rules.txt"


# --- IP addresses: the first two numbers become x.x, and version strings are kept ---

_IP_ONLY = guided_rules_text((), redact_users=False, redact_queries=False, mask_ips=True)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('"10.20.3.7"', '"x.x.3.7"'),
        ("10.0.0.5:8042", "x.x.0.5:8042"),
        ("/10.0.0.5", "/x.x.0.5"),
        ('"http://10.0.0.5:8042/ws"', '"http://x.x.0.5:8042/ws"'),
        ('"10.0.0.1,10.0.0.2"', '"x.x.0.1,x.x.0.2"'),
        ('"10.0.0.1,10.0.0.2,10.0.0.3,10.0.0.4"', '"x.x.0.1,x.x.0.2,x.x.0.3,x.x.0.4"'),
        ('"connect to 10.0.0.5. Then"', '"connect to x.x.0.5. Then"'),
        ("10.0.0.5:8042 is the first", "x.x.0.5:8042 is the first"),
        ('"hn0.example.net", "ip": "172.17.0.2"', '"hn0.example.net", "ip": "x.x.0.2"'),
    ],
)
def test_ip_rules_mask_addresses_in_every_position(text: str, expected: str) -> None:
    assert _apply(_IP_ONLY, text) == expected


@pytest.mark.parametrize(
    "version",
    [
        "3.3.4.5.1.8.42",  # a dotted build string
        "3.3.1.5.1.8.42",
        "2.6.0-cdh5.7.0",  # a CDH version
        "5.1.3000.0.2609040041",  # a long build number
        "2.6.1.0-129",  # an HDP stack version with a build number
        "cdh5.7.0.1",  # a letter before the numbers
        "1110.0.0.5",  # a longer number before what would otherwise be an address
        "3.4.1",
    ],
)
def test_ip_rules_keep_version_strings(version: str) -> None:
    text = f'{{"version": "{version}", "build": "{version}."}}'

    assert _apply(_IP_ONLY, text) == text


def test_ip_rules_mask_an_address_at_the_very_start_or_end_of_a_response() -> None:
    assert _apply(_IP_ONLY, "10.0.0.5:8042") == "x.x.0.5:8042"
    assert _apply(_IP_ONLY, "/10.0.0.5") == "/x.x.0.5"


@pytest.mark.parametrize(
    "text",
    [
        '{"min_version": "2.5.0.0"}',
        '{"desired_version" : "5.1.8.42"}',
        '{"min_ambari_metrics_hadoop_sink_version":"2.7.0.0"}',
        '{"hdp.version": "3.1.0.0"}',
    ],
)
def test_four_number_versions_under_version_keys_are_kept(text: str) -> None:
    """Versions are not personal data and the assessment needs them, even when they have the shape of an address."""
    assert _apply(_IP_ONLY, text) == text


def test_an_address_next_to_a_version_is_still_masked() -> None:
    text = '{"desired_version": "5.1.8.42", "ip": "10.0.10.12"}'

    assert _apply(_IP_ONLY, text) == '{"desired_version": "5.1.8.42", "ip": "x.x.10.12"}'


def test_ip_rules_are_not_part_of_the_file_unless_asked_for() -> None:
    text = '{"ip": "10.0.0.5"}'

    assert _apply(guided_rules_text((), redact_users=False, redact_queries=False), text) == text


@pytest.mark.parametrize(
    "name",
    [
        "impala_backends.json",
        "impala_subscribers.json",
        "impala_varz.json",
        "cm_hosts.json",
        "cm_impala_queries.json",
        "yarn_applications_vanilla.json",
        "ambari_hosts_sim.json",
        "ambari_host_components_sim.json",
    ],
)
def test_ip_rules_leave_no_address_in_real_fixtures_and_keep_json_valid(name: str) -> None:
    text = _fixture(name)
    # The same boundary as the rules: no letter, digit, underscore, hyphen or dot-then-continuation on either side.
    addresses = re.compile(
        r"(?<![0-9A-Za-z._-])[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}(?![0-9A-Za-z_-]|\.[0-9A-Za-z_])"
    )
    assert addresses.findall(text), f"{name} has no addresses to mask"

    masked = _apply(_IP_ONLY, text)

    assert addresses.findall(masked) == []
    json.loads(masked)
    # Some fixtures already contain "x.x." in their text, so count the new placeholders only.
    assert masked.count("x.x.") - text.count("x.x.") == len(addresses.findall(text))


def test_ip_rules_mask_the_impala_backend_addresses_and_keep_the_ports() -> None:
    masked = _apply(_IP_ONLY, _fixture("impala_backends.json"))

    assert "172.24.0.5" not in masked
    assert "x.x.0.5" in masked


@pytest.mark.parametrize(
    "fixture",
    ["ambari_hosts_hdi.json", "ambari_stack_versions_hdi.json", "ambari_blueprint_hdi.json", "yarn_nodes_hdi.json"],
)
def test_ip_rules_mask_real_hdinsight_addresses_but_keep_versions(fixture: str) -> None:
    """Real HDInsight responses: every IPv4 is masked, every version (e.g. 2.7.0.0, 5.1.8.42) is kept."""
    text = (Path(__file__).resolve().parents[2] / "resources" / "assessments" / "hadoop" / fixture).read_text()
    rules = parse_masking_rules(guided_rules_text([], redact_users=False, redact_queries=False, mask_ips=True))

    masked = apply_masking_rules(rules, text)

    json.loads(masked)
    versions = re.findall(r'"[A-Za-z0-9_.-]*[Vv]ersion[A-Za-z0-9_.-]*"\s*:\s*"[^"]*"', text)
    assert versions == re.findall(r'"[A-Za-z0-9_.-]*[Vv]ersion[A-Za-z0-9_.-]*"\s*:\s*"[^"]*"', masked)
    leftover = [
        ip
        for ip in re.findall(r"(?<![0-9A-Za-z._-])\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?![0-9A-Za-z_-])", masked)
        if not any(ip in v for v in versions)
    ]
    assert not leftover
    assert "@lbver@" not in masked


def test_user_rules_redact_ranger_audit_users() -> None:
    """Ranger records who created and last updated each service and policy (createdBy / updatedBy)."""
    rules = parse_masking_rules(guided_rules_text([], redact_users=True, redact_queries=False))

    masked = apply_masking_rules(rules, '{"createdBy":"Admin","updatedBy" : "etl_svc","name":"sim_hive"}')

    assert json.loads(masked) == {"createdBy": "redacted", "updatedBy": "redacted", "name": "sim_hive"}


def test_user_rules_redact_users_in_ranger_audit_filters_but_nothing_else_in_them() -> None:
    rules = parse_masking_rules(guided_rules_text([], redact_users=True, redact_queries=False))
    filters = (
        "[ {'accessResult': 'DENIED', 'isAudited': true}, {'users':['hive','hue'],'actions':['SHOW_ROLES']},"
        " {'users': [ 'oozie' ],'resources': {'path': {'values': ['/user/oozie']}}} ]"
    )
    v2_body = json.dumps({"configs": {"ranger.plugin.audit.filters": filters}})
    v1_body = json.dumps({"config": json.dumps({"ranger.plugin.audit.filters": filters})})
    expected = (
        "[ {'accessResult': 'DENIED', 'isAudited': true}, {'users':['redacted'],'actions':['SHOW_ROLES']},"
        " {'users':['redacted'],'resources': {'path': {'values': ['/user/oozie']}}} ]"
    )

    assert json.loads(apply_masking_rules(rules, v2_body))["configs"]["ranger.plugin.audit.filters"] == expected
    assert (
        json.loads(json.loads(apply_masking_rules(rules, v1_body))["config"])["ranger.plugin.audit.filters"] == expected
    )


def test_user_rules_redact_impala_delegated_users_and_cm_pool_users_only() -> None:
    """Impala's delegated (proxy) user and Cloudera Manager's per-user YARN pool timeseries."""
    rules = parse_masking_rules(guided_rules_text([], redact_users=True, redact_queries=False))
    impala = {
        "queryId": "q1",
        "attributes": {"delegated_user": "analyst2", "original_user": "etl@EXAMPLE.COM", "num_backends": "4"},
    }
    pool = {
        "metadata": {
            "attributes": {"userName": "analyst1", "poolName": "root.users.analyst1", "schedulerType": "CAPACITY"}
        }
    }

    masked_impala = json.loads(apply_masking_rules(rules, json.dumps(impala)))
    masked_pool = json.loads(apply_masking_rules(rules, json.dumps(pool)))

    assert masked_impala["attributes"] == {
        "delegated_user": "redacted",
        "original_user": "redacted",
        "num_backends": "4",
    }
    assert masked_pool["metadata"]["attributes"] == {
        "userName": "redacted",
        "poolName": "root.users.analyst1",
        "schedulerType": "CAPACITY",
    }
