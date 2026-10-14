#!/bin/bash

# mkdir -p Puerto_Rico_S2_10m
# cd Puerto_Rico_S2_10m

#for lat in N17 N18
for lat in N17
do
    for lon in W068 W067 W066
    do
        aws s3 cp \
          "s3://esa-worldcover-s2/rgbnir/2021/${lat}/ESA_WorldCover_10m_2021_v200_${lat}${lon}_S2RGBNIR.tif" \
          . \
          --no-sign-request
    done
done


gdalbuildvrt \
  Puerto_Rico_S2_10m_cloudfree.vrt \
  *S2RGBNIR.tif

gdal_translate \
  Puerto_Rico_S2_10m_cloudfree.vrt \
  Puerto_Rico_S2_10m_cloudfree.tif \
  -co TILED=YES \
  -co COMPRESS=DEFLATE \
  -co BIGTIFF=IF_SAFER


#for f in *S2RGBNIR.tif
for f in *.tif
do
    gdal_translate \
      -b 3 -b 2 -b 1 \
      -scale 0 3000 0 255 \
      -ot Byte \
      -of JPEG \
      -co QUALITY=90 \
      "$f" \
      "${f%.tif}_quickview.jpg"
done
