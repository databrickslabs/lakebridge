SELECT EXTRACT(YEAR FROM TO_TIMESTAMP('2013-05-08T23:39:20.123-07:00')) AS v
    FROM (values(1)) v1;

------+
   V  |
------+
 2013 |
------+