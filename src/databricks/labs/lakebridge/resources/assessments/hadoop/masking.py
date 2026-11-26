"""Search/replace masking rules, in the original profiler's ``sed.txt`` form, applied in Python (sed is never run).

The file is read on every run; its rules run in order on every raw REST response before it is parsed or stored. Each line
that is not blank and does not start with ``#`` must be one substitution:

- ``s<d>find<d>replace<d>[flags]``, where ``<d>`` is any single character that is not a letter, digit or backslash
  (``/``, ``|``, ``#``, ``,`` and so on). ``\\<d>`` is a literal delimiter inside ``find`` and ``replace``.
- ``find`` is a POSIX basic regular expression, read as sed reads it: ``\\(`` ``\\)`` ``\\{m,n\\}`` ``\\|`` ``\\+`` and ``\\?``
  are operators, and bare ``( ) { } | + ?`` are literals. ``.``, ``*``, ``^``, ``$``, bracket expressions, ``\\1``-``\\9``,
  ``\\w``, ``\\W``, ``\\s``, ``\\S``, ``\\b``, ``\\B``, ``\\<``, ``\\>``, ``\\n`` and ``\\t`` work as in sed. A delimiter inside a
  bracket expression is literal (``s/[a-z/]//g`` is valid here; GNU sed stops at the ``/``), and ``\\-`` inside brackets
  is a hyphen.
- ``replace`` takes ``&`` for the whole match, ``\\1``-``\\9`` for a group, ``\\&`` for a literal ``&``, and ``\\n`` and
  ``\\t`` for a newline and a tab.
- ``flags``: ``g`` replaces every match on a line, otherwise only the first. ``I`` ignores case. Others are rejected.

Rules apply line by line, as ``sed -f``: ``^`` and ``$`` anchor to the line and no match crosses a line break (``\\n`` in
``find`` never matches), so the output equals the original profiler's ``SEARCH_REPLACE`` output.
"""

import os
import re
import string
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

_HINT = "each line must look like s/<text to find>/<replacement>/g, for example s/corp\\.example\\.com/masked.example/g"

_POSIX_CLASSES: dict[str, str] = {
    "alpha": "a-zA-Z",
    "digit": "0-9",
    "alnum": "a-zA-Z0-9",
    "upper": "A-Z",
    "lower": "a-z",
    "xdigit": "0-9A-Fa-f",
    "space": r" \t\n\r\f\v",
    "blank": r" \t",
    "punct": "".join(re.escape(char) for char in string.punctuation),
}
_SIMPLE_ESCAPES: dict[str, str] = {"n": "\n", "t": "\t"}
# The value of a JSON string, escapes included: "a \\"b" is one value. Written in the rules file's BRE syntax.
_JSON_STRING = r'"\(\\.\|[^"\\]\)\+"'
_SPACE = "[[:space:]]*"
# JSON keys whose values are user names (YARN, Spark History, Impala, CM, Ranger audit fields). Shared with the tests.
USER_KEYS = (
    "user",
    "sparkUser",
    "effective_user",
    "connected_user",
    # Impala's delegated (proxy) user and CM's per-user YARN pool metrics.
    "delegated_user",
    "userName",
    "original_user",
    "username",
    "owner",
    "createdBy",
    "updatedBy",
)
_USER_KEY_ALTERNATION = "\\|".join(USER_KEYS)
_USER_RULES = (
    f's/"\\({_USER_KEY_ALTERNATION}\\)"{_SPACE}:{_SPACE}{_JSON_STRING}/"\\1":"redacted"/g',
    # User lists: Ranger v2 policy items ("users") and v1 permission maps ("userList").
    f's/"\\(users\\|userList\\)"{_SPACE}:{_SPACE}\\[{_SPACE}{_JSON_STRING}\\({_SPACE},{_SPACE}{_JSON_STRING}\\)*{_SPACE}\\]/"\\1":["redacted"]/g',
    # The same keys inside an escaped JSON string, e.g. a Ranger v1 "config":"{\\"username\\":\\"hdfs\\"}".
    f's/\\\\"\\({_USER_KEY_ALTERNATION}\\)\\\\":\\\\"[^\\\\"]*\\\\"/\\\\"\\1\\\\":\\\\"redacted\\\\"/g',
    # Users in Ranger audit filters, a single-quoted string: {'users':['hive','hue'],...}.
    f"s/'users'{_SPACE}:{_SPACE}\\[{_SPACE}'[^']*'\\({_SPACE},{_SPACE}'[^']*'\\)*{_SPACE}\\]/'users':['redacted']/g",
)
# An IPv4 address: four 1-3 digit numbers with no digit, letter, dot, underscore or hyphen on either side, so strings
# like 3.3.4.5.1.8.42 or 2.6.1.0-129 are kept. BRE has no lookarounds, so the rule captures and restores the neighbours;
# the first two numbers become x.x. Listed twice: two addresses one character apart need a second pass.
_IP_RULE = (
    r"s/\([^0-9A-Za-z._-]\)[0-9]\{1,3\}\.[0-9]\{1,3\}\.\([0-9]\{1,3\}\.[0-9]\{1,3\}\)\([^0-9A-Za-z._-]\)/\1x.x.\2\3/g"
)
# An address at the start or end of a line has no neighbour on one side.
_IP_START_RULE = r"s/^[0-9]\{1,3\}\.[0-9]\{1,3\}\.\([0-9]\{1,3\}\.[0-9]\{1,3\}\)\([^0-9A-Za-z._-]\)/x.x.\1\2/"
_IP_END_RULE = r"s/\([^0-9A-Za-z._-]\)[0-9]\{1,3\}\.[0-9]\{1,3\}\.\([0-9]\{1,3\}\.[0-9]\{1,3\}\)$/\1x.x.\2/"
# An address that ends a sentence: a full stop, then a character that cannot continue it.
_IP_SENTENCE_RULE = (
    r"s/\([^0-9A-Za-z._-]\)[0-9]\{1,3\}\.[0-9]\{1,3\}\.\([0-9]\{1,3\}\.[0-9]\{1,3\}\)\(\.[^0-9A-Za-z._-]\)/\1x.x.\2\3/g"
)
# A four-number value under a key containing "version" (e.g. Ambari's "desired_version") looks like an address but is
# needed by the assessment, so its dots are swapped for a marker before the address rules and restored after.
_VERSION_MARK = "@lbver@"
_VERSION_PROTECT_RULE = (
    r's/\("[A-Za-z0-9_.-]*[Vv][Ee][Rr][Ss][Ii][Oo][Nn][A-Za-z0-9_.-]*"[ ]*:[ ]*"\)'
    r"\([0-9]\{1,3\}\)\.\([0-9]\{1,3\}\)\.\([0-9]\{1,3\}\)\.\([0-9]\{1,3\}\)"
    rf'"/\1\2{_VERSION_MARK}\3{_VERSION_MARK}\4{_VERSION_MARK}\5"/g'
)
_VERSION_RESTORE_RULE = rf"s/{_VERSION_MARK}/./g"
_IP_RULES = (
    _VERSION_PROTECT_RULE,
    _IP_START_RULE,
    _IP_RULE,
    _IP_RULE,
    _IP_END_RULE,
    _IP_SENTENCE_RULE,
    _VERSION_RESTORE_RULE,
)
_QUERY_RULES = (
    f's/"\\(statement\\|stmt\\)"{_SPACE}:{_SPACE}{_JSON_STRING}/"\\1":"redacted"/g',
    f's/"diagnostics"{_SPACE}:{_SPACE}{_JSON_STRING}/"diagnostics":"redacted"/g',
    # An application name follows its application_ id on one line (the ResourceManager's single-line JSON).
    f's/\\("id"{_SPACE}:{_SPACE}"application_[^"]*"[^}}]*"name"{_SPACE}:{_SPACE}"\\)\\(\\\\.\\|[^"\\\\]\\)\\+"/\\1redacted"/g',
    # The Spark History Server prints one key per line; only an application's "name" is indented by exactly two spaces.
    f's/^\\(  "name"{_SPACE}:{_SPACE}"\\)\\(\\\\.\\|[^"\\\\]\\)\\+"/\\1redacted"/',
)
_RULES_HEADER = """# Hadoop profiler masking rules. One rule per line: s/<text to find>/<replacement>/g
# The rules run on every REST response before anything is saved to the extract, line by line like sed -f:
# each line goes through the rules in order. With g a rule changes every match in a line; without g only the first.
# Lines starting with # are ignored. Edit, add or remove rules: the profiler reads this file on every run.
"""
# A host name is letters, digits, hyphens and underscores, separated by dots.
DOMAIN_PATTERN = re.compile(r"\.?[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*")


def default_rules_file() -> Path:
    """Where the guided configuration saves the rules file, next to the Lakebridge credentials."""
    return Path.home() / ".databricks" / "labs" / "lakebridge" / "hadoop-masking-rules.txt"


def guided_rules_text(
    domains: Sequence[str],
    *,
    redact_users: bool,
    redact_queries: bool,
    mask_ips: bool = False,
) -> str:
    """The rules file for the guided answers. Each domain gets a numbered placeholder, so host names stay unique."""
    lines = [_RULES_HEADER.rstrip("\n")]
    if domains:
        lines += [
            "",
            "# Host names: each domain becomes a numbered placeholder, so host names stay unique "
            "(rm1.corp.example.com becomes rm1.masked-1).",
        ]
    # Longest first, so eu.corp.example.com is replaced before corp.example.com matches inside it.
    numbered = sorted(enumerate(domains, start=1), key=lambda pair: -len(pair[1]))
    for number, domain in numbered:
        escaped = re.sub(r"([.*\[\]^$\\/])", r"\\\1", domain)
        lines += [
            f"# Domain {number}: {domain} -> masked-{number}",
            f"s/\\.{escaped}\\>/.masked-{number}/g",
            f"s/\\<{escaped}\\>/masked-{number}/g",
        ]
    if mask_ips:
        lines += [
            "",
            "# IP addresses: the first two numbers become x.x, so 10.20.3.7 becomes x.x.3.7. Version strings are kept.",
        ]
        lines += list(_IP_RULES)
    if redact_users:
        lines += ["", '# User names: each value becomes "redacted". The spacing around the colon does not matter.']
        lines += list(_USER_RULES)
    if redact_queries:
        lines += ["", "# Query text and job names: each value becomes redacted. Empty values are left as they are."]
        lines += list(_QUERY_RULES)
    return "\n".join(lines) + "\n"


def write_rules_file(path: Path, text: str, *, overwrite: bool) -> None:
    """Write a rules file only its owner can read, creating its directory if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if overwrite else os.O_EXCL)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(text)
    os.chmod(path, 0o600)


class MaskingRulesError(ValueError):
    """The masking rules file cannot be read, or one of its lines is not a supported substitution."""


class _LineError(Exception):
    """A line that is not a supported substitution; the message is the reason."""


@dataclass(frozen=True)
class SearchReplaceRule:
    """One substitution: the compiled pattern and its replacement as text parts and group numbers."""

    pattern: re.Pattern[str]
    # Literal text, or a group number: 0 is the whole match, as "&" is in sed.
    replacement: tuple[str | int, ...]
    replace_all: bool

    def apply(self, text: str) -> str:
        out: list[str] = []
        position = 0
        previous_end = -1
        for match in self.pattern.finditer(text):
            # sed never matches an empty string right where the previous match ended; Python's re.sub does.
            if match.start() == match.end() == previous_end:
                continue
            out.append(text[position : match.start()])
            out.append(self._expand(match))
            position = previous_end = match.end()
            if not self.replace_all:
                break
        out.append(text[position:])
        return "".join(out)

    def _expand(self, match: re.Match[str]) -> str:
        return "".join((match.group(part) or "") if isinstance(part, int) else part for part in self.replacement)


def apply_masking_rules(rules: tuple[SearchReplaceRule, ...], text: str) -> str:
    """Apply the rules to one raw response line by line, as ``sed -f`` does, so the output matches the original
    profiler's SEARCH_REPLACE pipe."""
    if not rules:
        return text
    lines = text.split("\n")
    for index, line in enumerate(lines):
        for rule in rules:
            line = rule.apply(line)
        lines[index] = line
    return "\n".join(lines)


def load_masking_rules(path: str | Path) -> tuple[SearchReplaceRule, ...]:
    """Read and parse the rules file (a leading ~ is expanded). Raises MaskingRulesError on any read or syntax error."""
    rules_file = Path(path).expanduser()
    try:
        text = rules_file.read_text(encoding="utf-8-sig")
    except FileNotFoundError as e:
        raise MaskingRulesError(f"masking rules file not found: {rules_file}") from e
    except UnicodeDecodeError as e:
        raise MaskingRulesError(f"masking rules file is not UTF-8 text: {rules_file}") from e
    except OSError as e:
        raise MaskingRulesError(f"masking rules file cannot be read: {rules_file} ({e.strerror or e})") from e
    return parse_masking_rules(text, source=str(rules_file))


def parse_masking_rules(text: str, source: str = "masking rules file") -> tuple[SearchReplaceRule, ...]:
    """Parse the text of a rules file. ``source`` names the file in error messages."""
    rules: list[SearchReplaceRule] = []
    for number, raw in enumerate(text.split("\n"), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rules.append(_parse_substitution(line))
        except _LineError as e:
            raise MaskingRulesError(f'{source}, line {number}: {e} ("{line}"); {_HINT}') from None
    return tuple(rules)


def _parse_substitution(line: str) -> SearchReplaceRule:
    if not line.startswith("s") or len(line) < 2:
        raise _LineError("not a substitution; it must start with s")
    delimiter = line[1]
    if delimiter.isalnum() or delimiter == "\\":
        raise _LineError("the character after s must be the delimiter, a punctuation character such as /")
    find, after_find = _split_part(line, 2, delimiter, "text to find", bracket_aware=True)
    if not find:
        raise _LineError("the text to find is empty")
    replacement, after_replacement = _split_part(line, after_find, delimiter, "replacement", bracket_aware=False)
    replace_all, ignore_case = _parse_flags(line[after_replacement:])

    translator = _FindTranslator(find, delimiter)
    pattern_source = translator.translate()
    try:
        pattern = re.compile(pattern_source, re.IGNORECASE if ignore_case else 0)
    except re.error as e:  # pragma: no cover  (the translation always compiles; this is a safety net)
        raise _LineError(f"the text to find is not a valid regular expression ({e})") from e
    return SearchReplaceRule(
        pattern=pattern,
        replacement=_parse_replacement(replacement, translator.group_count),
        replace_all=replace_all,
    )


def _split_part(line: str, start: int, delimiter: str, part: str, *, bracket_aware: bool) -> tuple[str, int]:
    """Read one part up to its closing delimiter. Returns the raw text and the index just after the delimiter."""
    out: list[str] = []
    i = start
    while i < len(line):
        char = line[i]
        if char == "\\":
            out.append(line[i : i + 2])
            i += 2
        elif char == delimiter:
            return "".join(out), i + 1
        elif bracket_aware and char == "[":
            end = _bracket_end(line, i)
            if end is None:
                raise _LineError("the bracket expression [ ... ] is not closed")
            out.append(line[i:end])
            i = end
        else:
            out.append(char)
            i += 1
    raise _LineError(f"the {part} has no closing {delimiter}")


def _bracket_end(text: str, start: int) -> int | None:
    """The index just after the bracket expression that opens at ``start``, or None when it is not closed."""
    i = start + 1
    if text[i : i + 1] == "^":
        i += 1
    if text[i : i + 1] == "]":  # a leading ] is a member of the set, not the end
        i += 1
    while i < len(text):
        char = text[i]
        if char == "\\":
            i += 2
        elif char == "[" and text[i + 1 : i + 2] == ":":
            close = text.find(":]", i + 2)
            if close == -1:
                return None
            i = close + 2
        elif char == "]":
            return i + 1
        else:
            i += 1
    return None


def _translate_bracket(body: str) -> str:
    """Translate one bracket expression, brackets included, to a Python character set."""
    inner = body[1:-1]
    negate = inner.startswith("^")
    if negate:
        inner = inner[1:]
    items: list[str] = []
    i = 0
    while i < len(inner):
        char = inner[i]
        if char == "[" and inner[i + 1 : i + 2] == ":":
            close = inner.find(":]", i + 2)
            name = inner[i + 2 : close]
            if close == -1 or name not in _POSIX_CLASSES:
                raise _LineError(f"[:{name}:] is not a supported character class")
            items.append(_POSIX_CLASSES[name])
            i = close + 2
            continue
        if char == "[" and inner[i + 1 : i + 2] in (".", "="):
            raise _LineError("collating elements [. .] and [= =] are not supported")
        if char == "\\" and i + 1 < len(inner):
            escaped = inner[i + 1]
            items.append(re.escape(_SIMPLE_ESCAPES.get(escaped, escaped)))
            i += 2
            continue
        if char == "]":  # only a leading ] reaches here
            items.append(r"\]")
        elif char in "[&~|" or (char == "-" and inner[i + 1 : i + 2] == "-"):
            items.append("\\" + char)  # Python reads these as set operators or nested sets
        else:
            items.append(char)
        i += 1
    return "[" + ("^" if negate else "") + "".join(items) + "]"


class _FindTranslator:
    """Translate a POSIX basic regular expression, as sed reads it, to a Python regular expression."""

    def __init__(self, text: str, delimiter: str) -> None:
        self._text = text
        self._delimiter = delimiter
        self._i = 0
        self._out: list[str] = []
        self._depth = 0
        self.group_count = 0
        self._closed: set[int] = set()  # groups whose \) has been read: a back-reference may only name these
        self._open: list[int] = []
        # As sed: '*' is literal with nothing before it, '^' anchors only at the start, and a** is a*.
        self._at_start = True
        self._expr_start = True
        self._repeated = False

    def translate(self) -> str:
        text = self._text
        while self._i < len(text):
            char = text[self._i]
            if char == "\\":
                self._escape()
                continue
            self._i += 1
            if char == "[":
                end = _bracket_end(text, self._i - 1)
                if end is None:
                    raise _LineError("the bracket expression [ ... ] is not closed")
                self._atom(_translate_bracket(text[self._i - 1 : end]))
                self._i = end
            elif char == "*":
                self._repeat("*", r"\*")
            elif char == "^":
                if self._expr_start:
                    self._out.append("^")
                    self._expr_start = False
                    self._at_start = True
                    self._repeated = False
                else:
                    self._atom(r"\^")
            elif char == "$":
                at_end = self._i == len(text) or text[self._i : self._i + 2] in (r"\)", r"\|")
                self._atom(r"\Z" if at_end else r"\$")
            elif char == ".":
                self._atom(".")
            else:
                self._atom(re.escape(char))
        if self._depth:
            raise _LineError("\\( has no matching \\)")
        return "".join(self._out)

    def _escape(self) -> None:
        if self._i + 1 >= len(self._text):
            raise _LineError("a trailing backslash")
        escaped = self._text[self._i + 1]
        self._i += 2
        if escaped == self._delimiter:  # \<d> is always the literal delimiter, even when it is a BRE operator such as |
            self._atom(re.escape(escaped))
        elif escaped == "(":
            self._out.append("(")
            self._depth += 1
            self.group_count += 1
            self._open.append(self.group_count)
            self._at_start = self._expr_start = True
            self._repeated = False
        elif escaped == ")":
            if self._depth == 0:
                raise _LineError("\\) has no matching \\(")
            self._depth -= 1
            self._closed.add(self._open.pop())
            self._atom(")")
        elif escaped == "|":
            self._out.append("|")
            self._at_start = self._expr_start = True
            self._repeated = False
        elif escaped == "{":
            end = self._text.find("\\}", self._i)
            if end == -1:
                raise _LineError("\\{ has no matching \\}")
            interval = self._text[self._i : end]
            if not re.fullmatch(r"\d+(,\d*)?|,\d+", interval):
                raise _LineError(f"\\{{{interval}\\}} is not a valid interval")
            if self._at_start:
                raise _LineError("\\{ follows nothing to repeat")
            self._i = end + 2
            self._repeat("{" + interval + "}", "")
        elif escaped in "+?":
            self._repeat(escaped, re.escape(escaped))
        elif escaped in "123456789":
            if int(escaped) not in self._closed:
                raise _LineError(f"\\{escaped} refers to a group the text to find does not define yet")
            self._atom("\\" + escaped)
        elif escaped in "wWsSbB":
            self._atom("\\" + escaped)
        elif escaped == "<":
            self._atom(r"\b(?=\w)")
        elif escaped == ">":
            self._atom(r"\b(?<=\w)")
        elif escaped in _SIMPLE_ESCAPES:
            self._atom(re.escape(_SIMPLE_ESCAPES[escaped]))
        elif escaped.isalnum():
            raise _LineError(f"\\{escaped} is not supported")
        else:
            self._atom(re.escape(escaped))

    def _atom(self, token: str) -> None:
        self._out.append(token)
        self._at_start = self._expr_start = self._repeated = False

    def _repeat(self, token: str, literal: str) -> None:
        if self._at_start:
            self._atom(literal)
        elif self._repeated:
            if token != "*":
                raise _LineError("a repeat operator follows another repeat operator")
        else:
            self._out.append(token)
            self._repeated = True


def _parse_flags(flags: str) -> tuple[bool, bool]:
    """Returns (replace every match, ignore case)."""
    seen: set[str] = set()
    for flag in flags:
        if flag not in ("g", "I") or flag in seen:
            raise _LineError(f"unsupported flag {flag!r}; the flags are g (every match) and I (ignore case)")
        seen.add(flag)
    return "g" in seen, "I" in seen


def _parse_replacement(text: str, group_count: int) -> tuple[str | int, ...]:
    parts: list[str | int] = []
    literal: list[str] = []
    i = 0
    while i < len(text):
        char = text[i]
        if char == "\\":
            if i + 1 >= len(text):
                raise _LineError("a trailing backslash in the replacement")
            escaped = text[i + 1]
            i += 2
            if escaped.isdigit():
                if int(escaped) > group_count:
                    raise _LineError(f"\\{escaped} refers to a group the text to find does not define")
                if literal:
                    parts.append("".join(literal))
                    literal = []
                parts.append(int(escaped))
            else:
                literal.append(_SIMPLE_ESCAPES.get(escaped, escaped))
            continue
        i += 1
        if char == "&":
            if literal:
                parts.append("".join(literal))
                literal = []
            parts.append(0)
        else:
            literal.append(char)
    if literal:
        parts.append("".join(literal))
    return tuple(parts)
