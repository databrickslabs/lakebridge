SELECT TheDate,
    HOUR(time_of_day) as hour_of_day,
    CAST(round(avg(totNCPUs), 0) AS FLOAT) as totNCPUs,
    CAST(round(avg(totVproc1), 0) AS FLOAT) as totVproc1,
    CAST(round(avg(totCPUUExec), 0) AS FLOAT) as totCPUUExec,
    CAST(round(avg(totCPUUServ), 0) AS FLOAT) as totCPUUServ,
    CAST(round(avg(totCPUIoWait), 0) AS FLOAT) as totCPUIoWait,
    CAST(round(avg(totMemSizeMB), 0) AS FLOAT) as totMemSizeMB,
    CAST(round(avg(totCPUIdle), 0) AS FLOAT) as totCPUIdle,
    CAST(round(avg(totMemFreeMB), 0) AS FLOAT) as totMemFreeMB
FROM (
    SELECT thedate,
        cast(
            cast(
                cast(TheTime as format '99:99:99.99') as char(11)
            ) as time(6)
        ) as time_of_day,
        sum(NCPUs) as totNCPUs,
        sum(Vproc1) as totVproc1,
        sum(CPUUExec) as totCPUUExec,
        sum(CPUUServ) as totCPUUServ,
        sum(CPUIoWait) as totCPUIoWait,
        sum(MemSize) as totMemSizeMB,
        sum(CPUIdle) as totCPUIdle,
        sum(round(MemFreeKB / 1024, 0)) as totMemFreeMB
    from dbc.resusagespma
    where TheDate >= date - 60
    group by thedate,
        thetime
) X
group by TheDate,
    hour_of_day;
