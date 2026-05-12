-- This procedure fetches BQ Commitment modifications
-- in the last 41 days

DECLARE metadatalevel STRING DEFAULT 'region-us';
    
-- SET metadatalevel to the format <project>.<region>
SET metadatalevel = 'my-gcp-project.region-us';

EXECUTE IMMEDIATE
FORMAT("""
    SELECT @metadatalevel AS metadata_level,
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
    from `%s`.INFORMATION_SCHEMA.CAPACITY_COMMITMENT_CHANGES
;
""", metadatalevel)
USING 
metadatalevel AS metadatalevel;