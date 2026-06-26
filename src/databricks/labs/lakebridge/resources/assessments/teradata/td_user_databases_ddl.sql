CREATE TABLE td_user_databases (
    DatabaseName VARCHAR,
    CreatorName VARCHAR,
    CreateTimeStamp TIMESTAMP,
    LastAlterTimeStamp TIMESTAMP,
    ProtectionType VARCHAR,
    JournalFlag VARCHAR,
    PermSpace BIGINT,
    SpoolSpace BIGINT,
    TempSpace BIGINT
);
