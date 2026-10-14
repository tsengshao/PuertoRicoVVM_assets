#!/usr/bin/env python3
"""Create VVM TOPO.nc from PuertoRico_Elevation.txt.

The output follows the variable names, attributes, ocean initialization, and
height-to-topo-grid conversion used by create_topolsm.F.
"""

from __future__ import annotations

import argparse
from collections import deque
from pathlib import Path

import numpy as np
from netCDF4 import Dataset


EARTH_RADIUS_M = 6_371_000.0


OCEAN = {
    "albedo": 8.0,
    "gvf": 0.0,
    "lai": 0.0,
    "shdmax": 0.0,
    "shdmin": 0.0,
    "lu": 16,
    "slope": 0,
    "soil": 14,
}

EVERGREEN_BROADLEAF_FOREST = {
    "albedo": 12.0,
    "gvf": 95.0,
    "lai": 6.48,
    "shdmax": 95.0,
    "shdmin": 95.0,
    "lu": 13,
    "slope": 1,
    "soil": 13,
}


def _base_stretched_coordinate(nk2: int, dz: float, dz1: float, domain: float, zb: float) -> tuple[np.ndarray, np.ndarray, float, float]:
    nk3 = nk2 + 1
    cz2 = (dz - dz1) / (dz * (domain - dz))
    cz1 = 1.0 - cz2 * domain

    zz_base = np.empty(nk3, dtype=np.float64)
    zt_base = np.empty(nk3, dtype=np.float64)
    zz_base[0] = zb
    for k in range(1, nk3):
        zz_base[k] = zz_base[k - 1] + dz

    zt_base[0] = zz_base[0]
    zt_base[1] = zz_base[0] + dz / 2.0
    for k in range(2, nk3):
        zt_base[k] = zt_base[k - 1] + dz

    zz = zz_base * (cz1 + cz2 * zz_base)
    zt = zt_base * (cz1 + cz2 * zt_base)
    return zz, zt, cz1, cz2


def vertical_coordinate(
    nk2: int,
    dz: float,
    dz1: float,
    domain: float,
    zb: float,
    mode: str,
) -> tuple[np.ndarray, np.ndarray]:
    """Replicate TaiwanVVM vertical-coordinate options."""
    zz, zt, cz1, cz2 = _base_stretched_coordinate(nk2, dz, dz1, domain, zb)

    if mode == "stretched":
        return zz[:nk2], zt[:nk2]

    if mode != "taiwanvvm-100m":
        raise ValueError(f"unknown vertical coordinate mode: {mode}")

    kt = int((1.0 - cz1) / cz2 / 2.0 / dz)
    kt1 = int(zz[kt - 1] / dz1 + 0.999)
    if kt1 >= nk2:
        raise ValueError(
            f"vertical mode {mode!r} needs nk2 > {kt1} for DZ={dz}, DZ1={dz1}, DOMAIN={domain}; "
            "use a larger --nk2 or use --vertical-mode stretched for a compact 34-layer grid"
        )

    zz[0] = zb
    for k in range(1, kt1 - 1):
        for kk in range(nk2, k, -1):
            zz[kk] = zz[kk - 1] + dz1
        zz[k] = zz[k - 1] + dz1

    zt[0] = zb
    zt[1] = zz[0] + dz1 / 2.0
    for k in range(2, kt1):
        for kk in range(nk2, k, -1):
            zt[kk] = zt[kk - 1] + dz1
        zt[k] = zt[k - 1] + dz1

    return zz[:nk2], zt[:nk2]


def fortran_nint(values: np.ndarray) -> np.ndarray:
    """Fortran NINT for non-negative values."""
    return np.floor(values + 0.5).astype(np.int32)


def topo_from_height(height_m: np.ndarray, zz: np.ndarray) -> np.ndarray:
    """Map real terrain height in meters to the VVM topo grid index."""
    topo = np.zeros(height_m.shape, dtype=np.float64)
    land = height_m > 0.0
    topo[land] = 2.0

    for k0 in range(1, len(zz) - 1):
        layer = land & (height_m >= zz[k0])
        topo[layer] = (k0 + 1) + fortran_nint((height_m[layer] - zz[k0]) / (zz[k0 + 1] - zz[k0]))

    topo[~land] = 0.0
    return topo


def largest_land_component(land: np.ndarray) -> np.ndarray:
    """Return only the largest 4-connected land component."""
    visited = np.zeros(land.shape, dtype=bool)
    largest: list[tuple[int, int]] = []
    nx, ny = land.shape

    for start_i, start_j in np.argwhere(land):
        if visited[start_i, start_j]:
            continue

        component: list[tuple[int, int]] = []
        queue: deque[tuple[int, int]] = deque([(int(start_i), int(start_j))])
        visited[start_i, start_j] = True

        while queue:
            i, j = queue.popleft()
            component.append((i, j))
            for ni, nj in ((i - 1, j), (i + 1, j), (i, j - 1), (i, j + 1)):
                if 0 <= ni < nx and 0 <= nj < ny and land[ni, nj] and not visited[ni, nj]:
                    visited[ni, nj] = True
                    queue.append((ni, nj))

        if len(component) > len(largest):
            largest = component

    main_island = np.zeros(land.shape, dtype=bool)
    for i, j in largest:
        main_island[i, j] = True
    return main_island


def single_grid_caves(topo: np.ndarray, land: np.ndarray) -> np.ndarray:
    """Find one-cell terrain pits within land, using create_topolsm.F's periodic edges."""
    west = np.roll(topo, 1, axis=0)
    east = np.roll(topo, -1, axis=0)
    south = np.roll(topo, 1, axis=1)
    north = np.roll(topo, -1, axis=1)
    return land & (west > topo) & (east > topo) & (south > topo) & (north > topo)


def fill_single_grid_caves(topo: np.ndarray, land: np.ndarray) -> tuple[np.ndarray, int, int]:
    """Fill one-cell land pits until none remain."""
    out = topo.copy()
    total_filled = 0
    iterations = 0

    while True:
        cave = single_grid_caves(out, land)
        count = int(cave.sum())
        if count == 0:
            return out, total_filled, iterations

        west = np.roll(out, 1, axis=0)
        east = np.roll(out, -1, axis=0)
        south = np.roll(out, 1, axis=1)
        north = np.roll(out, -1, axis=1)
        out[cave] = np.minimum.reduce([west[cave], east[cave], south[cave], north[cave]])
        total_filled += count
        iterations += 1


def load_elevation(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    data = np.loadtxt(path)
    if data.ndim != 2 or data.shape[1] != 3:
        raise ValueError(f"{path} must contain three columns: lon lat elevation_m")

    lon = np.unique(data[:, 0])
    lat = np.unique(data[:, 1])
    nx, ny = len(lon), len(lat)
    if data.shape[0] != nx * ny:
        raise ValueError(f"{path} is not a complete lon/lat grid")

    lon_index = {value: i for i, value in enumerate(lon)}
    lat_index = {value: j for j, value in enumerate(lat)}
    elev = np.empty((nx, ny), dtype=np.float64)
    for row_lon, row_lat, row_elev in data:
        elev[lon_index[row_lon], lat_index[row_lat]] = row_elev

    return lon.astype(np.float32), lat.astype(np.float32), elev


def center_on_domain(
    lon: np.ndarray,
    lat: np.ndarray,
    elev: np.ndarray,
    nx: int,
    ny: int,
    dx: float,
    dy: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float, float, tuple[int, int, int, int], tuple[float, float]]:
    """Create an ocean domain centered on the source lon/lat center."""
    src_nx, src_ny = elev.shape
    src_dlon = float(np.median(np.diff(lon)))
    src_dlat = float(np.median(np.diff(lat)))

    center_lon = float((lon[0] + lon[-1]) / 2.0)
    center_lat = float((lat[0] + lat[-1]) / 2.0)
    meters_per_degree_lat = np.pi * EARTH_RADIUS_M / 180.0
    meters_per_degree_lon = meters_per_degree_lat * np.cos(np.deg2rad(center_lat))
    #meters_per_degree_lat = 111000.
    #meters_per_degree_lon = 111000.
    dlon = dx / meters_per_degree_lon
    dlat = dy / meters_per_degree_lat

    target_lon = center_lon + (np.arange(nx, dtype=np.float64) - (nx - 1) / 2.0) * dlon
    target_lat = center_lat + (np.arange(ny, dtype=np.float64) - (ny - 1) / 2.0) * dlat

    lon_idx = np.rint((target_lon - float(lon[0])) / src_dlon).astype(int)
    lat_idx = np.rint((target_lat - float(lat[0])) / src_dlat).astype(int)
    valid_lon = (lon_idx >= 0) & (lon_idx < src_nx)
    valid_lat = (lat_idx >= 0) & (lat_idx < src_ny)

    target_elev = np.zeros((nx, ny), dtype=np.float64)
    target_elev[np.ix_(valid_lon, valid_lat)] = elev[np.ix_(lon_idx[valid_lon], lat_idx[valid_lat])]

    pad_w = int(np.argmax(valid_lon)) if valid_lon.any() else nx
    pad_e = int(nx - np.flatnonzero(valid_lon)[-1] - 1) if valid_lon.any() else nx
    pad_s = int(np.argmax(valid_lat)) if valid_lat.any() else ny
    pad_n = int(ny - np.flatnonzero(valid_lat)[-1] - 1) if valid_lat.any() else ny
    return target_lon.astype(np.float32), target_lat.astype(np.float32), target_elev, dlon, dlat, (pad_w, pad_e, pad_s, pad_n), (center_lon, center_lat)


def format_meters(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}".replace(".", "p")


def default_output_path(nx: int, ny: int, dx: float, dy: float, nk2: int) -> Path:
    dx_text = format_meters(dx)
    if np.isclose(dx, dy):
        spacing_text = f"dx{dx_text}m"
    else:
        spacing_text = f"dx{dx_text}m_dy{format_meters(dy)}m"
    return Path(f"TOPO_nx{nx}_ny{ny}_{spacing_text}_{nk2}levs.nc")


def add_coord_var(ds: Dataset, name: str, dtype: str, dims: tuple[str, ...], attrs: dict[str, str]):
    var = ds.createVariable(name, dtype, dims)
    for key, value in attrs.items():
        setattr(var, key, value)
    return var


def write_topo_nc(
    output: Path,
    lon: np.ndarray,
    lat: np.ndarray,
    zt: np.ndarray,
    zz: np.ndarray,
    elev_m: np.ndarray,
    topo: np.ndarray,
    dz: float,
    dz1: float,
    domain: float,
    zb: float,
    vertical_mode: str,
    dlon: float,
    dlat: float,
    dx: float,
    dy: float,
    padding: tuple[int, int, int, int],
) -> None:
    nx, ny = elev_m.shape
    nk2 = len(zt)
    land = elev_m > 0.0

    height = np.zeros((nx, ny), dtype=np.float32)
    k_index = np.maximum(topo.astype(np.int32), 1) - 1
    height[:, :] = (zz[k_index] / 1000.0).astype(np.float32)
    height[~land] = 0.0

    mask = np.ones((nx, ny, nk2), dtype=np.int8)
    for i, j in np.argwhere(np.ones((nx, ny), dtype=bool)):
        k = int(max(topo[i, j], 1))
        mask[i, j, :k] = 0

    fields = {}
    for name, ocean_value in OCEAN.items():
        dtype = np.int32 if name in {"lu", "slope", "soil"} else np.float32
        arr = np.full((nx, ny), ocean_value, dtype=dtype)
        arr[land] = EVERGREEN_BROADLEAF_FOREST[name]
        fields[name] = arr

    with Dataset(output, "w", format="NETCDF3_64BIT_OFFSET") as ds:
        ds.createDimension("lon", nx)
        ds.createDimension("lat", ny)
        ds.createDimension("lev", nk2)
        ds.createDimension("time", 1)

        ds.source = "PuertoRico_Elevation.txt"
        ds.note = "Ocean where elevation <= 0 m; evergreen broadleaf forest where elevation > 0 m."
        ds.vertical_coordinate = vertical_mode
        ds.DZ = dz
        ds.DZ1 = dz1
        ds.DOMAIN = domain
        ds.ZB = zb
        ds.DLON = dlon
        ds.DLAT = dlat
        ds.DX = dx
        ds.DY = dy
        ds.pad_west = padding[0]
        ds.pad_east = padding[1]
        ds.pad_south = padding[2]
        ds.pad_north = padding[3]

        add_coord_var(ds, "lon", "f4", ("lon",), {
            "axis": "X",
            "long_name": "longitude",
            "units": "degrees_east",
        })[:] = lon
        add_coord_var(ds, "lat", "f4", ("lat",), {
            "axis": "Y",
            "long_name": "latitude",
            "units": "degrees_north",
        })[:] = lat
        add_coord_var(ds, "lev", "f4", ("lev",), {
            "axis": "Z",
            "long_name": "altitude (m)",
            "units": "m",
            "positive": "up",
        })[:] = zt.astype(np.float32)
        add_coord_var(ds, "time", "f4", ("time",), {
            "axis": "T",
            "long_name": "time",
            "standard_name": "time",
            "units": "hours since 2000-01-01 00:00",
        })[:] = np.array([0.0], dtype=np.float32)

        variables = [
            ("mask", "i1", ("lev", "lat", "lon"), np.transpose(mask, (2, 1, 0)), "mask of thermodynamic variables", "T/F"),
            ("topo", "f8", ("lat", "lon"), topo.astype(np.float64).T, "terrian height in VVM", "grid"),
            ("height", "f4", ("lat", "lon"), height.T, "terrian height", "km"),
            ("albedo", "f4", ("lat", "lon"), fields["albedo"].T, "surface albedo", "%"),
            ("gvf", "f4", ("lat", "lon"), fields["gvf"].T, "green vegetation fraction", "%"),
            ("lai", "f4", ("lat", "lon"), fields["lai"].T, "leaf area index", "m^2 m^-2"),
            ("lu", "i4", ("lat", "lon"), fields["lu"].T, "land type", "type"),
            ("shdmax", "f4", ("lat", "lon"), fields["shdmax"].T, "maximum vegetation fraction", "%"),
            ("shdmin", "f4", ("lat", "lon"), fields["shdmin"].T, "minimum vegetation fraction", "%"),
            ("slope", "i4", ("lat", "lon"), fields["slope"].T, " surface slope type", "type"),
            ("soil", "i4", ("lat", "lon"), fields["soil"].T, "land soil type", "type"),
        ]

        for name, dtype, dims, values, long_name, units in variables:
            var = ds.createVariable(name, dtype, dims)
            var.long_name = long_name
            var.units = units
            var[:] = values


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--elevation", type=Path, default=Path("PuertoRico_Elevation.txt"))
    parser.add_argument("--output", type=Path, default=None, help="Output NetCDF path. Default includes nx, ny, dx, and levels.")
    parser.add_argument("--nx", "--x", dest="nx", type=int, default=1024, help="Output zonal grid size.")
    parser.add_argument("--ny", "--y", dest="ny", type=int, default=768, help="Output meridional grid size.")
    parser.add_argument("--dx", type=float, default=500.0, help="Output zonal grid spacing in meters.")
    parser.add_argument("--dy", type=float, default=None, help="Output meridional grid spacing in meters. Defaults to --dx.")
    parser.add_argument("--nk2", "--levs", dest="nk2", type=int, default=75, help="Number of VVM vertical levels.")
    parser.add_argument(
        "--vertical-mode",
        choices=("taiwanvvm-100m", "stretched"),
        default="stretched",
        help="taiwanvvm-100m keeps 100 m spacing near the surface then stretches aloft; stretched uses only the base TaiwanVVM transform.",
    )
    parser.add_argument("--dz", type=float, default=250.0)
    parser.add_argument("--dz1", type=float, default=100.0)
    parser.add_argument("--domain", type=float, default=15000.0)
    parser.add_argument("--zb", type=float, default=0.0)
    parser.add_argument(
        "--keep-islands",
        action="store_true",
        help="Keep all positive-elevation islands. Default keeps only the largest land component.",
    )
    args = parser.parse_args()
    if args.dy is None:
        args.dy = args.dx
    if args.output is None:
        args.output = default_output_path(args.nx, args.ny, args.dx, args.dy, args.nk2)

    lon, lat, elev = load_elevation(args.elevation)
    source_shape = elev.shape
    lon, lat, elev, dlon, dlat, padding, source_center = center_on_domain(lon, lat, elev, args.nx, args.ny, args.dx, args.dy)
    original_land_count = int((elev > 0.0).sum())
    if not args.keep_islands:
        main_island = largest_land_component(elev > 0.0)
        elev = np.where(main_island, elev, 0.0)

    zz, zt = vertical_coordinate(args.nk2, args.dz, args.dz1, args.domain, args.zb, args.vertical_mode)
    land = elev > 0.0
    topo = topo_from_height(elev, zz)
    topo[land & (topo < 2.0)] = 2.0
    topo, filled_caves, cave_iterations = fill_single_grid_caves(topo, land)
    topo[land & (topo < 2.0)] = 2.0
    topo[~land] = 0.0
    remaining_caves = int(single_grid_caves(topo, land).sum())

    write_topo_nc(
        args.output, lon, lat, zt, zz, elev, topo, args.dz, args.dz1, args.domain,
        args.zb, args.vertical_mode, dlon, dlat, args.dx, args.dy, padding,
    )

    print(f"wrote {args.output}")
    print(f"source grid lon={source_shape[0]} lat={source_shape[1]}")
    print(f"grid lon={len(lon)} lat={len(lat)} lev={args.nk2} vertical_mode={args.vertical_mode}")
    domain_center = (float((lon[0] + lon[-1]) / 2.0), float((lat[0] + lat[-1]) / 2.0))
    print(f"dx={args.dx:g} dy={args.dy:g} meters")
    print(f"dlon={dlon:.7f} dlat={dlat:.7f} degrees")
    print(f"source center lon/lat={source_center[0]:.6f} {source_center[1]:.6f}")
    print(f"domain center lon/lat={domain_center[0]:.6f} {domain_center[1]:.6f}")
    print(f"center offset lon/lat={domain_center[0] - source_center[0]:.8f} {domain_center[1] - source_center[1]:.8f} degrees")
    print(f"ocean padding west/east/south/north={padding}")
    print(f"land cells={int((elev > 0).sum())} ocean cells={int((elev <= 0).sum())}")
    if not args.keep_islands:
        print(f"removed island land cells={original_land_count - int((elev > 0).sum())}")
    print(f"filled single-grid land caves={filled_caves} iterations={cave_iterations}")
    print(f"remaining single-grid land caves={remaining_caves}")
    print(f"topo min={topo.min():.0f} max={topo.max():.0f}")


if __name__ == "__main__":
    main()
