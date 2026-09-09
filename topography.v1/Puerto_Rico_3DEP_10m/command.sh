#!/bin/bash

# mkdir -p Puerto_Rico_3DEP_10m
# cd Puerto_Rico_3DEP_10m

for tile in \
    n19w068 n19w067 n19w066 \
    n18w068 n18w067 n18w066
do
    aws s3 cp \
      "s3://prd-tnm/StagedProducts/Elevation/13/TIFF/current/${tile}/" \
      . \
      --recursive \
      --no-sign-request \
done

# merging all tif
gdalbuildvrt Puerto_Rico_DEM_10m_master.vrt USGS_13_*.tif

gdal_translate \
    Puerto_Rico_DEM_10m_master.vrt \
    Puerto_Rico_DEM_10m_master.tif \
    -co TILED=YES \
    -co COMPRESS=DEFLATE \
    -co BIGTIFF=IF_SAFER

# create quickview (hillshade and color version)
gdaldem hillshade \
  Puerto_Rico_DEM_10m_master.tif \
  Puerto_Rico_DEM_10m_hillshade.tif \
  -z 1 \
  -az 315 \
  -alt 45

gdal_translate \
  Puerto_Rico_DEM_10m_hillshade.tif \
  Puerto_Rico_DEM_10m_hillshade.jpg \
  -outsize 50% 50% \
  -of JPEG \
  -co QUALITY=90

# color version
cat > dem_colors.txt << EOF
0     0   100  0
100   80  160  60
300   180 180 80
600   180 120 60
900   160 100 70
1200  200 200 200
1500  255 255 255
EOF
gdaldem color-relief \
  Puerto_Rico_DEM_10m_master.tif \
  dem_colors.txt \
  Puerto_Rico_DEM_10m_color.tif
gdal_translate \
  Puerto_Rico_DEM_10m_color.tif \
  Puerto_Rico_DEM_10m_color.jpg \
  -outsize 50% 50% \
  -of JPEG \
  -co QUALITY=90


