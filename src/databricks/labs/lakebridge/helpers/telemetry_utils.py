from databricks.sdk.useragent import alphanum_pattern, semver_pattern


def make_alphanum_or_semver(value: str) -> str:
    if alphanum_pattern.match(value) or semver_pattern.match(value):
        return value
    # assume it's not a semver, replace illegal alphanum chars
    result = []
    for char in value:
        if not alphanum_pattern.match(char):
            char = '_'
        result.append(char)
    return "".join(result)
