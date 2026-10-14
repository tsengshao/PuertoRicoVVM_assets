# Puerto Rico VVM/VVMex Topography Builder

`build_puerto_rico_topography.py` converts the Puerto Rico USGS 3DEP DEM and
ESA WorldCover GeoTIFFs into model-independent surface files and topography
initialization files for VVM and VVMex.

The parameter calculations and current lookup tables are summarized in
[`SURFACE_PARAMETER_METHODS.md`](SURFACE_PARAMETER_METHODS.md).

The implementation follows [`topo_strategy.md`](topo_strategy.md), VVM's
[`CODE/create_topolsm.F`](https://github.com/chienmingwu/VVM/blob/master/CODE/create_topolsm.F),
and VVMex's
[`tools/generate_init_nc.py`](https://github.com/Aaron-Hsieh-0129/VVMex/blob/main/tools/generate_init_nc.py).

## Inputs

The default inputs are:

```text
Puerto_Rico_3DEP_10m/Puerto_Rico_DEM_10m_master.tif
Puerto_Rico_LandCover_10m/Puerto_Rico_LandCover_10m_master.tif
```

The DEM is USGS 3DEP elevation data. The land-cover raster is ESA WorldCover
2021 v200 with the original class IDs retained in the master and surface
files.

An existing model-independent master can be supplied instead:

```json
"inputs": {
  "surface_master": "Puerto_Rico_topography_output/Puerto_Rico_surface_master.nc"
}
```

Leave `surface_master` as `null` to build it from the two TIFF files.

## Environment

Use the existing `gdal` mamba environment:

```bash
mamba activate gdal
python build_puerto_rico_topography.py
```

If `mamba` is unavailable in a non-interactive shell, invoke the environment's
Python directly:

```bash
/home/C.shaoyu/miniforge3/envs/gdal/bin/python \
  build_puerto_rico_topography.py
```

Required Python packages are GDAL, NumPy, SciPy, netCDF4, and pyproj.

## Default run

The default command automatically reads
[`config_topo_lev45.json`](config_topo_lev45.json). The resolutions and output
directory are taken from that file:

```bash
python build_puerto_rico_topography.py
```

Outputs are written to the directory selected by `output.directory` in the
JSON. Existing files with the same names are replaced.

The default processing settings are:

| Setting | Default |
|---|---:|
| Master grid spacing | From JSON |
| Target grid spacing | From JSON |
| Main island only | Yes |
| Ocean padding | 25 km |
| Domain size | From JSON |
| Grid-size multiple | From JSON |
| Water threshold | 0.5 |
| Periodic cave check | Yes |
| Maximum cave iterations | 10 |
| VVM vertical levels | 45, including the bottom floor |
| VVMex vertical levels | 44, with the bottom floor omitted |
| `dz` / `dz1` | 500 m / 100 m |
| Vertical coordinate | From JSON |

## JSON configuration

All normal pipeline settings are controlled by
`config_topo_lev45.json`. To build only 500 m and 1 km products,
edit this part of the file:

```json
"horizontal_grid": {
  "target_dx_m": [500.0, 1000.0]
}
```

Then run:

```bash
python build_puerto_rico_topography.py \
  --config config_topo_lev45.json
```

The JSON sections control:

| Section | Settings |
|---|---|
| `inputs` | DEM, WorldCover, and optional reusable surface-master paths |
| `output` | Output directory and master-file switch |
| `models` | `vvm`, `vvmex`, or both |
| `horizontal_grid` | Target resolutions, master resolution, padding, domain size, and grid multiple |
| `land_surface` | Main-island switch and water threshold |
| `vertical_grid` | `nz`, `dz1_m`, `dz_m`, stretching, and VVMex floor removal |
| `topography` | Periodic cave check and iteration limit |

Paths in the JSON are resolved relative to the JSON file. An unknown section
or option is reported as an error so that misspelled settings are not ignored.

`water_fraction_threshold` controls the target-grid coastline. The script
first calculates the fraction of WorldCover water pixels in each target cell,
then applies:

```text
water_fraction >= water_fraction_threshold  -> water
water_fraction <  water_fraction_threshold  -> land
```

The usual value `0.5` is a majority rule. Increasing the threshold retains
more coastal cells as land; decreasing it classifies more coastal cells as
water. In the model-independent surface file these cells remain water with
`height=0`. When VVM/VVMex topography is built, exterior ocean receives
`topo=0`, while enclosed lakes and narrow inland river segments receive an
initial `topo=2` and participate in cave correction. Their water land-use
category is preserved.

Command-line options override the JSON for one run. For example, this uses the
JSON for everything except the target resolutions:

```bash
python build_puerto_rico_topography.py \
  --config config_topo_lev45.json \
  --dx 500 1000
```

## Reusing a surface master

Set `inputs.surface_master` to reuse a previously generated
`Puerto_Rico_surface_master.nc`. For example:

```json
"inputs": {
  "dem": "Puerto_Rico_3DEP_10m/Puerto_Rico_DEM_10m_master.tif",
  "landcover": "Puerto_Rico_LandCover_10m/Puerto_Rico_LandCover_10m_master.tif",
  "surface_master": "Puerto_Rico_topography_output/Puerto_Rico_surface_master.nc"
}
```

In reuse mode the script reads `height_raw`, `slope_percent`, `worldcover`,
and `land_mask` from that file. It skips TIFF warping, domain discovery,
padding, slope calculation, and writing another master. The reused master
therefore determines the domain, projection, master spacing, and
main-island selection. If corresponding JSON values differ, the script prints
a warning rather than silently applying them.

The target resolutions, `water_fraction_threshold`, land-surface mappings,
vertical grid, and selected models are still applied for the new run. Each
target spacing must be an integer multiple of the reused master spacing, and
the master dimensions must divide exactly into the target grid.

The same mode can be selected from the command line:

```bash
python build_puerto_rico_topography.py \
  --surface-master Puerto_Rico_topography_output/Puerto_Rico_surface_master.nc
```

## Output files

The pipeline writes one high-resolution master file:

```text
Puerto_Rico_surface_master.nc
```

For every requested resolution it writes:

```text
Puerto_Rico_surface_<resolution>.nc
Puerto_Rico_VVM_<resolution>.nc
Puerto_Rico_VVMex_<resolution>.nc
```

For example, `<resolution>` is `500m`, `1km`, or `1.5km`, depending on the
requested `target_dx_m` values.

It also writes:

```text
Puerto_Rico_topography.log
Puerto_Rico_topography_QA.json
```

The log records each processing phase and all warnings. An error is printed
with its traceback and causes a non-zero exit status; processing errors are not
silently ignored.

### Master surface

`Puerto_Rico_surface_master.nc` contains:

```text
x, y, lon, lat
height_raw
slope_percent
worldcover
land_mask
water_mask
```

### Coarse surface files

Each model-independent surface file contains:

```text
x, y, lon, lat
height
slope_percent
worldcover_dominant
land_fraction
tree_fraction, shrub_fraction, grass_fraction, crop_fraction
urban_fraction, bare_fraction, snow_fraction, water_fraction
wetland_fraction, mangrove_fraction, moss_fraction
```

Every target resolution is aggregated directly from the common 10 m master.
The 1.5 km product is not derived from 500 m, and the 2 km product is not
derived from 1 km.

### VVM files

VVM files use the `NETCDF3_64BIT_OFFSET` format and contain:

```text
lon, lat, lev, time
mask, topo, height
lu, soil, slope
albedo, gvf, lai, shdmax, shdmin
```

`lu` uses the VVM USGS land-use classes.

### VVMex files

VVMex files use NetCDF4 and contain:

```text
nx, ny, nz, time
lon, lat
topo, mask, height, sea_land_ice_mask
vegtype, soiltype, slopetype
Tg
albedo, gvf, lai, shdmax, shdmin
sm1, sm2, sm3, sm4
sl1, sl2, sl3, sl4
```

`vegtype` uses Modified IGBP/MODIS Noah classes.

## Topography indexing

Terrain height is averaged on each target grid before it is converted to a
discrete vertical level. The script then converts the zero-based vertical
index to the model index and adds one grid to every land cell:

```text
ocean topo = 0
minimum land topo = 2
```

VVM and VVMex use the same two-dimensional `topo` numbering. When
`vvmex_drop_bottom_level=true`, the script removes the VVM bottom floor from
the VVMex vertical coordinate and shifts only the three-dimensional mask by
one level. The two-dimensional `topo` values do not change.

The single-grid cave correction is run independently for every resolution and
model. Only land cells are eligible for correction, and ocean topo is reset to
zero after correction. The number of caves before correction, corrected cells,
iterations, and remaining caves is recorded in the QA JSON.

## Coordinate axes

All coordinate variables carry explicit axis attributes:

| File | Axis attributes |
|---|---|
| Master/surface | `x` and `lon`: `axis="X"`; `y` and `lat`: `axis="Y"` |
| VVM | `lon`: `X`; `lat`: `Y`; `lev`: `Z`; `time`: `T` |
| VVMex | `nx` and `lon`: `X`; `ny` and `lat`: `Y`; `nz`: `Z`; `time`: `T` |

The common grid uses a local equidistant cylindrical projection with metre
coordinates. This preserves rectilinear longitude and latitude coordinates for
VVM while allowing all aggregation to use metre-based grid spacing.

## Main-island placement

Main-island-only mode is enabled by default. The script identifies land from
WorldCover, applies four-neighbour connected-component labeling, and retains
the largest component. It does not use a hand-written geographic bounding box.

After aggregation to every requested VVM/VVMex resolution, the script repeats
the four-neighbour check. If the water-fraction discretization has created
detached land components, only the largest target-grid component is retained;
the detached cells are converted consistently to water in the surface and both
model products. Counts and removed area are written to the QA JSON under
`post_regrid_main_island_cleanup`. This second check runs only when
`main_island_only` is enabled.

The main island's bounding-box center is placed at the center of the model
domain. Domain dimensions are expanded so that every requested resolution has
an integer grid size divisible by `--grid-multiple`.

Keep all islands with:

```bash
python build_puerto_rico_topography.py --all-islands
```

## Vertical-grid configuration

Topography indices depend on the model's vertical grid. Configure it in the
`vertical_grid` section of `config_topo_lev45.json`:

```json
{
  "vertical_grid": {
    "nz": 45,
    "dz1_m": 100.0,
    "dz_m": 500.0,
    "stretching_domain_m": 15000.0,
    "coordinate_type": "default",
    "vvmex_drop_bottom_level": true
  }
}
```

`nz` is the number of VVM levels including its bottom floor. With `nz=45` and
`vvmex_drop_bottom_level=true`, the VVM file has 45 levels and the VVMex file
has 44 levels. The VVMex vertical coordinate is the VVM coordinate with its
bottom floor removed.

`dz1_m` is the fine spacing used in the lowest part of the atmosphere. `dz_m`
is the nominal coarse spacing used by the stretched upper grid.
`stretching_domain_m` controls the height scale of the stretching formula.
With `coordinate_type="default"`, the grid smoothly stretches from `dz1_m`
toward `dz_m`. With `coordinate_type="taiwanvvm"`, the lowest part instead
uses fixed `dz1_m` spacing before the stretched grid.

The supported vertical coordinate types are `default` and `taiwanvvm`. The
same settings can also be supplied directly on the command line:

```bash
python build_puerto_rico_topography.py \
  --nz 45 \
  --dz1 100 \
  --dz 500 \
  --vertical-domain 15000 \
  --vertical-coordinate taiwanvvm \
  --vvmex-drop-bottom-level
```

## Common command examples

Build only 500 m and 2 km products:

```bash
python build_puerto_rico_topography.py --dx 500 2000
```

Build only VVM files:

```bash
python build_puerto_rico_topography.py --models vvm
```

Select another output directory and omit the large master NetCDF:

```bash
python build_puerto_rico_topography.py \
  --output-dir output_no_master \
  --no-write-master
```

Set an explicit centered domain:

```bash
python build_puerto_rico_topography.py \
  --domain-width-km 240 \
  --domain-height-km 144
```

The explicit dimensions must contain the selected land and padding, must be
divisible by every requested grid spacing, and must satisfy
`--grid-multiple`.

Show every option and its current default:

```bash
python build_puerto_rico_topography.py --help
```

## Land-cover mapping

| WorldCover | Description | VVM USGS | VVMex IGBP |
|---:|---|---:|---:|
| 10 | Tree cover | 13 | 2 |
| 20 | Shrubland | 8 | 7 |
| 30 | Grassland | 7 | 10 |
| 40 | Cropland | 4 | 12 |
| 50 | Built-up | 1 | 13 |
| 60 | Bare/sparse vegetation | 19 | 16 |
| 70 | Snow and ice | 24 | 15 |
| 80 | Permanent water | 16 | 17 |
| 90 | Herbaceous wetland | 17 | 11 |
| 95 | Mangroves | 18 | 11 |
| 100 | Moss and lichen | 20 | 19 |

Categorical fields are selected from class fractions; category IDs are never
averaged. Continuous surface parameters are weighted by WorldCover fractions.

## Current data limitations

Only the DEM and WorldCover inputs are available, so several land-surface
fields use explicit prototype defaults:

- Soil type uses the mapping described in `topo_strategy.md`; production runs
  should use an independent soil dataset.
- `Tg` is initialized to 300 K.
- VVMex soil moisture and liquid soil moisture are initialized to 0.3054033
  m3/m3 in all four layers.
- `shdmax` and `shdmin` use representative WorldCover green vegetation
  fraction because no seasonal climatology was supplied.
- PROJ network access is disabled. The NAD83 DEM is transformed using locally
  available or ballpark NAD83/WGS84 operations, which introduces metre-scale
  horizontal uncertainty.

These conditions are emitted as warnings in `Puerto_Rico_topography.log`.

## Resource use

The master grid covers the complete configured domain at `master_dx_m`.
Building a large domain therefore requires substantial memory and temporary
array space. `max_master_cells` stops the run before creating a master grid
larger than its configured safety limit.

For example, a 512 km by 512 km domain at 10 m spacing would contain
51,200 by 51,200 cells, or about 2.62 billion cells. This is permitted only if
`max_master_cells` is set above 2.62 billion, but it still requires very large
memory and storage. A 512 km domain is divisible by a 1,600 m target spacing
(320 target cells), while it is not divisible by a 1,500 m target spacing.

Example lightweight smoke run:

```bash
python build_puerto_rico_topography.py \
  --dx 2000 \
  --master-dx 500 \
  --analysis-dx 500 \
  --no-write-master \
  --output-dir /tmp/puerto_rico_topography_smoke
```

The smoke command verifies the workflow but does not preserve the spatial
detail needed for production output.
