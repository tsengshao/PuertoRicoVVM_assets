#!/usr/bin/env python3
"""Build Puerto Rico surface, VVM TOPO, and VVMex spatial NetCDF files.

The implementation follows topo_strategy.md and the topography conventions in:
  * VVM CODE/create_topolsm.F
  * VVMex tools/generate_init_nc.py

Run with the Python in the ``gdal`` mamba environment.  The program logs every
phase, emits warnings for prototype fields, and exits non-zero with a traceback
when an operation fails.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

# This host may not have network access to PROJ's optional NAD83 grid service.
# Force deterministic local transformations; the pipeline logs the datum caveat.
os.environ.setdefault("PROJ_NETWORK", "OFF")

from osgeo import gdal, osr
from scipy import ndimage

try:
    from pyproj import CRS, Transformer
except ImportError as exc:  # pragma: no cover - checked explicitly at startup
    raise SystemExit("ERROR: pyproj is required in the gdal environment") from exc


TARGET_DX_LIST = [100.0, 500.0, 1000.0, 1500.0, 2000.0]
WORLD_COVER_CLASSES = np.array([10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 100], dtype=np.uint8)
FRACTION_NAMES = [
    "tree_fraction", "shrub_fraction", "grass_fraction", "crop_fraction",
    "urban_fraction", "bare_fraction", "snow_fraction", "water_fraction",
    "wetland_fraction", "mangrove_fraction", "moss_fraction",
]

# WorldCover -> model classes, as specified by topo_strategy.md.
VVM_USGS = np.array([13, 8, 7, 4, 1, 19, 24, 16, 17, 18, 20], dtype=np.int32)
VVMEX_IGBP = np.array([2, 7, 10, 12, 13, 16, 15, 17, 11, 11, 19], dtype=np.int32)

# Prototype soil mapping requested by topo_strategy.md: loam, sandy loam,
# organic, water, and land ice. Both models use these IDs for the listed types.
SOIL_CLASS = np.array([6, 6, 6, 6, 6, 3, 16, 14, 13, 13, 6], dtype=np.int32)

# Representative parameters indexed by WorldCover class. They come from the
# USGS / MODIFIED_IGBP_MODIS_NOAH VEGPARM values and the examples in
# VVMex/tools/generate_init_nc.py. Percent fields remain in percent.
ALBEDO = np.array([12, 25, 19, 19, 15, 38, 70, 8, 14, 12, 16], dtype=np.float32)
GVF = np.array([95, 70, 80, 80, 10, 1, 0, 0, 60, 95, 60], dtype=np.float32)
LAI = np.array([6.48, 2.0, 4.0, 4.0, 1.0, 0.1, 0.01, 0.0, 4.0, 6.48, 2.0], dtype=np.float32)

LOG = logging.getLogger("puerto_rico_topography")


def config_defaults(path: Path) -> dict:
    """Load the user-facing JSON structure and map it to argparse names."""
    with path.open(encoding="utf-8") as handle:
        config = json.load(handle)
    allowed_top = {"inputs", "output", "models", "horizontal_grid", "land_surface", "vertical_grid", "topography"}
    unknown_top = set(config) - allowed_top
    if unknown_top:
        raise ValueError(f"unknown top-level configuration keys: {sorted(unknown_top)}")
    inputs = config.get("inputs", {})
    output = config.get("output", {})
    horizontal = config.get("horizontal_grid", {})
    surface = config.get("land_surface", {})
    vertical = config.get("vertical_grid", {})
    topography = config.get("topography", {})
    sections = {
        "inputs": (inputs, {"dem", "landcover", "surface_master"}),
        "output": (output, {"directory", "write_master"}),
        "horizontal_grid": (horizontal, {"target_dx_m", "master_dx_m", "analysis_dx_m", "padding_km",
                                         "domain_width_km", "domain_height_km", "grid_multiple", "max_master_cells"}),
        "land_surface": (surface, {"main_island_only", "water_fraction_threshold"}),
        "vertical_grid": (vertical, {"nz", "dz1_m", "dz_m", "stretching_domain_m", "coordinate_type",
                                     "vvmex_drop_bottom_level"}),
        "topography": (topography, {"periodic_cave_check", "cave_check_max_iterations"}),
    }
    for section_name, (section, allowed) in sections.items():
        if not isinstance(section, dict):
            raise TypeError(f"{section_name} must be a JSON object")
        unknown = set(section) - allowed
        if unknown:
            raise ValueError(f"unknown keys in {section_name}: {sorted(unknown)}")
    mapped = {}

    def relative_path(value):
        candidate = Path(value)
        return candidate if candidate.is_absolute() else path.parent / candidate

    def put(target, section, key, transform=lambda value: value):
        if key in section:
            mapped[target] = transform(section[key])

    put("dem", inputs, "dem", relative_path)
    put("landcover", inputs, "landcover", relative_path)
    put("surface_master", inputs, "surface_master",
        lambda value: None if value is None else relative_path(value))
    put("output_dir", output, "directory", relative_path)
    put("write_master", output, "write_master", bool)
    if "models" in config:
        mapped["models"] = list(config["models"])
    put("dx", horizontal, "target_dx_m", lambda values: [float(value) for value in values])
    put("master_dx", horizontal, "master_dx_m", float)
    put("analysis_dx", horizontal, "analysis_dx_m", float)
    put("padding_km", horizontal, "padding_km", float)
    put("domain_width_km", horizontal, "domain_width_km", lambda value: None if value is None else float(value))
    put("domain_height_km", horizontal, "domain_height_km", lambda value: None if value is None else float(value))
    put("grid_multiple", horizontal, "grid_multiple", int)
    put("max_master_cells", horizontal, "max_master_cells", int)
    put("main_island_only", surface, "main_island_only", bool)
    put("water_threshold", surface, "water_fraction_threshold", float)
    put("nz", vertical, "nz", int)
    put("dz1", vertical, "dz1_m", float)
    put("dz", vertical, "dz_m", float)
    put("vertical_domain", vertical, "stretching_domain_m", float)
    put("vertical_coordinate", vertical, "coordinate_type", str)
    put("vvmex_drop_bottom_level", vertical, "vvmex_drop_bottom_level", bool)
    put("periodic_topo", topography, "periodic_cave_check", bool)
    put("cave_max_iter", topography, "cave_check_max_iterations", int)
    return mapped


def resolution_label(dx: float) -> str:
    if dx < 1000:
        return f"{int(dx)}m" if dx.is_integer() else f"{dx:g}m"
    km = dx / 1000.0
    return f"{int(km)}km" if km.is_integer() else f"{km:g}km"


def round_up(value: float, quantum: float) -> float:
    return math.ceil(value / quantum - 1.0e-12) * quantum


def integer_lcm(values: list[float]) -> int:
    integers = []
    for value in values:
        rounded = round(value)
        if not math.isclose(value, rounded, abs_tol=1.0e-8):
            raise ValueError("All --dx and --master-dx values must be whole meters")
        integers.append(int(rounded))
    return math.lcm(*integers)


def setup_logging(output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "Puerto_Rico_topography.log"
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    file_handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    file_handler.setFormatter(formatter)
    LOG.setLevel(logging.INFO)
    LOG.handlers[:] = [stream, file_handler]
    return log_path


def raster_center_lonlat(path: Path) -> tuple[float, float]:
    ds = gdal.Open(str(path))
    if ds is None:
        raise FileNotFoundError(path)
    gt = ds.GetGeoTransform()
    px = ds.RasterXSize / 2.0
    py = ds.RasterYSize / 2.0
    x = gt[0] + px * gt[1] + py * gt[2]
    y = gt[3] + px * gt[4] + py * gt[5]
    src = osr.SpatialReference(wkt=ds.GetProjection())
    if src.IsGeographic():
        # Both current inputs use Greenwich longitude/latitude. NAD83 and WGS84
        # differ by metre scale here, below the 10 m source resolution.
        return float(x), float(y)
    dst = osr.SpatialReference()
    dst.ImportFromEPSG(4326)
    src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    dst.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    lon, lat, _ = osr.CoordinateTransformation(src, dst).TransformPoint(x, y)
    return lon, lat


def local_eqc_wkt(lon_0: float, lat_ts: float) -> str:
    proj = (
        f"+proj=eqc +lat_ts={lat_ts:.12g} +lat_0=0 +lon_0={lon_0:.12g} "
        "+datum=WGS84 +units=m +no_defs"
    )
    return CRS.from_proj4(proj).to_wkt()


def densified_raster_bounds(path: Path, dst_wkt: str) -> tuple[float, float, float, float]:
    ds = gdal.Open(str(path))
    gt = ds.GetGeoTransform()
    src = osr.SpatialReference(wkt=ds.GetProjection())
    dst = osr.SpatialReference(wkt=dst_wkt)
    src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    dst.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    transform = osr.CoordinateTransformation(src, dst)
    points = []
    for t in np.linspace(0.0, 1.0, 41):
        for px, py in ((t * ds.RasterXSize, 0), (t * ds.RasterXSize, ds.RasterYSize),
                       (0, t * ds.RasterYSize), (ds.RasterXSize, t * ds.RasterYSize)):
            x = gt[0] + px * gt[1] + py * gt[2]
            y = gt[3] + px * gt[4] + py * gt[5]
            points.append(transform.TransformPoint(x, y)[:2])
    xy = np.asarray(points)
    return float(xy[:, 0].min()), float(xy[:, 1].min()), float(xy[:, 0].max()), float(xy[:, 1].max())


def warp_array(
    source: Path,
    dst_wkt: str,
    bounds: tuple[float, float, float, float],
    width: int,
    height: int,
    resampling: str,
    output_type: int,
    src_nodata: float | int | None,
    dst_nodata: float | int,
) -> np.ndarray:
    options = gdal.WarpOptions(
        format="MEM", dstSRS=dst_wkt, outputBounds=bounds,
        width=width, height=height, resampleAlg=resampling,
        outputType=output_type, srcNodata=src_nodata, dstNodata=dst_nodata,
        multithread=True, warpOptions=["NUM_THREADS=ALL_CPUS"],
    )
    result = gdal.Warp("", str(source), options=options)
    if result is None:
        raise RuntimeError(f"GDAL Warp failed for {source}")
    array = result.GetRasterBand(1).ReadAsArray()
    result = None
    return array


def component_info(land: np.ndarray) -> tuple[np.ndarray, dict[str, int]]:
    structure = ndimage.generate_binary_structure(2, 1)  # 4-neighbour connectivity
    labels, count = ndimage.label(land, structure=structure)
    if count == 0:
        raise ValueError("No land component was found in WorldCover")
    sizes = np.bincount(labels.ravel())
    sizes[0] = 0
    label_id = int(np.argmax(sizes))
    rows, cols = np.where(labels == label_id)
    info = {
        "label": label_id, "pixels": int(sizes[label_id]), "components": int(count),
        "row_min": int(rows.min()), "row_max": int(rows.max()),
        "col_min": int(cols.min()), "col_max": int(cols.max()),
    }
    return labels, info


def discover_domain(args: argparse.Namespace, dst_wkt: str) -> tuple[tuple[float, float, float, float], dict]:
    dem_bounds = densified_raster_bounds(args.dem, dst_wkt)
    analysis_dx = args.analysis_dx
    width = math.ceil((dem_bounds[2] - dem_bounds[0]) / analysis_dx)
    height = math.ceil((dem_bounds[3] - dem_bounds[1]) / analysis_dx)
    bounds = (dem_bounds[0], dem_bounds[1], dem_bounds[0] + width * analysis_dx,
              dem_bounds[1] + height * analysis_dx)
    LOG.info("Finding the main island on a %.0f m analysis grid (%d x %d)", analysis_dx, width, height)
    wc = warp_array(args.landcover, dst_wkt, bounds, width, height, "near", gdal.GDT_Byte, 0, 0)
    land = np.isin(wc, WORLD_COVER_CLASSES[WORLD_COVER_CLASSES != 80])
    labels, main = component_info(land)
    LOG.info("WorldCover has %d land components; largest has %.2f km2 on analysis grid",
             main["components"], main["pixels"] * analysis_dx**2 / 1.0e6)

    # Array rows run north to south under GDAL.
    main_xmin = bounds[0] + main["col_min"] * analysis_dx
    main_xmax = bounds[0] + (main["col_max"] + 1) * analysis_dx
    main_ymax = bounds[3] - main["row_min"] * analysis_dx
    main_ymin = bounds[3] - (main["row_max"] + 1) * analysis_dx
    center_x = 0.5 * (main_xmin + main_xmax)
    center_y = 0.5 * (main_ymin + main_ymax)

    selected = labels == main["label"] if args.main_island_only else land
    rows, cols = np.where(selected)
    selected_xmin = bounds[0] + cols.min() * analysis_dx
    selected_xmax = bounds[0] + (cols.max() + 1) * analysis_dx
    selected_ymax = bounds[3] - rows.min() * analysis_dx
    selected_ymin = bounds[3] - (rows.max() + 1) * analysis_dx

    padding = args.padding_km * 1000.0
    need_width = 2.0 * max(center_x - selected_xmin, selected_xmax - center_x) + 2.0 * padding
    need_height = 2.0 * max(center_y - selected_ymin, selected_ymax - center_y) + 2.0 * padding
    quantum = integer_lcm(args.dx) * args.grid_multiple
    domain_width = args.domain_width_km * 1000.0 if args.domain_width_km else round_up(need_width, quantum)
    domain_height = args.domain_height_km * 1000.0 if args.domain_height_km else round_up(need_height, quantum)
    if domain_width + 1e-6 < need_width or domain_height + 1e-6 < need_height:
        raise ValueError("Requested domain width/height clips selected land or configured padding")
    for dx in args.dx:
        nx, ny = domain_width / dx, domain_height / dx
        if not nx.is_integer() or not ny.is_integer():
            raise ValueError(f"Domain is not divisible by dx={dx:g} m")
        if int(nx) % args.grid_multiple or int(ny) % args.grid_multiple:
            raise ValueError(f"Grid for dx={dx:g} is not a multiple of {args.grid_multiple}")
    final_bounds = (center_x - domain_width / 2, center_y - domain_height / 2,
                    center_x + domain_width / 2, center_y + domain_height / 2)
    metadata = {
        "center_x": center_x, "center_y": center_y,
        "main_island_analysis_pixels": main["pixels"],
        "analysis_component_count": main["components"],
        "domain_width_m": domain_width, "domain_height_m": domain_height,
        "bounds": list(final_bounds), "grid_quantum_m": quantum,
    }
    LOG.info("Domain %.1f x %.1f km; main-island bbox center is domain center",
             domain_width / 1000, domain_height / 1000)
    return final_bounds, metadata


def block_mean(array: np.ndarray, factor: int) -> np.ndarray:
    ny, nx = array.shape
    if ny % factor or nx % factor:
        raise ValueError(f"Array shape {array.shape} is not divisible by factor {factor}")
    return array.reshape(ny // factor, factor, nx // factor, factor).mean(axis=(1, 3), dtype=np.float64).astype(np.float32)


def block_fraction(array: np.ndarray, value: int, factor: int) -> np.ndarray:
    ny, nx = array.shape
    return (array == value).reshape(ny // factor, factor, nx // factor, factor).mean(axis=(1, 3), dtype=np.float64).astype(np.float32)


def xy_lonlat(bounds: tuple[float, float, float, float], dx: float, nx: int, ny: int, dst_wkt: str):
    x = bounds[0] + (np.arange(nx, dtype=np.float64) + 0.5) * dx
    y = bounds[1] + (np.arange(ny, dtype=np.float64) + 0.5) * dx
    inverse = Transformer.from_crs(CRS.from_wkt(dst_wkt), CRS.from_epsg(4326), always_xy=True)
    lon, _ = inverse.transform(x, np.full_like(x, y[ny // 2]))
    _, lat = inverse.transform(np.full_like(y, x[nx // 2]), y)
    return x, y, np.asarray(lon), np.asarray(lat)


def create_var(ds: Dataset, name: str, dtype: str, dims: tuple[str, ...], data,
               units: str, long_name: str, compressed: bool = True):
    kwargs = {"zlib": True, "complevel": 2, "shuffle": True} if compressed else {}
    var = ds.createVariable(name, dtype, dims, **kwargs)
    var.units = units
    var.long_name = long_name
    var[:] = data
    return var


def write_master(path: Path, height: np.ndarray, wc: np.ndarray, land: np.ndarray,
                 slope: np.ndarray, bounds, master_dx: float, dst_wkt: str, attrs: dict):
    ny, nx = height.shape
    x, y, lon, lat = xy_lonlat(bounds, master_dx, nx, ny, dst_wkt)
    LOG.info("Writing master NetCDF: %s", path)
    with Dataset(path, "w", format="NETCDF4") as ds:
        ds.createDimension("x", nx); ds.createDimension("y", ny)
        ds.Conventions = "CF-1.8"
        ds.title = f"Puerto Rico model-independent {master_dx:g} m surface master"
        ds.projection_wkt = dst_wkt
        for key, value in attrs.items():
            setattr(ds, key, value)
        ds.history = f"Generated {datetime.now(timezone.utc).isoformat()}"
        x_var = create_var(ds, "x", "f8", ("x",), x, "m", "projected x coordinate")
        y_var = create_var(ds, "y", "f8", ("y",), y, "m", "projected y coordinate")
        x_var.axis = "X"; y_var.axis = "Y"
        # EQC is separable, but strategy requests 2-D lon/lat in the master.
        lon_var = ds.createVariable("lon", "f4", ("y", "x"), zlib=True, complevel=2,
                                    chunksizes=(min(256, ny), min(1024, nx)))
        lat_var = ds.createVariable("lat", "f4", ("y", "x"), zlib=True, complevel=2,
                                    chunksizes=(min(256, ny), min(1024, nx)))
        lon_var.units = "degrees_east"; lon_var.standard_name = "longitude"
        lat_var.units = "degrees_north"; lat_var.standard_name = "latitude"
        lon_var.axis = "X"; lat_var.axis = "Y"
        for j0 in range(0, ny, 256):
            j1 = min(ny, j0 + 256)
            lon_var[j0:j1, :] = np.broadcast_to(lon, (j1 - j0, nx))
            lat_var[j0:j1, :] = np.broadcast_to(lat[j0:j1, None], (j1 - j0, nx))
        create_var(ds, "height_raw", "f4", ("y", "x"), height, "m", "DEM elevation above sea level")
        create_var(ds, "slope_percent", "f4", ("y", "x"), slope, "%", "high-resolution terrain slope")
        create_var(ds, "worldcover", "u1", ("y", "x"), wc, "class", "ESA WorldCover 2021 class")
        create_var(ds, "land_mask", "u1", ("y", "x"), land, "1", "land mask")
        create_var(ds, "water_mask", "u1", ("y", "x"), ~land, "1", "water mask")


def read_master(path: Path, args: argparse.Namespace):
    """Read a compatible model-independent master instead of warping TIFFs."""
    required = {"x", "y", "height_raw", "slope_percent", "worldcover", "land_mask"}
    LOG.info("Reusing surface master: %s", path)
    with Dataset(path, "r") as ds:
        missing = sorted(required - set(ds.variables))
        if missing:
            raise ValueError(f"Surface master is missing required variables: {missing}")
        if "projection_wkt" not in ds.ncattrs():
            raise ValueError("Surface master is missing the projection_wkt attribute")
        x = np.asarray(ds.variables["x"][:], dtype=np.float64)
        y = np.asarray(ds.variables["y"][:], dtype=np.float64)
        if x.size < 2 or y.size < 2:
            raise ValueError("Surface master x/y coordinates require at least two cells")
        dx_x = np.diff(x); dx_y = np.diff(y)
        master_dx = float(dx_x[0])
        if master_dx <= 0 or not np.allclose(dx_x, master_dx, rtol=0, atol=1.0e-6):
            raise ValueError("Surface master x coordinate is not uniformly increasing")
        if not np.allclose(dx_y, master_dx, rtol=0, atol=1.0e-6):
            raise ValueError("Surface master x/y grid spacing is inconsistent")
        nx_master = x.size; ny_master = y.size
        if nx_master * ny_master > args.max_master_cells:
            raise ValueError(
                f"Surface master has {nx_master * ny_master:,} cells; exceeds "
                f"--max-master-cells={args.max_master_cells:,}")
        for target_dx in args.dx:
            ratio = target_dx / master_dx
            factor = int(round(ratio))
            if not math.isclose(ratio, factor, abs_tol=1.0e-9):
                raise ValueError(
                    f"target dx={target_dx:g} m is not an integer multiple of "
                    f"surface-master dx={master_dx:g} m")
            if nx_master % factor or ny_master % factor:
                raise ValueError(
                    f"Surface-master shape {ny_master}x{nx_master} is not divisible "
                    f"by the aggregation factor {factor} for dx={target_dx:g} m")
            nx_target = nx_master // factor; ny_target = ny_master // factor
            if nx_target % args.grid_multiple or ny_target % args.grid_multiple:
                raise ValueError(
                    f"Reused-master target grid {ny_target}x{nx_target} for "
                    f"dx={target_dx:g} m is not divisible by grid_multiple="
                    f"{args.grid_multiple}")
        configured_dx = args.master_dx
        if not math.isclose(configured_dx, master_dx, abs_tol=1.0e-6):
            LOG.warning(
                "Configured master_dx_m=%.6g is ignored; reused master spacing is %.6g m",
                configured_dx, master_dx)
        args.master_dx = master_dx
        master_island = bool(getattr(ds, "main_island_only", args.main_island_only))
        if master_island != args.main_island_only:
            LOG.warning(
                "Configured main_island_only=%s is ignored; reused master has %s",
                args.main_island_only, master_island)
        args.main_island_only = master_island
        dst_wkt = str(ds.projection_wkt)
        dem = np.asarray(ds.variables["height_raw"][:], dtype=np.float32)
        slope = np.asarray(ds.variables["slope_percent"][:], dtype=np.float32)
        wc = np.asarray(ds.variables["worldcover"][:], dtype=np.uint8)
        land = np.asarray(ds.variables["land_mask"][:], dtype=bool)
    expected_shape = (ny_master, nx_master)
    for name, values in (("height_raw", dem), ("slope_percent", slope),
                         ("worldcover", wc), ("land_mask", land)):
        if values.shape != expected_shape:
            raise ValueError(
                f"Surface-master variable {name} has shape {values.shape}; "
                f"expected {expected_shape}")
    bounds = (float(x[0] - master_dx / 2), float(y[0] - master_dx / 2),
              float(x[-1] + master_dx / 2), float(y[-1] + master_dx / 2))
    width_m = bounds[2] - bounds[0]; height_m = bounds[3] - bounds[1]
    configured = (args.domain_width_km, args.domain_height_km)
    actual = (width_m / 1000.0, height_m / 1000.0)
    if any(value is not None for value in configured) and any(
            value is not None and not math.isclose(value, size, abs_tol=1.0e-6)
            for value, size in zip(configured, actual)):
        LOG.warning(
            "Configured domain dimensions %s km are ignored; reused master is %.6g x %.6g km",
            configured, actual[0], actual[1])
    LOG.info(
        "Loaded common %.6g m master grid (%d x %d); TIFF warping, domain discovery, "
        "padding, and write_master are skipped",
        master_dx, nx_master, ny_master)
    metadata = {
        "source_master": str(path), "center_x": 0.5 * (bounds[0] + bounds[2]),
        "center_y": 0.5 * (bounds[1] + bounds[3]),
        "domain_width_m": width_m, "domain_height_m": height_m,
        "bounds": list(bounds), "master_dx_m": master_dx,
    }
    return dem, slope, wc, land, bounds, dst_wkt, metadata


def build_vertical(nz: int, dz: float, dz1: float, coord_type: str,
                   domain: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if nz < 3 or dz <= 0 or dz1 <= 0:
        raise ValueError("Vertical grid requires nz >= 3 and positive dz/dz1")
    if domain <= dz:
        raise ValueError("Vertical stretching domain must be greater than dz")
    cz2 = (dz - dz1) / (dz * (domain - dz))
    cz1 = 1.0 - cz2 * domain
    z_up = np.arange(nz, dtype=np.float64) * dz
    vvm_zt = np.zeros(nz, dtype=np.float64)
    vvm_zt[1:] = (np.arange(1, nz, dtype=np.float64) - 0.5) * dz
    z_up *= cz1 + cz2 * z_up
    vvm_zt *= cz1 + cz2 * vvm_zt
    if coord_type == "taiwanvvm":
        if abs(cz2) < 1e-15:
            kt1 = 1
        else:
            kt = int((1.0 - cz1) / cz2 / 2.0 / dz)
            kt_idx = max(0, min(kt - 1, nz - 1))
            kt1 = int(z_up[kt_idx] / dz1 + 0.999)
        z_up[0] = 0.0
        for k in range(1, max(1, min(kt1 - 1, nz))):
            z_up[k + 1:] = z_up[k:-1] + dz1
            z_up[k] = z_up[k - 1] + dz1
        vvm_zt[0] = 0.0
        vvm_zt[1] = 0.5 * dz1
        for k in range(2, min(kt1, nz)):
            vvm_zt[k + 1:] = vvm_zt[k:-1] + dz1
            vvm_zt[k] = vvm_zt[k - 1] + dz1
    elif coord_type != "default":
        raise ValueError("Supported vertical coordinate types: default, taiwanvvm")
    z_mid = np.empty_like(z_up)
    z_mid[:-1] = 0.5 * (z_up[:-1] + z_up[1:])
    z_mid[-1] = z_up[-1] + 0.5 * (z_up[-1] - z_up[-2])
    if not np.all(np.diff(z_up) > 0):
        raise ValueError("Generated vertical levels are not strictly increasing")
    return z_up, z_mid, vvm_zt


def apply_vertical_config(args: argparse.Namespace):
    if not args.vertical_config:
        LOG.info(
            "Vertical settings: "
            "VVM nz=%d, dz=%.1f m, dz1=%.1f m, type=%s",
            args.nz, args.dz, args.dz1, args.vertical_coordinate,
        )
        return
    with args.vertical_config.open(encoding="utf-8") as handle:
        grid = json.load(handle)["grid"]
    args.nz = int(grid["nz"]); args.dz = float(grid["dz"]); args.dz1 = float(grid["dz1"])
    args.vertical_coordinate = grid.get("vertical_coordinate_type", "default")
    args.vertical_domain = float(grid.get("vertical_stretching_domain_m", args.vertical_domain))
    args.vvmex_drop_bottom_level = bool(
        grid.get("vvmex_drop_bottom_level", args.vvmex_drop_bottom_level))
    LOG.info("Vertical grid loaded from %s", args.vertical_config)


def height_to_vvm_topo(height_m: np.ndarray, land: np.ndarray, z_up: np.ndarray) -> np.ndarray:
    midpoints = 0.5 * (z_up[:-1] + z_up[1:])
    nearest = np.searchsorted(midpoints, height_m, side="left")
    nearest = np.clip(nearest, 0, len(z_up) - 2)
    # Convert the zero-based vertical index to the model's one-based index,
    # then add one terrain grid to every land cell. Flat land is therefore
    # topo=2 while ocean remains topo=0.
    topo = nearest.astype(np.int32) + 1
    topo[land] += 1
    topo[~land] = 0
    return topo


def island_interior_water_mask(land: np.ndarray) -> np.ndarray:
    """Return lakes and narrow inland waterways on the four-neighbour grid.

    A one-cell erosion produces the open-ocean core. Reconnecting one cell of
    shoreline after edge flood-fill restores ordinary coastal ocean, but does
    not flood far into one- or two-cell-wide river channels. Consequently,
    enclosed lakes and narrow inland river segments participate in model
    topography, while the exterior ocean remains ``topo=0``. The surface
    land/water classification itself is not changed.
    """
    if land.ndim != 2:
        raise ValueError(f"land mask must be two-dimensional, got shape {land.shape}")
    structure = ndimage.generate_binary_structure(2, 1)
    water = ~land
    ocean_core_candidates = ndimage.binary_erosion(
        water, structure=structure, iterations=1, border_value=1)
    edge_seed = np.zeros_like(land, dtype=bool)
    edge_seed[0, :] = ocean_core_candidates[0, :]
    edge_seed[-1, :] = ocean_core_candidates[-1, :]
    edge_seed[:, 0] = ocean_core_candidates[:, 0]
    edge_seed[:, -1] = ocean_core_candidates[:, -1]
    ocean_core = ndimage.binary_propagation(
        edge_seed, mask=ocean_core_candidates, structure=structure)
    ocean = ndimage.binary_dilation(ocean_core, structure=structure, iterations=1) & water
    return np.asarray(water & ~ocean, dtype=bool)


def count_caves(topo: np.ndarray, periodic: bool, eligible: np.ndarray | None = None) -> np.ndarray:
    if periodic:
        west = np.roll(topo, 1, 1); east = np.roll(topo, -1, 1)
        south = np.roll(topo, 1, 0); north = np.roll(topo, -1, 0)
        result = (west > topo) & (east > topo) & (south > topo) & (north > topo)
        return result if eligible is None else result & eligible
    result = np.zeros_like(topo, dtype=bool)
    center = topo[1:-1, 1:-1]
    result[1:-1, 1:-1] = ((topo[1:-1, :-2] > center) & (topo[1:-1, 2:] > center) &
                          (topo[:-2, 1:-1] > center) & (topo[2:, 1:-1] > center))
    return result if eligible is None else result & eligible


def remove_caves(topo: np.ndarray, periodic: bool, max_iter: int,
                 eligible: np.ndarray | None = None) -> tuple[np.ndarray, dict]:
    result = topo.copy()
    before = int(count_caves(result, periodic, eligible).sum())
    corrected = 0
    iterations = 0
    for iterations in range(1, max_iter + 1):
        caves = count_caves(result, periodic, eligible)
        number = int(caves.sum())
        if number == 0:
            break
        west = np.roll(result, 1, 1); east = np.roll(result, -1, 1)
        south = np.roll(result, 1, 0); north = np.roll(result, -1, 0)
        replacement = np.minimum.reduce([west, east, south, north])
        result[caves] = replacement[caves]
        corrected += number
    remaining = int(count_caves(result, periodic, eligible).sum())
    return result, {"caves_before": before, "cave_updates": corrected,
                    "cave_iterations": iterations, "caves_remaining": remaining}


def validate_model_topography(topo: np.ndarray, terrain: np.ndarray, periodic: bool) -> None:
    """Fail before writing a model file if terrain/ocean or cave rules fail."""
    if topo.shape != terrain.shape:
        raise ValueError(f"topo shape {topo.shape} does not match terrain mask {terrain.shape}")
    if np.any(topo[terrain] < 2):
        raise ValueError("Model terrain contains topo below the minimum land level 2")
    if np.any(topo[~terrain] != 0):
        raise ValueError("Exterior ocean contains non-zero topo")
    caves = int(count_caves(topo, periodic, terrain).sum())
    if caves:
        raise ValueError(f"Model topography still contains {caves} four-neighbour caves")


def dominant_mapped(fractions: np.ndarray, mapping: np.ndarray) -> np.ndarray:
    unique = np.unique(mapping)
    summed = np.stack([fractions[mapping == value].sum(axis=0) for value in unique])
    return unique[np.argmax(summed, axis=0)].astype(np.int32)


def vvm_slope_category(low: np.ndarray, medium: np.ndarray, high: np.ndarray,
                       presence: float = 0.05) -> np.ndarray:
    present = np.stack([low >= presence, medium >= presence, high >= presence])
    # Guarantee at least the dominant bin when every fraction is below threshold.
    none = ~present.any(axis=0)
    dominant = np.argmax(np.stack([low, medium, high]), axis=0)
    for k in range(3):
        present[k][none & (dominant == k)] = True
    bits = present[0].astype(np.uint8) + 2 * present[1].astype(np.uint8) + 4 * present[2].astype(np.uint8)
    lookup = np.array([1, 1, 2, 4, 3, 5, 6, 7], dtype=np.int32)
    return lookup[bits]


def write_surface(path: Path, data: dict, attrs: dict, dst_wkt: str):
    ny, nx = data["height"].shape
    with Dataset(path, "w", format="NETCDF4") as ds:
        ds.createDimension("x", nx); ds.createDimension("y", ny)
        ds.Conventions = "CF-1.8"; ds.title = "Puerto Rico model-independent coarse surface"
        ds.projection_wkt = dst_wkt
        for key, value in attrs.items():
            setattr(ds, key, value)
        x_var = create_var(ds, "x", "f8", ("x",), data["x"], "m", "projected x coordinate")
        y_var = create_var(ds, "y", "f8", ("y",), data["y"], "m", "projected y coordinate")
        x_var.axis = "X"; y_var.axis = "Y"
        lon2d = np.broadcast_to(data["lon"], (ny, nx)); lat2d = np.broadcast_to(data["lat"][:, None], (ny, nx))
        lon_var = create_var(ds, "lon", "f4", ("y", "x"), lon2d, "degrees_east", "longitude")
        lat_var = create_var(ds, "lat", "f4", ("y", "x"), lat2d, "degrees_north", "latitude")
        lon_var.axis = "X"; lat_var.axis = "Y"
        fields = {
            "height": ("f4", "m", "target-grid mean terrain height"),
            "slope_percent": ("f4", "%", "mean high-resolution terrain slope"),
            "worldcover_dominant": ("u1", "class", "dominant WorldCover class"),
            "land_fraction": ("f4", "1", "land area fraction"),
        }
        for name in FRACTION_NAMES:
            fields[name] = ("f4", "1", f"WorldCover {name.replace('_fraction', '')} area fraction")
        for name, (dtype, units, long_name) in fields.items():
            create_var(ds, name, dtype, ("y", "x"), data[name], units, long_name)


def write_mask_by_level(var, topo: np.ndarray, nz: int, drop_bottom_floor: bool = False):
    # topo uses the VVM level numbering even when VVMex omits the bottom
    # floor. Removing that floor removes one blocked level from the 3-D mask.
    blocked = np.maximum(topo - 1, 0) if drop_bottom_floor else np.maximum(topo, 1)
    for k in range(nz):
        var[k, :, :] = (k >= blocked).astype(np.int8)


def write_vvm(path: Path, data: dict, topo: np.ndarray, z_up: np.ndarray, z_mid: np.ndarray):
    ny, nx = topo.shape
    reconstructed = np.where(topo > 0, z_up[np.maximum(topo - 1, 0)], 0.0) / 1000.0
    with Dataset(path, "w", format="NETCDF3_64BIT_OFFSET") as ds:
        ds.createDimension("lon", nx); ds.createDimension("lat", ny)
        ds.createDimension("lev", len(z_mid)); ds.createDimension("time", 1)
        ds.Conventions = "CF-1.4"; ds.title = "Puerto Rico VVM topography and land surface"
        ds.topo_land_offset = np.int32(1)
        create_var(ds, "lon", "f4", ("lon",), data["lon"], "degrees_east", "longitude", False).axis = "X"
        create_var(ds, "lat", "f4", ("lat",), data["lat"], "degrees_north", "latitude", False).axis = "Y"
        lev = create_var(ds, "lev", "f4", ("lev",), z_mid, "m", "altitude", False); lev.axis = "Z"; lev.positive = "up"
        time = create_var(ds, "time", "f4", ("time",), [0], "hours since 2000-01-01 00:00", "time", False); time.axis = "T"
        mask = ds.createVariable("mask", "i1", ("lev", "lat", "lon"))
        mask.units = "T/F"; mask.long_name = "mask of thermodynamic variables"
        write_mask_by_level(mask, topo, len(z_mid))
        create_var(ds, "topo", "f8", ("lat", "lon"), topo, "grid", "terrain height in VVM", False)
        create_var(ds, "height", "f4", ("lat", "lon"), reconstructed, "km", "terrain height", False)
        for name, dtype, units in (("albedo", "f4", "%"), ("gvf", "f4", "%"), ("lai", "f4", "m^2 m^-2"),
                                   ("lu", "i4", "type"), ("shdmax", "f4", "%"), ("shdmin", "f4", "%"),
                                   ("slope", "i4", "type"), ("soil", "i4", "type")):
            create_var(ds, name, dtype, ("lat", "lon"), data[name], units, name, False)


def write_vvmex(
        path: Path, data: dict, topo: np.ndarray, z_up: np.ndarray,
        z_mid: np.ndarray, drop_bottom_floor: bool):
    ny, nx = topo.shape
    index_z = np.clip(topo - 1, 0, len(z_up) - 1)
    reconstructed = np.where(topo > 0, z_up[index_z], 0.0) / 1000.0
    lon2d = np.broadcast_to(data["lon"], (ny, nx)); lat2d = np.broadcast_to(data["lat"][:, None], (ny, nx))
    with Dataset(path, "w", format="NETCDF4") as ds:
        ds.createDimension("nx", nx); ds.createDimension("ny", ny); ds.createDimension("nz", len(z_mid)); ds.createDimension("time", 1)
        ds.Conventions = "CF-1.8"; ds.title = "Puerto Rico VVMex spatial initial conditions"
        ds.topo_land_offset = np.int32(1)
        create_var(ds, "nx", "f8", ("nx",), data["lon"], "degrees_east", "longitude").axis = "X"
        create_var(ds, "ny", "f8", ("ny",), data["lat"], "degrees_north", "latitude").axis = "Y"
        nzv = create_var(ds, "nz", "f8", ("nz",), z_mid, "m", "altitude"); nzv.axis = "Z"; nzv.positive = "up"
        time = create_var(ds, "time", "f8", ("time",), [0], "seconds since 1970-01-01 00:00:00 UTC", "time"); time.axis = "T"
        lon_var = create_var(ds, "lon", "f8", ("ny", "nx"), lon2d, "degrees_east", "Longitude (2D matrix)")
        lat_var = create_var(ds, "lat", "f8", ("ny", "nx"), lat2d, "degrees_north", "Latitude (2D matrix)")
        lon_var.axis = "X"; lat_var.axis = "Y"
        create_var(ds, "topo", "f8", ("ny", "nx"), topo, "grid", "Topography index")
        mask = ds.createVariable("mask", "i1", ("nz", "ny", "nx"), zlib=True, complevel=2, shuffle=True)
        mask.units = "T/F"; mask.long_name = "3D atmospheric mask (0 terrain, 1 atmosphere)"
        write_mask_by_level(mask, topo, len(z_mid), drop_bottom_floor)
        create_var(ds, "height", "f8", ("ny", "nx"), reconstructed, "km", "Physical terrain height")
        fields = {
            "sea_land_ice_mask": (data["land"].astype(np.int32), "i4", "flag"),
            "vegtype": (data["vegtype"], "i4", "category"), "soiltype": (data["soiltype"], "i4", "category"),
            "slopetype": (data["slopetype"], "i4", "category"), "Tg": (data["Tg"], "f8", "K"),
            "albedo": (data["albedo"], "f8", "%"), "gvf": (data["gvf"], "f8", "%"),
            "lai": (data["lai"], "f8", "m2/m2"), "shdmax": (data["shdmax"], "f8", "%"),
            "shdmin": (data["shdmin"], "f8", "%"),
        }
        for n in ("sm1", "sm2", "sm3", "sm4", "sl1", "sl2", "sl3", "sl4"):
            fields[n] = (data[n], "f8", "m3/m3")
        for name, (values, dtype, units) in fields.items():
            create_var(ds, name, dtype, ("ny", "nx"), values, units, name)


def surface_for_dx(height, slope, wc, bounds, master_dx, dx, dst_wkt, water_threshold):
    ratio = dx / master_dx
    factor = int(round(ratio))
    if not math.isclose(ratio, factor, abs_tol=1e-9):
        raise ValueError(f"dx={dx:g} is not an integer multiple of master dx={master_dx:g}")
    fractions = np.stack([block_fraction(wc, int(value), factor) for value in WORLD_COVER_CLASSES])
    total = fractions.sum(axis=0)
    if np.any(np.abs(total - 1.0) > 2e-5):
        raise ValueError(f"WorldCover fractions do not sum to one (range {total.min()}..{total.max()})")
    height_mean = block_mean(height, factor)
    slope_mean = block_mean(slope, factor)
    water_fraction = fractions[7]
    land = water_fraction < water_threshold
    height_mean[~land] = 0.0; slope_mean[~land] = 0.0
    dominant = WORLD_COVER_CLASSES[np.argmax(fractions, axis=0)]
    ny, nx = height_mean.shape
    x, y, lon, lat = xy_lonlat(bounds, dx, nx, ny, dst_wkt)
    data = {"x": x, "y": y, "lon": lon, "lat": lat, "height": height_mean,
            "slope_percent": slope_mean, "worldcover_dominant": dominant.astype(np.uint8),
            "land_fraction": (1.0 - water_fraction).astype(np.float32), "land": land, "fractions": fractions}
    for name, values in zip(FRACTION_NAMES, fractions):
        data[name] = values
    return data


def retain_target_grid_main_island(surface: dict) -> dict[str, int]:
    """Keep only the largest four-connected land component after regridding.

    Main-island selection on the master grid removes real offshore islands,
    but the target-grid water-fraction threshold can split the retained Puerto
    Rico land mass into new, disconnected pieces.  Convert those pieces to
    unambiguous water cells so the surface, VVM, and VVMex products all use the
    same post-regrid coastline.
    """
    land = surface["land"]
    labels, main = component_info(land)
    retained = labels == main["label"]
    removed = land & ~retained
    removed_cells = int(removed.sum())

    if removed_cells:
        surface["land"][removed] = False
        surface["height"][removed] = 0.0
        surface["slope_percent"][removed] = 0.0
        surface["worldcover_dominant"][removed] = np.uint8(80)
        surface["land_fraction"][removed] = 0.0

        # Preserve the documented sum(fractions) == 1 invariant and make the
        # cleaned cells explicitly water, rather than merely overriding land.
        surface["fractions"][:, removed] = 0.0
        surface["fractions"][7, removed] = 1.0

    components_after = component_info(surface["land"])[1]["components"]
    if components_after != 1:
        raise ValueError(
            "Post-regrid main-island cleanup did not produce one land component: "
            f"found {components_after}")
    return {
        "components_before": main["components"],
        "components_after": components_after,
        "kept_land_cells": int(retained.sum()),
        "removed_land_cells": removed_cells,
        "removed_components": main["components"] - 1,
    }


def model_fields(surface: dict) -> tuple[dict, dict]:
    f = surface["fractions"]
    weighted = lambda table: np.tensordot(table.astype(np.float32), f, axes=(0, 0)).astype(np.float32)
    common = {"lon": surface["lon"], "lat": surface["lat"], "land": surface["land"],
              "albedo": weighted(ALBEDO), "gvf": weighted(GVF), "lai": weighted(LAI)}
    common["shdmax"] = common["gvf"].copy(); common["shdmin"] = common["gvf"].copy()
    # Slope regime fractions are recomputed from master in run_pipeline and replace these placeholders.
    categorical_f = f.copy()
    # Once the coastline threshold declares a target cell to be land, water
    # must not win the categorical vote merely because the land is split
    # among several vegetation classes. Ocean categories are forced below.
    categorical_f[7][surface["land"]] = 0.0
    vvm = dict(common)
    vvm["lu"] = dominant_mapped(categorical_f, VVM_USGS)
    vvm["soil"] = dominant_mapped(categorical_f, SOIL_CLASS)
    vvmex = dict(common)
    vvmex["vegtype"] = dominant_mapped(categorical_f, VVMEX_IGBP)
    vvmex["soiltype"] = dominant_mapped(categorical_f, SOIL_CLASS)
    vvm["lu"][~surface["land"]] = 16
    vvm["soil"][~surface["land"]] = 14
    vvmex["vegtype"][~surface["land"]] = 17
    vvmex["soiltype"][~surface["land"]] = 14
    vvmex["Tg"] = np.full(surface["land"].shape, 300.0, dtype=np.float64)
    for n in ("sm1", "sm2", "sm3", "sm4", "sl1", "sl2", "sl3", "sl4"):
        vvmex[n] = np.full(surface["land"].shape, 0.3054033, dtype=np.float64)
    return vvm, vvmex


def qa_record(dx, surface, vvm, vvmex, cave_vvm, cave_vvmex):
    land = surface["land"]
    cell_area = dx * dx
    return {
        "resolution_m": dx, "nx": int(land.shape[1]), "ny": int(land.shape[0]),
        "height_min_m": float(surface["height"][land].min()) if land.any() else 0.0,
        "height_max_m": float(surface["height"].max()),
        "height_mean_land_m": float(surface["height"][land].mean()) if land.any() else 0.0,
        "land_cells": int(land.sum()), "water_cells": int((~land).sum()),
        "land_area_km2": float(land.sum() * cell_area / 1e6),
        "water_area_km2": float((~land).sum() * cell_area / 1e6),
        "worldcover_domain_fractions": {name: float(surface[name].mean()) for name in FRACTION_NAMES},
        "vvm_lu": np.unique(vvm["lu"]).tolist(), "vvm_soil": np.unique(vvm["soil"]).tolist(),
        "vvm_slope": np.unique(vvm["slope"]).tolist(),
        "vvmex_vegtype": np.unique(vvmex["vegtype"]).tolist(),
        "vvmex_soiltype": np.unique(vvmex["soiltype"]).tolist(),
        "vvmex_slopetype": np.unique(vvmex["slopetype"]).tolist(),
        "mean_albedo": float(vvm["albedo"].mean()), "mean_gvf": float(vvm["gvf"].mean()),
        "mean_lai": float(vvm["lai"].mean()), "vvm_caves": cave_vvm, "vvmex_caves": cave_vvmex,
    }


def run_pipeline(args: argparse.Namespace):
    gdal.UseExceptions()
    osr.SetPROJEnableNetwork(False)
    apply_vertical_config(args)
    if args.surface_master:
        if not args.surface_master.is_file():
            raise FileNotFoundError(args.surface_master)
        dem, slope, wc, land, bounds, dst_wkt, domain_meta = read_master(
            args.surface_master, args)
    else:
        for path in (args.dem, args.landcover):
            if not path.is_file():
                raise FileNotFoundError(path)
        lon0, lat0 = raster_center_lonlat(args.dem)
        LOG.warning("PROJ network access is disabled; NAD83 is transformed to WGS84 with locally available/ballpark operations (metre-scale horizontal uncertainty)")
        dst_wkt = local_eqc_wkt(lon0, lat0)
        bounds, domain_meta = discover_domain(args, dst_wkt)
        width_m = bounds[2] - bounds[0]; height_m = bounds[3] - bounds[1]
        nx_master = int(round(width_m / args.master_dx)); ny_master = int(round(height_m / args.master_dx))
        if nx_master * ny_master > args.max_master_cells:
            raise ValueError(f"Master grid has {nx_master * ny_master:,} cells; exceeds --max-master-cells={args.max_master_cells:,}")
        LOG.info("Warping both TIFF files to common %.1f m grid (%d x %d, %.1f million cells)",
                 args.master_dx, nx_master, ny_master, nx_master * ny_master / 1e6)
        wc = warp_array(args.landcover, dst_wkt, bounds, nx_master, ny_master, "near", gdal.GDT_Byte, 0, 0)
        dem = warp_array(args.dem, dst_wkt, bounds, nx_master, ny_master, "average", gdal.GDT_Float32, -999999, -999999)
        # Convert GDAL north-to-south rows to increasing-y model order.
        wc = np.flipud(wc); dem = np.flipud(dem).astype(np.float32)
        wc[~np.isin(wc, WORLD_COVER_CLASSES)] = 80
        source_land = (wc != 80)
        if args.main_island_only:
            labels, main = component_info(source_land)
            land = labels == main["label"]
            removed = int(source_land.sum() - land.sum())
            LOG.info("Master-grid main-island selection kept %.2f km2 and converted %.2f km2 of other land to water",
                     land.sum() * args.master_dx**2 / 1e6, removed * args.master_dx**2 / 1e6)
            wc[~land] = 80
        else:
            land = source_land
        dem[(dem <= -999998) | ~np.isfinite(dem)] = 0.0
        dem = np.maximum(dem, 0.0); dem[~land] = 0.0
        gy, gx = np.gradient(dem.astype(np.float64), args.master_dx, args.master_dx)
        slope = (100.0 * np.hypot(gx, gy)).astype(np.float32); slope[~land] = 0.0
        del gx, gy
        if args.main_island_only:
            del labels
        else:
            del source_land

        attrs = {**domain_meta, "main_island_only": np.int8(args.main_island_only),
                 "master_dx_m": args.master_dx,
                 "water_fraction_threshold": args.water_threshold}
        if args.write_master:
            write_master(args.output_dir / "Puerto_Rico_surface_master.nc", dem, wc, land, slope,
                         bounds, args.master_dx, dst_wkt, attrs)

    z_up, z_mid, vvm_zt = build_vertical(
        args.nz, args.dz, args.dz1, args.vertical_coordinate, args.vertical_domain)
    vvmex_z = vvm_zt[1:] if args.vvmex_drop_bottom_level else z_mid
    LOG.info(
        "Vertical dimensions: VVM=%d levels; VVMex=%d levels%s",
        len(vvm_zt), len(vvmex_z),
        " (VVM bottom floor omitted)" if args.vvmex_drop_bottom_level else "",
    )
    LOG.warning("Soil classes are prototype mappings because no independent soil dataset was supplied")
    LOG.warning("Tg=300 K and soil moisture=0.3054033 m3/m3 are initialization defaults, not observations")
    LOG.warning("shdmax/shdmin use the representative WorldCover GVF because no seasonal climatology was supplied")
    qa = []
    for dx in args.dx:
        label = resolution_label(dx)
        LOG.info("Building %s products directly from the common master grid", label)
        surface = surface_for_dx(dem, slope, wc, bounds, args.master_dx, dx, dst_wkt, args.water_threshold)
        target_island_qa = None
        if args.main_island_only:
            target_island_qa = retain_target_grid_main_island(surface)
            LOG.info(
                "%s post-regrid main-island check: %d -> %d components; "
                "removed %d cells in %d disconnected components (%.2f km2)",
                label, target_island_qa["components_before"],
                target_island_qa["components_after"],
                target_island_qa["removed_land_cells"],
                target_island_qa["removed_components"],
                target_island_qa["removed_land_cells"] * dx * dx / 1.0e6)
        vvm, vvmex = model_fields(surface)
        factor = int(round(dx / args.master_dx))
        master_land_fraction = block_mean(land.astype(np.float32), factor)
        denominator = np.where(master_land_fraction > 0, master_land_fraction, 1.0)
        low_f = block_mean((land & (slope < 8)).astype(np.float32), factor) / denominator
        med_f = block_mean((land & (slope >= 8) & (slope < 30)).astype(np.float32), factor) / denominator
        high_f = block_mean((land & (slope >= 30)).astype(np.float32), factor) / denominator
        vvm["slope"] = vvm_slope_category(low_f, med_f, high_f)
        slope_bins = np.array([0, 8, 16, 30, 45, 60, 90, 120, 150, np.inf])
        bin_f = np.stack([block_mean((land & (slope >= slope_bins[k]) & (slope < slope_bins[k + 1])).astype(np.float32), factor)
                          / denominator for k in range(9)])
        vvmex["slopetype"] = (np.argmax(bin_f, axis=0) + 1).astype(np.int32)
        vvm["slope"][~surface["land"]] = 0; vvmex["slopetype"][~surface["land"]] = 0
        vvm["soil"][~surface["land"]] = 14; vvmex["soiltype"][~surface["land"]] = 14
        vvm["lu"][~surface["land"]] = 16; vvmex["vegtype"][~surface["land"]] = 17

        surface_attrs = {"target_dx_m": dx, "target_dy_m": dx,
                         "main_island_only": np.int8(args.main_island_only),
                         "water_fraction_threshold": args.water_threshold}
        write_surface(args.output_dir / f"Puerto_Rico_surface_{label}.nc", surface, surface_attrs, dst_wkt)
        inland_water = island_interior_water_mask(surface["land"])
        terrain = surface["land"] | inland_water
        LOG.info(
            "%s model topography promotes %d enclosed lake/river cells (%.2f km2) to topo >= 2",
            label, int(inland_water.sum()), float(inland_water.sum() * dx * dx / 1.0e6))
        base_topo = height_to_vvm_topo(surface["height"], terrain, z_up)
        topo_vvm, cave_vvm = remove_caves(
            base_topo, args.periodic_topo, args.cave_max_iter, terrain)
        topo_vvmex = base_topo.copy()
        topo_vvmex, cave_vvmex = remove_caves(
            topo_vvmex, args.periodic_topo, args.cave_max_iter, terrain)
        topo_vvm[~terrain] = 0
        topo_vvmex[~terrain] = 0
        validate_model_topography(topo_vvm, terrain, args.periodic_topo)
        validate_model_topography(topo_vvmex, terrain, args.periodic_topo)
        if "vvm" in args.models:
            write_vvm(args.output_dir / f"Puerto_Rico_VVM_{label}.nc", vvm, topo_vvm, z_up, vvm_zt)
        if "vvmex" in args.models:
            write_vvmex(
                args.output_dir / f"Puerto_Rico_VVMex_{label}.nc",
                vvmex, topo_vvmex, z_up, vvmex_z,
                args.vvmex_drop_bottom_level)
        record = qa_record(dx, surface, vvm, vvmex, cave_vvm, cave_vvmex)
        record["post_regrid_main_island_cleanup"] = target_island_qa
        record["model_topography"] = {
            "inland_water_cells": int(inland_water.sum()),
            "inland_water_area_km2": float(inland_water.sum() * dx * dx / 1.0e6),
            "inland_water_topo_min": int(topo_vvm[inland_water].min()) if inland_water.any() else None,
            "inland_water_topo_max": int(topo_vvm[inland_water].max()) if inland_water.any() else None,
            "terrain_topo_min": int(topo_vvm[terrain].min()) if terrain.any() else None,
            "exterior_ocean_topo": 0,
        }
        qa.append(record)
        LOG.info("%s QA: %dx%d, land %.1f km2, height max %.1f m, caves VVM/VVMex %d/%d remaining",
                 label, record["nx"], record["ny"], record["land_area_km2"], record["height_max_m"],
                 cave_vvm["caves_remaining"], cave_vvmex["caves_remaining"])
    report = {"configuration": vars_for_json(args), "domain": domain_meta, "resolutions": qa}
    with (args.output_dir / "Puerto_Rico_topography_QA.json").open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
    LOG.info("Completed all requested products; QA report: %s", args.output_dir / "Puerto_Rico_topography_QA.json")


def vars_for_json(args):
    return {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()}


def parse_args(argv=None):
    config_parser = argparse.ArgumentParser(add_help=False)
    config_parser.add_argument(
        "--config", type=Path, default=Path("config_topo_lev45.json"))
    config_args, _ = config_parser.parse_known_args(argv)
    defaults = {}
    if config_args.config:
        if not config_args.config.is_file():
            config_parser.error(f"configuration file does not exist: {config_args.config}")
        try:
            defaults = config_defaults(config_args.config)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            config_parser.error(f"cannot read configuration {config_args.config}: {exc}")

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument(
        "--config", type=Path, default=config_args.config,
        help="JSON file containing the complete pipeline configuration")
    parser.add_argument("--dem", type=Path, default=Path("Puerto_Rico_3DEP_10m/Puerto_Rico_DEM_10m_master.tif"))
    parser.add_argument("--landcover", type=Path, default=Path("Puerto_Rico_LandCover_10m/Puerto_Rico_LandCover_10m_master.tif"))
    parser.add_argument("--surface-master", type=Path,
                        help="Reuse an existing Puerto_Rico_surface_master.nc and skip TIFF processing")
    parser.add_argument("--output-dir", type=Path, default=Path("Puerto_Rico_topography_output"))
    parser.add_argument("--dx", type=float, nargs="+", default=TARGET_DX_LIST)
    parser.add_argument("--master-dx", type=float, default=10.0)
    parser.add_argument("--analysis-dx", type=float, default=100.0)
    parser.add_argument("--padding-km", type=float, default=25.0)
    parser.add_argument("--domain-width-km", type=float)
    parser.add_argument("--domain-height-km", type=float)
    parser.add_argument("--grid-multiple", type=int, default=8)
    island = parser.add_mutually_exclusive_group()
    island.add_argument("--main-island-only", dest="main_island_only", action="store_true", default=True)
    island.add_argument("--all-islands", dest="main_island_only", action="store_false")
    parser.add_argument("--water-threshold", type=float, default=0.5)
    parser.add_argument("--models", nargs="+", choices=("vvm", "vvmex"), default=["vvm", "vvmex"])
    parser.add_argument(
        "--vertical-config", "--vvmex-config", dest="vertical_config", type=Path,
        help="Read vertical-grid settings from a JSON file (the old --vvmex-config name remains an alias)")
    parser.add_argument("--nz", type=int, default=45,
                        help="Number of VVM levels including the bottom floor")
    parser.add_argument("--dz", type=float, default=500.0)
    parser.add_argument("--dz1", type=float, default=100.0)
    parser.add_argument("--vertical-coordinate", choices=("default", "taiwanvvm"), default="taiwanvvm")
    parser.add_argument("--vertical-domain", type=float, default=15000.0,
                        help="Height scale used by the stretched vertical-coordinate formula (m)")
    parser.add_argument("--vvmex-drop-bottom-level", action=argparse.BooleanOptionalAction, default=True,
                        help="Write VVMex with nz-1 levels by omitting the VVM bottom floor")
    parser.add_argument("--cave-max-iter", type=int, default=10)
    parser.add_argument("--periodic-topo", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--write-master", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--max-master-cells", type=int, default=500_000_000,
                        help="Safety limit against accidental huge master grids")
    parser.set_defaults(**defaults)
    args = parser.parse_args(argv)
    args.dx = [float(value) for value in args.dx]
    if any(value <= 0 for value in args.dx) or args.master_dx <= 0:
        parser.error("Grid spacings must be positive")
    if not 0 <= args.water_threshold <= 1:
        parser.error("--water-threshold must be between 0 and 1")
    if args.grid_multiple < 1:
        parser.error("--grid-multiple must be positive")
    return args


def main(argv=None):
    args = parse_args(argv)
    log_path = setup_logging(args.output_dir)
    LOG.info("Starting Puerto Rico TIFF -> surface/VVM/VVMex pipeline")
    LOG.info("Pipeline configuration: %s", args.config)
    LOG.info("Log file: %s", log_path)
    try:
        run_pipeline(args)
    except Exception:
        LOG.error("Pipeline failed; the exception is not being suppressed:\n%s", traceback.format_exc())
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
