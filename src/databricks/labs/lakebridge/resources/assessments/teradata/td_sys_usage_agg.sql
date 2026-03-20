SELECT TheDate,
    HOUR(time_of_day) as hour_of_day,
    round(avg(totNCPUs), 0) as totNCPUs,
    round(avg(totVproc1), 0) as totVproc1,
    round(avg(totCPUUExec), 0) as totCPUUExec,
    round(avg(totCPUUServ), 0) as totCPUUServ,
    round(avg(totCPUIoWait), 0) as totCPUIoWait,
    round(avg(totMemSizeMB), 0) as totMemSizeMB,
    round(avg(totCPUIdle), 0) as totCPUIdle,
    round(avg(totMemFreeMB), 0) as totMemFreeMB
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
