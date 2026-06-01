-- This procedure fetches a current view of BQ Commitments

DECLARE metadatalevel STRING DEFAULT 'region-us';
    
-- SET metadatalevel to the format <project>.<region>
SET metadatalevel = 'my-gcp-project.region-us';

EXECUTE IMMEDIATE
FORMAT("""
    SELECT @metadatalevel AS metadata_level,
            TO_HEX(MD5(capacity_commitment_id)) as capacity_commitment_id_hash,
            commitment_plan,
            state,
            slot_count,
            edition,
            is_flat_rate,
            renewal_plan
    from `%s`.INFORMATION_SCHEMA.CAPACITY_COMMITMENTS
;
""", metadatalevel)
USING 
metadatalevel AS metadatalevel;