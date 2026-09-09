* Plot VVM land type and terrain height for Puerto Rico.
*
* Run from the topography directory with OpenGrADS, for example:
*   grads -blc "run plot_puerto_rico_landtype_height.gs 500m"
*   grads -blc "run plot_puerto_rico_landtype_height.gs 1.5km"
*
* If no argument is supplied, edit the default resolution below.  In the
* model_PuertoRico_512x512km data set,
* 1.6km product.  Other labels (for example 100m) are used literally.

function main(args)

* -------------------------- user settings -------------------------------
resolution = '500m'
input_dir  = 'model_PuertoRico_512x512km'
figure_dir = 'model_PuertoRico_512x512km/figures'

lon_min = 292.4
lon_max = 294.8
lat_min = 17.3
lat_max = 19.4

* domain x:1/4-3/4, y:3/8-5/8, 
lon_min = 360 - 67.64
lon_max = 360 - 65.224
lat_min = 17.64
lat_max = 18.80

* sdfopen reads this file's native longitude as -180--180.  Keep the desired
* 0--360 settings above, but translate them for the GrADS dimension request.
plot_lon_min = lon_min
plot_lon_max = lon_max
if (lon_min > 180)
  plot_lon_min = lon_min - 360
endif
if (lon_max > 180)
  plot_lon_max = lon_max - 360
endif
say 'plot_lon_range: 'plot_lon_min' 'plot_lon_max
say 'plot_lat_range: 'lat_min' 'lat_max

* Optional command-line argument overrides the setting above.
if (args != '')
  resolution = subwrd(args,1)
endif

infile  = input_dir'/Puerto_Rico_VVM_'resolution'.nc'
outfile = figure_dir'/Puerto_Rico_landtype_height_'resolution'.png'
outfilepdf = figure_dir'/Puerto_Rico_landtype_height_'resolution'.pdf'

'reinit'
'set background 1'
'clear'
'sdfopen 'infile
if (rc != 0)
  say 'ERROR: GrADS could not open: 'infile
  say 'Available here: 100m, 500m, and 1.6km.'
  return
endif

* --------------------------- fixed colors -------------------------------
* VVM USGS land-use colors.  IDs possible from this data-generation method:
* 1, 4, 7, 8, 13, 16, 17, 18, 19, 20, and 24.
'set rgb 31 220  45  45'
'set rgb 32 245 205  60'
'set rgb 33 145 205  75'
'set rgb 34 205 160  90'
'set rgb 35  25 120  50'
'set rgb 36  65 145 215'
'set rgb 37  70 200 185'
'set rgb 38  25 135 120'
'set rgb 39 215 195 160'
'set rgb 40 180 165 200'
'set rgb 41 242 246 250'

* Ocean background and the fixed 0--1400 m terrain palette (100 m bins).
'set rgb 42 205 230 250'
'set rgb 50 220 245 180'
'set rgb 51 190 232 140'
'set rgb 52 150 215 100'
'set rgb 53 105 190  75'
'set rgb 54  65 160  65'
'set rgb 55 185 190  90'
'set rgb 56 210 190 105'
'set rgb 57 222 175 105'
'set rgb 58 205 150  95'
'set rgb 59 185 125  85'
'set rgb 60 160 105  80'
'set rgb 61 145 120 105'
'set rgb 62 165 150 140'
'set rgb 63 205 200 195'
'set rgb 64 245 245 242'

* Common map settings.
'set t 1'
'set lon 'plot_lon_min' 'plot_lon_max
'set lat 'lat_min' 'lat_max
'set mpdset hires'
'set mproj latlon'
'set map 0 1 1'
'set mpdraw off'
'set frame on'
'set grid off'
'set grads off'
'set xlint 0.5'
'set ylint 0.5'
'set xlopts 1 4 0.14'
'set ylopts 1 4 0.14'

* -------------------------- left: land type -----------------------------
'set parea 0.65 5.35 2.85 7.05'
'set gxout shaded'
'set csmooth off'
* Boundaries are midpoints between the valid (non-consecutive) class IDs.
'set clevs 2.5 5.5 7.5 10.5 14.5 16.5 17.5 18.5 19.5 22'
'set ccols 31 32 33 34 35 36 37 38 39 40 41'
'd lu'
* Black topo=1 contour is used as the coastline; built-in map is off.
'set gxout contour'
'set clevs 1'
'set ccolor 1'
'set cthick 4'
'set clab off'
'd topo'
'set string 1 c 5 0'
'set strsiz 0.18 0.20'
'draw string 3.0 6.6 Land type (VVM USGS)'

* Categorical legend: every color is tied to its class ID and name.
* Keep the two columns separated; "Evergreen Broadleaf" is the widest
* entry in the left column.
'set strsiz 0.095 0.115'
'set string 1 l 4 0'

'set line 31'; 'draw recf 0.72 1.99 0.98 2.17'
'set line 1 1 2'; 'draw rec 0.72 1.99 0.98 2.17'
'draw string 1.06 2.08 1  Urban / Built-Up'

'set line 32'; 'draw recf 0.72 1.69 0.98 1.87'
'set line 1 1 2'; 'draw rec 0.72 1.69 0.98 1.87'
'draw string 1.06 1.78 4  Mixed Cropland'

'set line 33'; 'draw recf 0.72 1.39 0.98 1.57'
'set line 1 1 2'; 'draw rec 0.72 1.39 0.98 1.57'
'draw string 1.06 1.48 7  Grassland'

'set line 34'; 'draw recf 0.72 1.09 0.98 1.27'
'set line 1 1 2'; 'draw rec 0.72 1.09 0.98 1.27'
'draw string 1.06 1.18 8  Shrubland'

'set line 35'; 'draw recf 0.72 0.79 0.98 0.97'
'set line 1 1 2'; 'draw rec 0.72 0.79 0.98 0.97'
'draw string 1.06 0.88 13 Evergreen Broadleaf'

'set line 36'; 'draw recf 0.72 0.49 0.98 0.67'
'set line 1 1 2'; 'draw rec 0.72 0.49 0.98 0.67'
'draw string 1.06 0.58 16 Water Bodies'

'set line 37'; 'draw recf 3.18 1.99 3.44 2.17'
'set line 1 1 2'; 'draw rec 3.18 1.99 3.44 2.17'
'draw string 3.52 2.08 17 Herbaceous Wetland'

'set line 38'; 'draw recf 3.18 1.69 3.44 1.87'
'set line 1 1 2'; 'draw rec 3.18 1.69 3.44 1.87'
'draw string 3.52 1.78 18 Wooded Wetland'

'set line 39'; 'draw recf 3.18 1.39 3.44 1.57'
'set line 1 1 2'; 'draw rec 3.18 1.39 3.44 1.57'
'draw string 3.52 1.48 19 Barren / Sparse'

'set line 40'; 'draw recf 3.18 1.09 3.44 1.27'
'set line 1 1 2'; 'draw rec 3.18 1.09 3.44 1.27'
'draw string 3.52 1.18 20 Herbaceous Tundra'

'set line 41'; 'draw recf 3.18 0.79 3.44 0.97'
'set line 1 1 2'; 'draw rec 3.18 0.79 3.44 0.97'
'draw string 3.52 0.88 24 Snow or Ice'

* --------------------------- right: height ------------------------------
'set parea 6.05 10.75 2.85 7.05'
* Water (lu=16) is assigned -1 m solely for coloring, so it receives the
* dedicated ocean color; land retains its physical height in meters.
'set gxout shaded'
'set csmooth off'
'set clevs -0.5 100 200 300 400 500 600 700 800 900 1000 1100 1200 1300 1400'
'set ccols 42 50 51 52 53 54 55 56 57 58 59 60 61 62 63 64'
'd const(maskout(height*1000,abs(lu-16)-0.5),-1,-u)'
'set gxout contour'
'set clevs 1'
'set ccolor 1'
'set cthick 4'
'set clab off'
'd topo'
'set string 1 c 5 0'
'set strsiz 0.18 0.20'
'draw string 8.4 6.6 Terrain height (m)'

* Fixed horizontal terrain color bar (same colors/range at every resolution).
xb = 5.75
yb = 1.48
yt = 1.70
dx = 0.313333
i = 0
while (i < 15)
  xl = xb + i*dx
  xr = xl + dx
  col = 50 + i
  'set line 'col
  'draw recf 'xl' 'yb' 'xr' 'yt
  'set line 1 1 1'
  'draw rec 'xl' 'yb' 'xr' 'yt
  i = i + 1
endwhile

'set string 1 c 4 0'
'set strsiz 0.100 0.120'
i = 0
while (i <= 6)
  xlab = xb + i*2*dx
  value = i*200
  'draw string 'xlab' 1.31 'value
  i = i + 1
endwhile
'draw string 10.45 1.31 1400+'
'set strsiz 0.115 0.135'
'draw string 8.025 1.03 Height above mean sea level (m)'

* Figure heading and output.
'set string 1 c 6 0'
'set strsiz 0.23 0.26'
'draw string 5.50 8.08 Puerto Rico VVM: 'resolution
'set string 1 c 4 0'
'set strsiz 0.115 0.135'
'draw string 5.50 7.60 'lon_min'E--'lon_max'E, 'lat_min'N--'lat_max'N'

'! mkdir -p 'figure_dir
'gxprint 'outfile' white x2200 y1700'
'gxprint 'outfilepdf' white'
if (rc != 0)
  say 'ERROR: Could not write: 'outfile
  return
endif
say 'Wrote: 'outfile
say 'Wrote: 'outfilepdf

return
