# Puerto Rico VVM / VVMex Topography and Land-Surface Preprocessing

## 0. Implementation Requirements

Puerto Rico preprocessing 第一階段先完成一個 **model-independent master NetCDF**，之後再由這個 master file 產生：

* 100 m
* 500 m
* 1 km
* 1.5 km
* 2 km

的 VVM / VVMex initialization files。

目前主要輸入為兩個已經 mosaic 完成的 GeoTIFF：

```text
Puerto_Rico_DEM_10m_master.tif
Puerto_Rico_LandCover_10m_master.tif
```

第一階段：

```text
DEM GeoTIFF
      +
WorldCover GeoTIFF
      ↓
common projection / common grid
      ↓
Puerto_Rico_surface_master.nc
```

master file 至少保存：

```text
x
y
lon
lat

height_raw
worldcover
land_mask
water_mask

optional:
land_fraction
water_fraction
```

此 master NC 不先轉成 VVM / VVMex land-type numbering。

---

## 0.1 Main-island-only switch

提供：

```python
MAIN_ISLAND_ONLY = True
```

或：

```bash
--main-island-only
```

### `False`

保留所有 land masses，包括：

* Puerto Rico main island
* Vieques
* Culebra
* Mona
* Desecheo
* offshore islands / small islets

### `True`

只保留 Puerto Rico main island。

建議：

```text
land/water mask
      ↓
connected-component labeling
      ↓
largest connected land component
      ↓
keep Puerto Rico main island
      ↓
other land components → water
```

不要使用人工 bounding box。

---

## 0.2 Single-grid topography cave check

DEM averaging 後轉成 VVM/VVMex discrete vertical `topo` index，再進行 single-grid cave check。

檢查：

```text
         north
           |
west — center — east
           |
         south
```

如果：

```text
west  > center
east  > center
north > center
south > center
```

則：

```python
topo[j, i] = min(
    topo[j, i-1],
    topo[j, i+1],
    topo[j-1, i],
    topo[j+1, i],
)
```

流程：

```text
continuous height
      ↓
target-grid mean height
      ↓
convert to discrete vertical topo
      ↓
single-grid cave check
      ↓
final physical height
      ↓
mask
```

不要直接修改原始 10 m DEM。

---

# 1. Target resolutions

目前 preprocessing 必須支援：

```text
100 m
500 m
1000 m
1500 m
2000 m
```

程式內統一使用公尺：

```python
TARGET_DX_LIST = [
    100.0,
    500.0,
    1000.0,
    1500.0,
    2000.0,
]
```

不要針對每個解析度寫獨立程式。

核心 preprocessing functions 必須接受：

```python
target_dx
```

例如：

```python
def build_target_grid(master_ds, target_dx):
    ...

def aggregate_height(master_ds, target_grid, target_dx):
    ...

def aggregate_landcover(master_ds, target_grid, target_dx):
    ...

def calculate_land_fraction(master_ds, target_grid, target_dx):
    ...

def export_vvm(surface_ds, target_dx):
    ...

def export_vvmex(surface_ds, target_dx):
    ...
```

如此未來若要新增：

```text
250 m
750 m
3 km
4 km
```

只需修改：

```python
TARGET_DX_LIST
```

---

# 2. Source datasets

## Terrain

```text
USGS 3DEP 1/3 arc-second DEM
~10 m
```

master：

```text
Puerto_Rico_DEM_10m_master.tif
```

用於：

```text
height
slope
topo
mask
```

---

## Land cover

```text
ESA WorldCover 2021 v200
10 m
```

master：

```text
Puerto_Rico_LandCover_10m_master.tif
```

classes：

|  ID | Class                    |
| --: | ------------------------ |
|  10 | Tree cover               |
|  20 | Shrubland                |
|  30 | Grassland                |
|  40 | Cropland                 |
|  50 | Built-up                 |
|  60 | Bare / sparse vegetation |
|  70 | Snow and ice             |
|  80 | Permanent water          |
|  90 | Herbaceous wetland       |
|  95 | Mangroves                |
| 100 | Moss and lichen          |

---

# 3. Phase 1: combine the two TIFF datasets

```text
Puerto_Rico_DEM_10m_master.tif
                 +
Puerto_Rico_LandCover_10m_master.tif
                 ↓
           reprojection
                 ↓
           common grid
                 ↓
Puerto_Rico_surface_master.nc
```

建議 master：

```text
dimensions:
    y
    x

coordinates:
    x(x)
    y(y)
    lon(y,x)
    lat(y,x)

variables:
    height_raw(y,x)
    worldcover(y,x)
    land_mask(y,x)
    water_mask(y,x)
```

WorldCover 保留原始：

```text
10, 20, 30, ..., 100
```

---

# 4. Multi-resolution architecture

```text
                  SOURCE DATA

        USGS 3DEP              ESA WorldCover
           ~10 m                    10 m
             │                        │
             └─────────┬──────────────┘
                       ▼
               common spatial grid
                       │
                       ▼
        Puerto_Rico_surface_master.nc
                       │
        ┌──────────────┼────────────────────────────┐
        ▼              ▼              ▼             ▼
      100 m          500 m          1 km        1.5 km
                                                      
                                      └──────────────► 2 km
                       │
                       ▼
              model-independent
                 coarse fields
                       │
            ┌──────────┴──────────┐
            ▼                     ▼
           VVM                  VVMex
```

實際程式：

```python
for target_dx in TARGET_DX_LIST:
    build_surface_for_resolution(
        master_file="Puerto_Rico_surface_master.nc",
        target_dx=target_dx,
    )
```

---

# 5. Output naming

建議統一由 resolution 自動產生 filename。

```python
def resolution_label(dx):
    if dx < 1000:
        return f"{int(dx)}m"

    km = dx / 1000.0

    if km.is_integer():
        return f"{int(km)}km"

    return f"{km:g}km"
```

結果：

```text
100       → 100m
500       → 500m
1000      → 1km
1500      → 1.5km
2000      → 2km
```

因此：

```text
Puerto_Rico_surface_100m.nc
Puerto_Rico_surface_500m.nc
Puerto_Rico_surface_1km.nc
Puerto_Rico_surface_1.5km.nc
Puerto_Rico_surface_2km.nc
```

model outputs：

```text
Puerto_Rico_VVM_100m.nc
Puerto_Rico_VVM_500m.nc
Puerto_Rico_VVM_1km.nc
Puerto_Rico_VVM_1.5km.nc
Puerto_Rico_VVM_2km.nc
```

以及：

```text
Puerto_Rico_VVMex_100m.nc
Puerto_Rico_VVMex_500m.nc
Puerto_Rico_VVMex_1km.nc
Puerto_Rico_VVMex_1.5km.nc
Puerto_Rico_VVMex_2km.nc
```

---

# 6. Continuous versus categorical variables

## Continuous

```text
height
albedo
gvf
lai
shdmax
shdmin
```

使用：

```text
area mean / weighted mean
```

## Categorical

```text
worldcover
lu
vegtype
soiltype
slopetype
```

使用：

```text
fraction
mode
dominant category
```

不能平均 category number。

---

# 7. Terrain height reduction

從：

```text
USGS 3DEP ~10 m
```

產生：

```text
100 m
500 m
1 km
1.5 km
2 km
```

公式：

$$
H_{\mathrm{target}}
=
\frac{\sum_i A_i H_i}
{\sum_i A_i}
$$

概念上：

| Target resolution | 約略 source coverage |
| ----------------: | -----------------: |
|             100 m |           ~10 × 10 |
|             500 m |           ~50 × 50 |
|              1 km |         ~100 × 100 |
|            1.5 km |         ~150 × 150 |
|              2 km |         ~200 × 200 |

但這只是概念值。

實際 implementation 不應寫：

```python
reshape(..., 150, 150)
```

因為 3DEP 是 1/3 arc-second，不是精確 10 m Cartesian grid。

應按照真正的 spatial overlap 做 averaging。

---

# 8. Target grid generation

所有解析度最好使用同一個 domain origin。

例如：

```python
DOMAIN_XMIN = ...
DOMAIN_XMAX = ...
DOMAIN_YMIN = ...
DOMAIN_YMAX = ...
```

然後：

```python
nx = int(np.ceil((DOMAIN_XMAX - DOMAIN_XMIN) / target_dx))
ny = int(np.ceil((DOMAIN_YMAX - DOMAIN_YMIN) / target_dx))
```

但 VVM/VVMex domain 通常需要滿足 decomposition / GPU layout，因此可以再提供：

```python
NX_OVERRIDE = None
NY_OVERRIDE = None
```

或：

```python
GRID_MULTIPLE = 8
```

例如：

```python
def round_up(n, multiple):
    return ((n + multiple - 1) // multiple) * multiple
```

則：

```python
nx = round_up(nx, GRID_MULTIPLE)
ny = round_up(ny, GRID_MULTIPLE)
```

如此 1.5 km / 2 km 不會因 domain size 無法整除而產生奇怪 grid。

---

# 9. Land-cover reduction

每個 target grid 計算 WorldCover fractions：

```text
tree_fraction
shrub_fraction
grass_fraction
crop_fraction
urban_fraction
bare_fraction
snow_fraction
water_fraction
wetland_fraction
mangrove_fraction
moss_fraction
```

此方法對所有解析度完全一致：

```python
for dx in [100, 500, 1000, 1500, 2000]:
    fractions = calculate_worldcover_fraction(
        source_worldcover,
        target_dx=dx,
    )
```

例如 2 km cell：

```text
Tree       0.48
Grass      0.16
Urban      0.04
Cropland   0.08
Water      0.24
```

不要把 10 m classes 先逐級：

```text
10 m → 500 m → 1 km → 2 km
```

應全部直接：

```text
10 m → 100 m
10 m → 500 m
10 m → 1 km
10 m → 1.5 km
10 m → 2 km
```

避免 cumulative aggregation error。

---

# 10. Land / sea classification

保存：

```text
land_fraction
water_fraction
```

例如：

```python
water = water_fraction >= 0.5
land = ~water
```

相同 threshold 必須用於：

```text
100 m
500 m
1 km
1.5 km
2 km
```

這樣才能研究 coastline 隨解析度變化。

---

# 11. Main-island-only processing

建議在 **highest-resolution/common master grid** 先建立：

```text
main_island_mask
```

然後 target aggregation 都使用相同 mask：

```text
main_island_mask
      │
      ├── 100 m
      ├── 500 m
      ├── 1 km
      ├── 1.5 km
      └── 2 km
```

先在 master grid 固定 Puerto Rico 本島，避免各解析度重新選到原始離島：

```python
if MAIN_ISLAND_ONLY:
    worldcover[~main_island_mask] = WATER_CLASS
```

然後才進行 multi-resolution aggregation。

不過 water-fraction threshold 在 target grid 離散化後，仍可能把原本相連的
本島切出少量不連通陸格。因此每個 target resolution 聚合完成後，若
`MAIN_ISLAND_ONLY` 為 true，再做一次四鄰接 connected-component QA，只保留
最大的 target-grid component，其他 component 一致轉成 water（height、slope、
land fraction 設為 0，WorldCover fraction 設為 100% water）。清理前後的
component 與 cell 數必須寫入 QA。這個步驟只清除離散化產生的斷片，不會讓
master 階段已刪除的原始離島重新出現。

---

# 12. WorldCover → VVM/VVMex

推薦：

```text
WorldCover fractions
       ↓
map fractions to model taxonomy
       ↓
sum fractions
       ↓
dominant model class
```

### VVM

使用 USGS。

### VVMex

使用 Modified IGBP/MODIS Noah。

不能共用 integer。

---

# 13. WorldCover mapping

| WorldCover | Description        |    VVM USGS | VVMex IGBP |
| ---------: | ------------------ | ----------: | ---------: |
|         10 | Tree cover         |          13 |          2 |
|         20 | Shrubland          |           8 |          7 |
|         30 | Grassland          |           7 |         10 |
|         40 | Cropland           |           4 |         12 |
|         50 | Built-up           |           1 |         13 |
|         60 | Bare / sparse      |          19 |         16 |
|         70 | Snow / ice         |          24 |         15 |
|         80 | Water              |          16 |         17 |
|         90 | Herbaceous wetland |          17 |         11 |
|         95 | Mangroves          |          18 |         11 |
|        100 | Moss / lichen      | 20 fallback |   fallback |

---

# 14. Surface parameters

Continuous parameters：

```text
albedo
gvf
lai
shdmax
shdmin
```

應由 land-cover fractions weighted mean。

例如：

```text
60 % forest
40 % grass
```

$$
GVF=0.6(95)+0.4(80)=89\%
$$

這在粗解析度：

```text
1 km
1.5 km
2 km
```

尤其重要，因為一個 grid 內 land heterogeneity 更大。

不建議 2 km grid 只取 dominant class 後把所有 surface properties 設成該 class。

---

# 15. Soil

prototype 可暫時：

```text
forest / grass / crop → loam
bare                  → sandy loam
wetland               → organic
water                 → water
```

正式 production 應使用獨立 soil dataset。

對 1.5 km / 2 km：

```text
soil category
```

仍然使用 categorical mode，而不是平均 soil ID。

---

# 16. Slope

先從 high-resolution DEM 計算：

$$
S =
100\sqrt{
(\partial z/\partial x)^2+
(\partial z/\partial y)^2
}
$$

master file 保存：

```text
slope_percent
```

然後再依 model-specific table 產生：

```text
VVM slope
VVMex slopetype
```

不要：

```text
10 m slope → 500 m mode → 1 km mode → 2 km mode
```

而是每個 resolution 直接從 master high-resolution slope statistics 產生。

---

# 17. VVM slope classification

VVM：

| ID | Meaning      |
| -: | ------------ |
|  1 | 0–8%         |
|  2 | 8–30%        |
|  3 | >30%         |
|  4 | 0–30% mixed  |
|  5 | 0–8% + >30%  |
|  6 | 8–30% + >30% |
|  7 | all three    |
|  8 | unused       |
|  9 | glacial ice  |

因此 1.5 km / 2 km grid 應保存各 slope regime fraction，再決定 mixed category。

---

# 18. VVMex slope classification

目前 `generate_init_nc.py` 最後定義：

| ID | Slope    |
| -: | -------- |
|  1 | 0–8%     |
|  2 | 8–16%    |
|  3 | 16–30%   |
|  4 | 30–45%   |
|  5 | 45–60%   |
|  6 | 60–90%   |
|  7 | 90–120%  |
|  8 | 120–150% |
|  9 | >150%    |

VVM 與 VVMex slope integer 不能直接共用。

---

# 19. Topography discrete conversion

對每個 target：

```python
TARGET_DX_LIST = [
    100,
    500,
    1000,
    1500,
    2000,
]
```

依序：

```text
master DEM
    ↓
target-grid height
    ↓
VVM vertical coordinate
    ↓
topo integer level
    ↓
cave correction
    ↓
height reconstructed from topo
    ↓
mask
```

---

# 20. Cave check must run independently for every resolution

非常重要。

不能：

```text
100 m topo cave-check
       ↓
average
       ↓
500 m / 1 km / 2 km
```

因為 discrete cave 是 resolution-dependent。

必須：

```text
100 m height
→ topo
→ cave check

500 m height
→ topo
→ cave check

1 km height
→ topo
→ cave check

1.5 km height
→ topo
→ cave check

2 km height
→ topo
→ cave check
```

建議函式：

```python
def remove_single_grid_caves(topo, periodic=True):
    topo = topo.copy()

    ny, nx = topo.shape

    for j in range(ny):
        for i in range(nx):

            if periodic:
                west  = topo[j, (i - 1) % nx]
                east  = topo[j, (i + 1) % nx]
                south = topo[(j - 1) % ny, i]
                north = topo[(j + 1) % ny, i]
            else:
                if i == 0 or i == nx - 1 or j == 0 or j == ny - 1:
                    continue

                west  = topo[j, i - 1]
                east  = topo[j, i + 1]
                south = topo[j - 1, i]
                north = topo[j + 1, i]

            center = topo[j, i]

            if (
                west  > center
                and east  > center
                and south > center
                and north > center
            ):
                topo[j, i] = min(
                    west,
                    east,
                    south,
                    north,
                )

    return topo
```

---

# 21. Iterative cave check

建議提供：

```python
CAVE_CHECK_MAX_ITER = 10
```

因為修掉一個 cave 後可能改變鄰近格狀態。

```python
def remove_caves_until_stable(topo, periodic=True, max_iter=10):

    topo = topo.copy()

    for iteration in range(max_iter):

        old = topo.copy()

        topo = remove_single_grid_caves(
            topo,
            periodic=periodic,
        )

        if np.array_equal(old, topo):
            return topo, iteration + 1

    return topo, max_iter
```

並記錄：

```text
caves before
caves corrected
iterations
caves remaining
```

對 1.5 km / 2 km 特別值得做 QA，因為山谷更容易因 vertical discretization 形成 isolated grid-level depressions。

---

# 22. Model-independent outputs

產生：

```text
Puerto_Rico_surface_100m.nc
Puerto_Rico_surface_500m.nc
Puerto_Rico_surface_1km.nc
Puerto_Rico_surface_1.5km.nc
Puerto_Rico_surface_2km.nc
```

每個至少包含：

```text
x
y
lon
lat

height
slope_percent

worldcover_dominant

tree_fraction
shrub_fraction
grass_fraction
crop_fraction
urban_fraction
bare_fraction
water_fraction
wetland_fraction
mangrove_fraction

land_fraction
```

---

# 23. VVM fields

VVM exporter 產生：

```text
lon
lat
lev
time

mask
topo
height

lu
soil
slope

albedo
gvf
lai
shdmax
shdmin
```

其中：

```text
lu = USGS
```

---

# 24. VVMex fields

重新核對 `generate_init_nc.py`：

```text
lon
lat

topo
mask
height
sea_land_ice_mask

vegtype
soiltype
slopetype

Tg

albedo
gvf
lai
shdmax
shdmin

sm1
sm2
sm3
sm4

sl1
sl2
sl3
sl4
```

其中：

```text
vegtype   = IGBP/MODIS Noah
soiltype  = STATSGO 19 category
slopetype = VVMex 9-bin slope category
```

---

# 25. Program structure

建議：

```text
01_build_master_surface.py
02_coarsen_surface.py
03_export_vvm.py
04_export_vvmex.py
```

---

## `01_build_master_surface.py`

輸入：

```text
Puerto_Rico_DEM_10m_master.tif
Puerto_Rico_LandCover_10m_master.tif
```

輸出：

```text
Puerto_Rico_surface_master.nc
```

功能：

```text
reprojection
common grid
main-island identification
DEM/WorldCover alignment
QA
```

---

## `02_coarsen_surface.py`

不 hard-code resolution。

```python
TARGET_DX_LIST = [
    100.0,
    500.0,
    1000.0,
    1500.0,
    2000.0,
]

for target_dx in TARGET_DX_LIST:

    output = build_coarse_surface(
        master_file="Puerto_Rico_surface_master.nc",
        target_dx=target_dx,
    )

    output.to_netcdf(
        f"Puerto_Rico_surface_{resolution_label(target_dx)}.nc"
    )
```

功能：

```text
height area mean

WorldCover fractions

land/water fractions

slope statistics

lon/lat generation
```

---

## `03_export_vvm.py`

支援：

```bash
python 03_export_vvm.py \
    --input Puerto_Rico_surface_1.5km.nc
```

或 batch：

```python
for target_dx in TARGET_DX_LIST:

    label = resolution_label(target_dx)

    export_vvm(
        input_file=f"Puerto_Rico_surface_{label}.nc",
        output_file=f"Puerto_Rico_VVM_{label}.nc",
    )
```

執行：

```text
WorldCover → USGS

soil mapping

VVM slope classification

VEGPARM parameters

height → topo

cave check

topo → final height

mask
```

---

## `04_export_vvmex.py`

同樣：

```python
for target_dx in TARGET_DX_LIST:

    label = resolution_label(target_dx)

    export_vvmex(
        input_file=f"Puerto_Rico_surface_{label}.nc",
        output_file=f"Puerto_Rico_VVMex_{label}.nc",
    )
```

執行：

```text
WorldCover → IGBP

soiltype

VVMex slopetype

Tg
albedo
gvf
lai
shdmax
shdmin

soil moisture initialization

height → topo

cave check

height reconstruction

sea_land_ice_mask
mask
```

---

# 26. Command-line interface

建議不要只接受單一：

```bash
--dx 500
```

而是支援多個：

```bash
python 02_coarsen_surface.py \
    --dx 100 500 1000 1500 2000
```

Python：

```python
parser.add_argument(
    "--dx",
    type=float,
    nargs="+",
    default=[100, 500, 1000, 1500, 2000],
    help="Target horizontal resolutions in meters",
)
```

然後：

```python
for target_dx in args.dx:
    ...
```

---

# 27. Suggested full command

例如：

```bash
python 01_build_master_surface.py \
    --dem Puerto_Rico_DEM_10m_master.tif \
    --landcover Puerto_Rico_LandCover_10m_master.tif \
    --output Puerto_Rico_surface_master.nc \
    --main-island-only
```

接著：

```bash
python 02_coarsen_surface.py \
    --input Puerto_Rico_surface_master.nc \
    --dx 100 500 1000 1500 2000
```

再：

```bash
python 03_export_vvm.py \
    --input-pattern "Puerto_Rico_surface_*.nc" \
    --check-topo-caves \
    --periodic-topo
```

與：

```bash
python 04_export_vvmex.py \
    --input-pattern "Puerto_Rico_surface_*.nc" \
    --check-topo-caves \
    --periodic-topo
```

---

# 28. Suggested configuration

```python
CONFIG = {

    "dem_file":
        "Puerto_Rico_DEM_10m_master.tif",

    "landcover_file":
        "Puerto_Rico_LandCover_10m_master.tif",

    "master_output":
        "Puerto_Rico_surface_master.nc",

    "target_dx":
        [
            100.0,
            500.0,
            1000.0,
            1500.0,
            2000.0,
        ],

    "main_island_only":
        True,

    "water_fraction_threshold":
        0.5,

    "topo_cave_check":
        True,

    "topo_periodic_check":
        True,

    "cave_check_max_iter":
        10,

    "models":
        [
            "vvm",
            "vvmex",
        ],
}
```

---

# 29. QA for every resolution

每個：

```text
100 m
500 m
1 km
1.5 km
2 km
```

都要輸出：

```text
NX
NY
DX
DY

min height
max height
mean height

land cells
water cells

land area
water area

WorldCover fractions

unique VVM lu
unique VVMex vegtype

unique soil types
unique slope types

single-grid caves before correction
single-grid caves corrected
single-grid caves remaining
```

---

# 30. Cross-resolution QA

除了單一解析度 QA，再做 resolution comparison：

```text
100 m → reference

500 m
1 km
1.5 km
2 km
```

比較：

```text
total land area
total water area
mean island elevation
maximum island elevation

forest fraction
urban fraction
grass fraction

coastline representation

mean GVF
mean LAI
mean albedo
```

例如：

```text
Resolution  Land Area   Mean H   Forest %
100 m       ...
500 m       ...
1 km        ...
1.5 km      ...
2 km        ...
```

主要檢查 coarse resolution 是否因 coastline representation 造成不合理 land-area drift。

---

# 31. Important rule for 1.5 km

`1.5 km` 不要假設：

```text
1.5 km = 3 × 500 m
```

雖然數學上成立，但 preprocessing 不應直接從 500 m file aggregate。

應該：

```text
10 m master
      ↓
directly
      ↓
1.5 km
```

同理：

```text
2 km
```

也不要：

```text
1 km → 2 km
```

全部 resolution 應直接從共同 master source 建立。

這樣：

```text
100 m
500 m
1 km
1.5 km
2 km
```

才是真正平行的 resolution experiments。

---

# 32. Final workflow

```text
              USGS 3DEP ~10 m
                     +
             WorldCover 10 m
                     │
                     ▼
        align / project / combine
                     │
                     ▼
      Puerto_Rico_surface_master.nc
                     │
              MAIN_ISLAND_ONLY
                     │
                     ▼
        high-resolution common data
                     │
       ┌─────────────┼──────────────┬────────────┐
       ▼             ▼              ▼            ▼
     100 m         500 m          1 km         1.5 km
                                                   │
                                                   ▼
                                                  2 km
       │             │              │             │
       └─────────────┴───────┬──────┴─────────────┘
                             ▼
                 model-independent surface
                             │
                 ┌───────────┴───────────┐
                 ▼                       ▼
                VVM                    VVMex
                 │                       │
               USGS                     IGBP
                 │                       │
             VVM slope              VVMex slope
                 │                       │
                 └───────────┬───────────┘
                             ▼
                      height → topo
                             │
                             ▼
                    single-grid cave check
                             │
                             ▼
                       final height
                             │
                             ▼
                           mask
```

---

# 33. Main design decisions

1. 使用共同的 ~10 m master geographic datasets。

2. 支援：

```text
100 m
500 m
1 km
1.5 km
2 km
```

3. 程式不得 hard-code 三個解析度，使用：

```python
TARGET_DX_LIST
```

4. 每個解析度都**直接從 master source 建立**，不得逐級 coarsening。

5. DEM 使用 area-weighted averaging。

6. Land cover 保存 class fractions。

7. VVM 和 VVMex 分別產生 land taxonomy。

8. VVM = USGS。

9. VVMex = Modified IGBP/MODIS Noah。

10. VVM 與 VVMex slope category 分開產生。

11. `MAIN_ISLAND_ONLY` 在高解析度 master 階段確定 Puerto Rico main island。

12. 所有解析度共用相同 main-island definition。

13. `height → topo` 後，每個解析度都獨立進行 single-grid cave check。

14. 建議 cave check 支援 periodic boundaries。

15. cave-corrected `topo` 後重新建立 physical `height` 與 `mask`。

16. 保存 `water_fraction`、`land_fraction` 與所有主要 WorldCover fractions。

17. 1.5 km 與 2 km 應視為與 100/500 m/1 km 平行的正式 resolution experiments，而不是從較粗或較細 simulation grid 再加工。

18. 所有 output filename 自動從 `target_dx` 產生，避免解析度新增後手動修改大量程式。



還有想要default, 讓本島預設是在正中間. 
