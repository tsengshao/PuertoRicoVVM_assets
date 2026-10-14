# mkdir -p Puerto_Rico_LandCover_10m
# cd Puerto_Rico_LandCover_10m

# ============================================================
# 1. Download ESA WorldCover 2021 v200 land-cover tiles
# ============================================================

for tile in \
    N15W069 \
    N15W066 \
    N18W069 \
    N18W066
do
    aws s3 cp \
      "s3://esa-worldcover/v200/2021/map/ESA_WorldCover_10m_2021_v200_${tile}_Map.tif" \
      . \
      --no-sign-request
done


# ============================================================
# 2. Mosaic the four tiles
# ============================================================

gdalbuildvrt \
  Puerto_Rico_LandCover_10m_master.vrt \
  ESA_WorldCover_10m_2021_v200_*_Map.tif


# ============================================================
# 3. Convert VRT mosaic to a real GeoTIFF
# ============================================================

gdal_translate \
  Puerto_Rico_LandCover_10m_master.vrt \
  Puerto_Rico_LandCover_10m_master.tif \
  -co TILED=YES \
  -co COMPRESS=DEFLATE \
  -co BIGTIFF=IF_SAFER


# ============================================================
# 4. Create official-style WorldCover color table
# ============================================================
### 10   Tree cover
### 20   Shrubland
### 30   Grassland
### 40   Cropland
### 50   Built-up
### 60   Bare / sparse vegetation
### 70   Snow and ice
### 80   Permanent water
### 90   Herbaceous wetland
### 95   Mangroves
### 100  Moss and lichen
# ============================================================

cat > WorldCover_colors.txt << EOF
0   0   0   0   0
10  0   100 0   255
20  255 187 34  255
30  255 255 76  255
40  240 150 255 255
50  250 0   0   255
60  180 180 180 255
70  240 240 240 255
80  0   100 200 255
90  0   150 160 255
95  0   207 117 255
100 250 230 160 255
EOF


# ============================================================
# 5. Create a colored GeoTIFF quick view
# ============================================================

gdaldem color-relief \
  Puerto_Rico_LandCover_10m_master.tif \
  WorldCover_colors.txt \
  Puerto_Rico_LandCover_10m_quickview.tif \
  -alpha


# ============================================================
# 6. Create JPEG quick view
# ============================================================

gdal_translate \
  Puerto_Rico_LandCover_10m_quickview.tif \
  Puerto_Rico_LandCover_10m_quickview.jpg \
  -of JPEG \
  -b 1 -b 2 -b 3 \
  -outsize 10% 10% \
  -r nearest \
  -co QUALITY=90


# ============================================================
# 7. Check final land-cover file
# ============================================================

gdalinfo Puerto_Rico_LandCover_10m_master.tif
