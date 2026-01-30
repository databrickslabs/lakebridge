/**
 * Retrieves metadata for all data files (type = 0) in the specified database
 * from sys.database_files. Returns file name, type, current size, free space,
 * maximum size, and a timestamp indicating when the data was extracted.
 */
SELECT Db_name()                                                           AS
       database_name,
       NAME                                                                AS
       FileName,
       type_desc,
       size / 128.0                                                        AS
       CurrentSizeMB,
       size / 128.0 - Cast(Fileproperty(NAME, 'SpaceUsed') AS INT) / 128.0 AS
       FreeSpaceInMB,
       max_size                                                            AS
       MaxSize,
       Sysdatetime()                                                       AS
       extract_ts
FROM   sys.database_files
WHERE  type = 0;
