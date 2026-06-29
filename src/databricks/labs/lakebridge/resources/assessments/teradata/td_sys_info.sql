SELECT Infokey as K,
    InfoData as V
FROM dbc.DBCInfoTbl

UNION ALL

SELECT *
FROM (
    SELECT '# of AMPs' as K,
        to_char(HASHAMP() + 1) as V
) X;
