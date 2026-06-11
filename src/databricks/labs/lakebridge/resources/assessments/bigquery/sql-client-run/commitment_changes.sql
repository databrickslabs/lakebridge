-- This procedure fetches BQ Commitment modifications
-- in the last 41 days

SELECT '{{project_region}}' AS metadata_level,
        change_timestamp,
        TO_HEX(MD5(capacity_commitment_id)) as capacity_commitment_id_hash,
        commitment_plan,
        state,
        slot_count,
        action,
        commitment_start_time,
        commitment_end_time,
        failure_status,
        renewal_plan,
        edition,
        is_flat_rate
from `{{project_region}}`.INFORMATION_SCHEMA.CAPACITY_COMMITMENT_CHANGES
;
