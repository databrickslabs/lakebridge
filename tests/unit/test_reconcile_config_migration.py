"""ReconcileConfig schema migrations.

Upstream's ``v1_migrate`` flattens ``database_config`` + ``data_source`` into the
``source`` / ``target`` connection structs. ``v2_migrate`` is the reconcile-optimizer
addition — it introduces the source-agnostic ``reconcile_optimizer`` flag and folds
older field names (``fingerprint_precheck``, ``redshift_fingerprint_precheck``,
``use_fingerprint_precheck``) into it on the way through.
"""

from databricks.labs.lakebridge.config import ReconcileConfig


def _v1_payload(extra: dict | None = None) -> dict:
    raw = {
        "data_source": "redshift",
        "report_type": "all",
        "secret_scope": "scope",
        "database_config": {
            "source_catalog": "dev",
            "source_schema": "public",
            "target_catalog": "c",
            "target_schema": "s",
        },
        "metadata_config": {"catalog": "m", "schema": "r", "volume": "v"},
        "version": 1,
    }
    if extra:
        raw.update(extra)
    return raw


def test_v1_migrate_advances_to_v2_with_source_target_structs():
    """Upstream's existing v1→v2 must keep working: ``database_config`` and
    ``data_source`` are flattened into ``source`` / ``target`` and v2 schema is
    declared. We only inherit this — we don't override it."""
    migrated = ReconcileConfig.v1_migrate(_v1_payload())
    assert migrated["version"] == 2
    assert migrated["source"]["dialect"] == "redshift"
    assert migrated["source"]["catalog"] == "dev"
    assert migrated["source"]["schema"] == "public"
    assert migrated["source"]["uc_connection_name"] == "TODO"
    assert migrated["target"]["catalog"] == "c"
    assert migrated["target"]["schema"] == "s"


def test_v2_migrate_adds_reconcile_optimizer_default_false():
    """v2 configs that predate the optimizer flag should keep their behaviour:
    no flag set, default ``False`` — i.e. ``v2_migrate`` is a no-op for callers
    who never opted in."""
    raw = {
        "report_type": "all",
        "source": {"dialect": "redshift", "catalog": "dev", "schema": "public", "uc_connection_name": "rs_conn"},
        "target": {"catalog": "c", "schema": "s"},
        "metadata_config": {"catalog": "m", "schema": "r", "volume": "v"},
        "version": 2,
    }
    migrated = ReconcileConfig.v2_migrate(dict(raw))
    assert migrated["version"] == 3
    assert "reconcile_optimizer" not in migrated  # default applied at dataclass load time


def test_v2_migrate_renames_fingerprint_precheck():
    """The v3 PoC field name ``fingerprint_precheck`` is itself now legacy: configs
    written before the user-facing rename must round-trip the value to the renamed
    ``reconcile_optimizer`` flag."""
    raw = {
        "report_type": "all",
        "source": {"dialect": "redshift", "catalog": "dev", "schema": "public", "uc_connection_name": "rs_conn"},
        "target": {"catalog": "c", "schema": "s"},
        "metadata_config": {"catalog": "m", "schema": "r", "volume": "v"},
        "fingerprint_precheck": True,
        "version": 2,
    }
    migrated = ReconcileConfig.v2_migrate(dict(raw))
    assert migrated["version"] == 3
    assert migrated["reconcile_optimizer"] is True
    assert "fingerprint_precheck" not in migrated


def test_v2_migrate_renames_redshift_fingerprint_precheck():
    """Long-lived internal deployments that set ``redshift_fingerprint_precheck`` on
    a v2 config (pre-rename) must round-trip the value to ``reconcile_optimizer``."""
    raw = {
        "report_type": "all",
        "source": {"dialect": "redshift", "catalog": "dev", "schema": "public", "uc_connection_name": "rs_conn"},
        "target": {"catalog": "c", "schema": "s"},
        "metadata_config": {"catalog": "m", "schema": "r", "volume": "v"},
        "redshift_fingerprint_precheck": True,
        "version": 2,
    }
    migrated = ReconcileConfig.v2_migrate(dict(raw))
    assert migrated["version"] == 3
    assert migrated["reconcile_optimizer"] is True
    assert "redshift_fingerprint_precheck" not in migrated


def test_v2_migrate_renames_use_fingerprint_precheck():
    """Same round-trip semantics for the older ``use_fingerprint_precheck`` name."""
    raw = {
        "report_type": "all",
        "source": {"dialect": "redshift", "catalog": "dev", "schema": "public", "uc_connection_name": "rs_conn"},
        "target": {"catalog": "c", "schema": "s"},
        "metadata_config": {"catalog": "m", "schema": "r", "volume": "v"},
        "use_fingerprint_precheck": True,
        "version": 2,
    }
    migrated = ReconcileConfig.v2_migrate(dict(raw))
    assert migrated["version"] == 3
    assert migrated["reconcile_optimizer"] is True
    assert "use_fingerprint_precheck" not in migrated


def test_v2_migrate_preserves_explicit_new_flag():
    """If both a legacy and the new key are present (operator hand-edit), the explicit
    new ``reconcile_optimizer`` key wins and the legacy keys are dropped."""
    raw = {
        "report_type": "all",
        "source": {"dialect": "redshift", "catalog": "dev", "schema": "public", "uc_connection_name": "rs_conn"},
        "target": {"catalog": "c", "schema": "s"},
        "metadata_config": {"catalog": "m", "schema": "r", "volume": "v"},
        "reconcile_optimizer": False,
        "fingerprint_precheck": True,
        "version": 2,
    }
    migrated = ReconcileConfig.v2_migrate(dict(raw))
    assert migrated["reconcile_optimizer"] is False
    assert "fingerprint_precheck" not in migrated


def test_v2_migrate_conflicting_legacy_flags_are_deterministic_first_wins():
    """When multiple legacy names are present with conflicting values (an operator
    hand-edit or a merge of two old configs), ``v2_migrate`` resolves deterministically:
    ``fingerprint_precheck`` is checked first, then ``redshift_fingerprint_precheck``,
    then ``use_fingerprint_precheck``, and the losers are dropped. This pins the
    precedence order so the resolution can never become dependent on dict iteration
    order, and documents that conflicting legacy flags do NOT raise — they resolve
    silently to the first."""
    raw = {
        "report_type": "all",
        "source": {"dialect": "redshift", "catalog": "dev", "schema": "public", "uc_connection_name": "rs_conn"},
        "target": {"catalog": "c", "schema": "s"},
        "metadata_config": {"catalog": "m", "schema": "r", "volume": "v"},
        "fingerprint_precheck": True,
        "use_fingerprint_precheck": False,
        "version": 2,
    }
    migrated = ReconcileConfig.v2_migrate(dict(raw))
    assert migrated["version"] == 3
    assert migrated["reconcile_optimizer"] is True, "fingerprint_precheck is checked first, so it wins the conflict"
    assert "fingerprint_precheck" not in migrated
    assert "use_fingerprint_precheck" not in migrated, "the losing legacy flag must not leak through"

    # Precedence is stable regardless of insertion order of the legacy keys.
    reordered = {
        "report_type": "all",
        "source": {"dialect": "redshift", "catalog": "dev", "schema": "public", "uc_connection_name": "rs_conn"},
        "target": {"catalog": "c", "schema": "s"},
        "metadata_config": {"catalog": "m", "schema": "r", "volume": "v"},
        "use_fingerprint_precheck": False,
        "fingerprint_precheck": True,
        "version": 2,
    }
    assert ReconcileConfig.v2_migrate(dict(reordered))["reconcile_optimizer"] is True


def test_full_migration_chain_v1_to_v3_carries_legacy_flag_through():
    """End-to-end: a v1 config carrying ``use_fingerprint_precheck`` (theoretically
    possible from a long-lived internal deployment) must end up at v3 with the flag
    surfaced as ``reconcile_optimizer``. Documents the chain ``v1_migrate → v2_migrate``."""
    raw = _v1_payload({"use_fingerprint_precheck": True})
    after_v1 = ReconcileConfig.v1_migrate(dict(raw))
    final = ReconcileConfig.v2_migrate(after_v1)
    assert final["version"] == 3
    assert final["reconcile_optimizer"] is True
    assert "use_fingerprint_precheck" not in final
    assert "redshift_fingerprint_precheck" not in final
