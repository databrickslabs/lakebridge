SELECT C.DatabaseName,
    FunctionName,
    NumParameters,
    ParameterDataTypes,
    case SrcFileLanguage
    when 'S' then 'SQL'
    when 'C' then 'C'
    when 'P' then 'C++'
    when 'J' then 'JAVA'
    when 'A' then 'SAS'
    end as FunctionLanguage,
    case FunctionType
    when 'A' then 'Aggregate'
    when 'B' then 'Aggregate and statistical'
    when 'C' then 'Contract function'
    when 'F' then 'Scalar'
    when 'H' then 'User-defined method'
    when 'I' then 'Internal type method'
    when 'L' then 'Table operator'
    when 'R' then 'Table'
    when 'S' then 'Statistical'
    end as FunctionType,
    ColumnType as ReturnType
from dbc.FunctionsV as T
left join dbc.ColumnsV as C on C.DatabaseName = T.DatabaseName
and C.TableName = T.SpecificName
and ColumnName = 'RETURN0'
where T.DatabaseName not in (
'All',
'Crashdumps',
'DBC',
'dbcmngr',
'Default',
'External_AP',
'EXTUSER',
'LockLogShredder',
'PUBLIC',
'Sys_Calendar',
'SysAdmin',
'SYSBAR',
'SYSJDBC',
'SYSLIB',
'SystemFe',
'SYSUDTLIB',
'SYSUIF',
'TD_SERVER_DB',
'TDStats',
'TD_SYSGPL',
'TD_SYSXML',
'TDMaps',
'TDPUSER',
'TDQCD',
'tdwm',
'SQLJ',
'TD_SYSFNLIB',
'SYSSPATIAL');
