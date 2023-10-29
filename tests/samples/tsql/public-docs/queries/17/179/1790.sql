-- see https://learn.microsoft.com/en-us/sql/t-sql/spatial-geography/shortestlineto-geography-data-type?view=sql-server-ver16

DECLARE @g1 geography = 'CIRCULARSTRING(-122.358 47.653, -122.348 47.649, -122.348 47.658, -122.358 47.658, -122.358 47.653)';  
DECLARE @g2 geography = 'LINESTRING(-119.119263 46.183634, -119.273071 47.107523, -120.640869 47.569114, -122.200928 47.454094)';  
SELECT @g1.ShortestLineTo(@g2).ToString();