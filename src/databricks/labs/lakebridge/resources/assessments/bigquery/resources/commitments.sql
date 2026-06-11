-- This procedure fetches a current view of BQ Commitments

SELECT '{{project_region}}' AS metadata_level,
        TO_HEX(MD5(capacity_commitment_id)) as capacity_commitment_id_hash,
        commitment_plan,
        state,
        slot_count,
        edition,
        is_flat_rate,
        renewal_plan
from `{{project_region}}`.INFORMATION_SCHEMA.CAPACITY_COMMITMENTS
;
