from __future__ import annotations

import re
from typing import Protocol


class ReconcileStrategy(Protocol):
    """Strategy interface for matching source names against candidate target names.

    Batch interface: receives all names at once so implementations (e.g. LLM-based)
    can make globally optimal assignments.
    """

    def match_all(self, source_names: list[str], candidate_names: list[str]) -> dict[str, str | None]:
        """Return a mapping from each source name to its best candidate match (or None)."""


class NormalizedMatcher:
    """Match names by trying progressively looser normalisations.

    For each normalisation step the matcher builds a lookup from normalised
    candidate -> original candidate. If a source name normalises to the same
    form as exactly one candidate at that step, it's a match.
    """

    DELIMITER_RE = re.compile(r"[-\s]+")

    def match_all(self, source_names: list[str], candidate_names: list[str]) -> dict[str, str | None]:
        results: dict[str, str | None] = {}
        remaining = list(candidate_names)

        for src in source_names:
            matched = self._match_one(src, remaining)
            results[src] = matched
            if matched is not None:
                remaining.remove(matched)

        return results

    @classmethod
    def _match_one(cls, source_name: str, candidates: list[str]) -> str | None:
        src_forms = cls.normalize_steps(source_name)
        candidate_forms = [(cand, cls.normalize_steps(cand)) for cand in candidates]

        for step, src_norm in enumerate(src_forms):
            matches = [cand for cand, forms in candidate_forms if forms[step] == src_norm]
            if len(matches) == 1:
                return matches[0]
        return None

    @classmethod
    def normalize_steps(cls, name: str) -> list[str]:
        """Return progressively more aggressive normalisations of `name`.

        Steps:
        0. trim + lowercase
        1. unify delimiters (kebab / spaces -> underscore)
        2. collapse all underscores (`emp_id` -> `empid`)
        3. naive singularise (strip trailing s/es/ies)
        """
        form = name.strip().lower()
        forms = [form]
        form = cls.DELIMITER_RE.sub("_", form)
        forms.append(form)
        form = form.replace("_", "")
        forms.append(form)
        form = cls.naive_singularize(form)
        forms.append(form)
        return forms

    @staticmethod
    def naive_singularize(word: str) -> str:
        """Best-effort singularisation for English table/column names.

        Rules (applied in order):
        - `ies` -> `y`  (categories -> category)
        - `ses` -> `s`  (addresses -> address)
        - `s`   -> ``   (employees -> employee)
        """
        if word.endswith("ies"):
            return word[:-3] + "y"
        if word.endswith("ses"):
            return word[:-2]
        if word.endswith("s"):
            return word[:-1]
        return word


class LlmMatcher:
    """Match source names against candidate target names using an LLM.

    Implement `ReconcileStrategy` so it can slot into
    `run_strategy_chain` after `NormalizedMatcher`. Plug it in **after** the
    deterministic matcher so the LLM is only asked about names that simple
    normalisation could not resolve.
    """

    def match_all(self, source_names: list[str], candidate_names: list[str]) -> dict[str, str | None]:
        # TODO: implement.
        # Sketch:
        #   1. Build a prompt that lists `source_names` and `candidate_names` and asks for an
        #      assignment of each source to at most one candidate (or `null`).
        #   2. Call the serving endpoint, e.g.
        #      `self._ws.serving_endpoints.query(name=self._endpoint_name, ...)`.
        #   3. Parse the response into `{source: candidate | None}`.
        #   4. Drop / null-out any candidate the model returned that is not in `candidate_names`,
        #      so `run_strategy_chain.remaining_candidates.remove(matched)` cannot fail.
        raise NotImplementedError("LLM-assisted reconcile strategy is not yet implemented")


def run_strategy_chain(
    strategies: list[ReconcileStrategy],
    source_names: list[str],
    candidate_names: list[str],
) -> dict[str, str | None]:
    """Run `strategies` in order, returning a mapping from source -> candidate.

    Each strategy is given the remaining unmatched source names and remaining
    candidates. Once a candidate is claimed it is removed from the pool for
    subsequent strategies.
    """
    results: dict[str, str | None] = {}
    remaining_candidates = list(candidate_names)
    unmatched = list(source_names)

    for strategy in strategies:
        batch_result = strategy.match_all(unmatched, remaining_candidates)
        still_unmatched: list[str] = []
        for src in unmatched:
            matched = batch_result.get(src)
            if matched is not None:
                results[src] = matched
                remaining_candidates.remove(matched)
            else:
                still_unmatched.append(src)
        unmatched = still_unmatched

    for src in unmatched:
        results[src] = None

    return results
