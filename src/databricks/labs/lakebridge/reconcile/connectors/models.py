import dataclasses


@dataclasses.dataclass
class NormalizedIdentifier:
    ansi_normalized: str
    source_normalized: str
