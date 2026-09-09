'reinit'
'set background 1'
'c'
exp='puertorico_1p6km'
*exp='puertorico_500m'
'open 'exp'/gs_ctl_files/bar.ctl'
figdir=exp'/gs_ctl_files/figs10mins'
'! mkdir -p 'figdir
say '! convert -delay 5 -loop 0 'figdir'/{001..145}.png 'figdir'/output.gif'

* initial profile
'set x 1'
'set y 1'
'set t 1'
'set vrange 310 400'
'd the'
'd thes'
'd const(lev,380);ug;vg'
'd 380+mag(ug,vg)'
'draw title initial'
'gxprint 'figdir'/00_initial.png x2200 y1700'
pull c

'reinit'
'set background 1'
'c'
'open 'exp'/gs_ctl_files/dynamic.ctl'
'open 'exp'/gs_ctl_files/surface.ctl'
'open 'exp'/gs_ctl_files/topo.ctl'

'q file'
line=sublin(result,5)
nx=subwrd(line,3) 
ny=subwrd(line,6)
nz=subwrd(line,9)
nt=subwrd(line,12)


m=0
while(m<=145)
say m
'c'
'set grads off'
*'set x 1 256'
*'set y 1 256'
'set z 1'
'set t 1'
'set lon 292.4 294.8'
'set lat 17.3 19.4'
'set xlint 0.5'
'set ylint 0.5'
*X Limits = 2.58333 to 8.41667
*Y Limits = 0.75 to 7.75
'set parea 1 10 1.5 7.75'

'set mpdset hires'
'set map 1 1 8'
'set xlopts 1 5 0.2'
'set ylopts 1 5 0.2'

'set mproj off'
*'color 0 1000 100 -kind black->white'
'color 0 1000 100 -kind white->(150,150,150)'
'd height.3(z=1,t=1)*1000.'

'set gxout contour'
'set clevs 0.0001'
'set cthick 10'
'set ccolor 1'
'set clab off'
'd height.3(z=1,t=1)'

'set t 1'
if (m=0)
*tt=(m-1)*144+2
*tt1=m*144+1
tt=2
tt1=(m+1)*144+1
'define k1=ave(sprec.2(z=1),t='tt',t='tt1')*86400'
else
'set t 'm
'define k1=sprec.2(z=1)*3600'
endif

'cwbraincol.gs'
'set gxout shaded'
'set xlint 1'
'set ylint 1'
'd k1'
'cbar.gs'
'draw map 1 1 8'
'set lev 500'
'set cthick 5'
'set ccolor 1'
'set gxout stream'
'set strmden -10'
*'d u.1;v.1'
'q time'
res=subwrd(result,3)
mod=math_mod(m,6)
if(mod=1)
res1=substr(res,1,2)
res1=res1':00'
else
res1=substr(res,1,5)
endif

*'draw title 'res1''
if (m=0)
'draw title rain [mm/day]'
else
'draw title rain [mm/hr] 'res1
endif

mm=math_format( '%03.0f', m)
'gxprint 'figdir'/'mm'.png x2200 y1700'
if (m=0)
'gxprint 'figdir'/'mm'.svg'
endif

if(m=0)
* draw timeseries of land
'set t 1 last'
'set x 1'
'set y 1'
'define rland=3600*aave(maskout(sprec.2,height.3(t=1,z=1)-0.000005),x=1,x='nx',y=1,y='ny')'
pull c
'c'
'set tlsupp month'
'set vrange -0.1 4'
'd rland'
'draw title rain over land'
'gxprint 'figdir'/rain_land.png x2200 y1700'
'gxprint 'figdir'/rain_land.svg'
pull c
endif

*pull v
m=m+1
endwhile
'! convert -delay 5 -loop 0 'figdir'/{001..145}.png 'figdir'/output.gif'


