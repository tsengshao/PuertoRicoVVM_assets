#!/usr/bin/env python3
"""
Download a University of Wyoming sounding and convert it to a VVM initial
sounding file.

Output columns:
    P[hPa] T[K] Qv[g/kg] U[m/s] V[m/s]

Example:
    /home/umbrella0c/miniconda3/envs/py311/bin/python uwyo_to_vvm_sounding.py \
        --year 2021 --month 08 --from-time 2612 --to-time 2612 --stnm 78526

    writes:
        uwyo_78526_20210826_12_vvm.txt
        uwyo_78526_20210826_12_raw.html

    If a FROM/TO range returns only one observation, the default output name
    uses that observation time instead of the requested range.
"""

from __future__ import annotations

import argparse
import math
import sys
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


UWYO_URL = "https://weather.uwyo.edu/cgi-bin/sounding"
KNOT_TO_MPS = 0.514444
EPSILON = 0.622
MISSING_VALUES = {-9999.0, -999.0, 99999.0, 9999.0}
COLUMN_SLICES = {
    "PRES": (0, 7),
    "HGHT": (7, 14),
    "TEMP": (14, 21),
    "DWPT": (21, 28),
    "RELH": (28, 35),
    "MIXR": (35, 42),
    "DRCT": (42, 49),
    "SKNT": (49, 56),
    "THTA": (56, 63),
    "THTE": (63, 70),
    "THTV": (70, 77),
}


@dataclass(frozen=True)
class UwyoLevel:
    pressure_hpa: float
    height_m: float | None
    temperature_c: float
    dewpoint_c: float
    mixing_ratio_gkg: float
    wind_dir_deg: float
    wind_speed_kt: float


@dataclass(frozen=True)
class VvmLevel:
    pressure_hpa: float
    temperature_k: float
    qv_gkg: float
    u_ms: float
    v_ms: float


@dataclass(frozen=True)
class SkippedRow:
    line_number: int
    pressure_text: str
    reason: str
    raw_line: str


@dataclass(frozen=True)
class ParseResult:
    levels: list[UwyoLevel]
    skipped_rows: list[SkippedRow]


def build_uwyo_url(
    *,
    region: str,
    year: int,
    month: int,
    from_time: str,
    to_time: str,
    station: str,
) -> str:
    query = {
        "region": region,
        "TYPE": "TEXT:LIST",
        "YEAR": f"{year:04d}",
        "MONTH": f"{month:02d}",
        "FROM": from_time,
        "TO": to_time,
        "STNM": station,
    }
    return f"{UWYO_URL}?{urllib.parse.urlencode(query)}"


def download_text(url: str, timeout: int = 30) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "uwyo-to-vvm-sounding/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def _to_float(text: str) -> float | None:
    text = text.strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if value in MISSING_VALUES or math.isnan(value):
        return None
    return value


def parse_fixed_width_fields(raw_line: str) -> dict[str, str]:
    return {
        name: raw_line[start:end].strip()
        for name, (start, end) in COLUMN_SLICES.items()
    }


def parse_uwyo_text(text: str) -> ParseResult:
    """Parse the fixed-width-ish UWYO text table.

    UWYO rows usually contain:
    PRES HGHT TEMP DWPT RELH MIXR DRCT SKNT THTA THTE THTV
    """
    levels: list[UwyoLevel] = []
    skipped_rows: list[SkippedRow] = []
    in_table = False

    required_columns = ("PRES", "TEMP", "DWPT", "MIXR", "DRCT", "SKNT")

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("PRES") and "TEMP" in line and "DWPT" in line:
            in_table = True
            continue
        if not in_table:
            continue
        if set(line) <= {"-"}:
            continue
        if line.startswith("Station information") or line.startswith("</PRE>"):
            break

        fields = parse_fixed_width_fields(raw_line)
        if fields["PRES"] == "hPa":
            continue

        parsed = {name: _to_float(fields[name]) for name in required_columns}
        missing_columns = [name for name, value in parsed.items() if value is None]
        if missing_columns:
            skipped_rows.append(
                SkippedRow(
                    line_number=line_number,
                    pressure_text=fields["PRES"] or "?",
                    reason="missing/invalid " + ",".join(missing_columns),
                    raw_line=raw_line,
                )
            )
            continue

        pressure = parsed["PRES"]
        height = _to_float(fields["HGHT"])
        temperature = parsed["TEMP"]
        dewpoint = parsed["DWPT"]
        mixing_ratio = parsed["MIXR"]
        wind_dir = parsed["DRCT"]
        wind_speed = parsed["SKNT"]

        levels.append(
            UwyoLevel(
                pressure_hpa=pressure,
                height_m=height,
                temperature_c=temperature,
                dewpoint_c=dewpoint,
                mixing_ratio_gkg=mixing_ratio,
                wind_dir_deg=wind_dir,
                wind_speed_kt=wind_speed,
            )
        )

    if not levels:
        raise ValueError("No sounding table rows were found in the UWYO response.")

    return ParseResult(levels=levels, skipped_rows=skipped_rows)


def print_skipped_rows(skipped_rows: list[SkippedRow]) -> None:
    if not skipped_rows:
        print("skipped rows: 0")
        return

    reason_counts = Counter(row.reason for row in skipped_rows)
    print(f"skipped rows: {len(skipped_rows)}")
    for reason, count in reason_counts.items():
        print(f"  - {reason}: {count}")

    preview_count = min(5, len(skipped_rows))
    print(f"  first {preview_count} skipped row(s):")
    for row in skipped_rows[:preview_count]:
        print(
            f"    line {row.line_number}, PRES={row.pressure_text}: "
            f"{row.reason}; {row.raw_line.strip()}"
        )


def vapor_pressure_from_dewpoint_hpa(dewpoint_c: float) -> float:
    """Bolton-style Magnus formula over liquid water, in hPa."""
    return 6.112 * math.exp((17.67 * dewpoint_c) / (dewpoint_c + 243.5))


def specific_humidity_from_dewpoint_gkg(pressure_hpa: float, dewpoint_c: float) -> float:
    """Backup formula: derive specific humidity from pressure and dewpoint."""
    vapor_pressure = vapor_pressure_from_dewpoint_hpa(dewpoint_c)
    if vapor_pressure >= pressure_hpa:
        raise ValueError(
            f"Vapor pressure {vapor_pressure:.3f} hPa is not below "
            f"pressure {pressure_hpa:.3f} hPa."
        )
    mixing_ratio_kgkg = EPSILON * vapor_pressure / (pressure_hpa - vapor_pressure)
    specific_humidity_kgkg = mixing_ratio_kgkg / (1.0 + mixing_ratio_kgkg)
    return specific_humidity_kgkg * 1000.0


def specific_humidity_from_mixing_ratio_gkg(mixing_ratio_gkg: float) -> float:
    """Convert UWYO MIXR from mixing ratio r [g/kg] to specific humidity q [g/kg].

    r is water vapor mass / dry-air mass. VVM Qv here is specific humidity,
    q = r / (1 + r), with r first converted to kg/kg.
    """
    mixing_ratio_kgkg = mixing_ratio_gkg / 1000.0
    specific_humidity_kgkg = mixing_ratio_kgkg / (1.0 + mixing_ratio_kgkg)
    return specific_humidity_kgkg * 1000.0


def wind_components_ms(direction_deg: float, speed_kt: float) -> tuple[float, float]:
    """Convert meteorological wind direction and speed to u/v components.

    UWYO direction is the direction wind comes from. Positive u is eastward,
    positive v is northward.
    """
    speed_ms = speed_kt * KNOT_TO_MPS
    radians = math.radians(direction_deg)
    u = -speed_ms * math.sin(radians)
    v = -speed_ms * math.cos(radians)
    return u, v


def convert_to_vvm(levels: list[UwyoLevel]) -> list[VvmLevel]:
    converted: list[VvmLevel] = []
    seen_pressures: set[float] = set()

    for level in levels:
        pressure_key = round(level.pressure_hpa, 4)
        if pressure_key in seen_pressures:
            continue
        seen_pressures.add(pressure_key)

        u_ms, v_ms = wind_components_ms(level.wind_dir_deg, level.wind_speed_kt)
        converted.append(
            VvmLevel(
                pressure_hpa=level.pressure_hpa,
                temperature_k=level.temperature_c + 273.15,
                #qv_gkg=specific_humidity_from_mixing_ratio_gkg(level.mixing_ratio_gkg),
                qv_gkg=level.mixing_ratio_gkg,
                u_ms=u_ms,
                v_ms=v_ms,
            )
        )

    return converted


def format_vvm(levels: list[VvmLevel]) -> str:
    lines = ["    P[hPa]       T[K]   Qv[g/kg]     U[m/s]     V[m/s]"]
    for level in levels:
        lines.append(
            f"{level.pressure_hpa:10.4f}"
            f"{level.temperature_k:11.4f}"
            f"{level.qv_gkg:11.4f}"
            f"{level.u_ms:11.4f}"
            f"{level.v_ms:11.4f}"
        )
    return "\n".join(lines) + "\n"


def positive_int(text: str) -> int:
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return value


def _first_query_value(query: dict[str, list[str]], name: str) -> str | None:
    values = query.get(name)
    if not values:
        return None
    return values[0]


def sounding_request_parts(args: argparse.Namespace) -> tuple[int, int, str, str, str]:
    year = args.year
    month = args.month
    from_time = args.from_time
    to_time = args.to_time
    station = args.stnm

    if args.url:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(args.url).query)
        year_text = _first_query_value(query, "YEAR")
        month_text = _first_query_value(query, "MONTH")
        from_text = _first_query_value(query, "FROM")
        to_text = _first_query_value(query, "TO")
        station_text = _first_query_value(query, "STNM")

        if year_text is not None:
            year = int(year_text)
        if month_text is not None:
            month = int(month_text)
        if from_text is not None:
            from_time = from_text
        if to_text is not None:
            to_time = to_text
        if station_text is not None:
            station = station_text

    return year, month, from_time, to_time, station


def _time_label(year: int, month: int, ddhh: str) -> str:
    if len(ddhh) != 4 or not ddhh.isdigit():
        safe_ddhh = "".join(ch if ch.isalnum() else "_" for ch in ddhh)
        return f"{year:04d}{month:02d}_{safe_ddhh}"
    return f"{year:04d}{month:02d}{ddhh[:2]}_{ddhh[2:]}"


def parse_observation_times(text: str) -> list[str]:
    observation_times: list[str] = []
    seen: set[str] = set()

    for raw_line in text.splitlines():
        if "Observation time:" not in raw_line:
            continue
        observation_time = raw_line.split("Observation time:", 1)[1].strip()
        if observation_time and observation_time not in seen:
            seen.add(observation_time)
            observation_times.append(observation_time)

    return observation_times


def _observation_time_label(observation_time: str, fallback_year: int) -> str | None:
    date_text, separator, time_text = observation_time.partition("/")
    if separator != "/" or len(date_text) != 6 or len(time_text) < 2:
        return None
    if not date_text.isdigit() or not time_text[:2].isdigit():
        return None

    yy = int(date_text[:2])
    century = fallback_year - (fallback_year % 100)
    full_year = century + yy
    if full_year - fallback_year > 50:
        full_year -= 100
    elif fallback_year - full_year > 50:
        full_year += 100

    return f"{full_year:04d}{date_text[2:4]}{date_text[4:6]}_{time_text[:2]}"


def default_output_stem(
    *,
    year: int,
    month: int,
    from_time: str,
    to_time: str,
    station: str,
    observation_times: list[str] | None = None,
) -> str:
    if observation_times and len(observation_times) == 1:
        observation_label = _observation_time_label(observation_times[0], year)
        if observation_label is not None:
            return f"uwyo_{station}_{observation_label}"

    from_label = _time_label(year, month, from_time)
    if to_time == from_time:
        time_label = from_label
    else:
        time_label = f"{from_label}_to_{_time_label(year, month, to_time)}"
    return f"uwyo_{station}_{time_label}"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download UWYO sounding text and write VVM initial sounding format."
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--url",
        help="Full UWYO sounding URL. fbclid and other unknown query parameters are harmless.",
    )
    source.add_argument(
        "--input-html",
        type=Path,
        help="Read a previously downloaded UWYO text/HTML file instead of downloading.",
    )

    parser.add_argument("--region", default="naconf", help="UWYO region code.")
    parser.add_argument("--year", type=positive_int, default=2021)
    parser.add_argument("--month", type=positive_int, default=8)
    parser.add_argument("--from-time", default="2612", help="UWYO FROM value, e.g. 2612.")
    parser.add_argument("--to-time", default="2612", help="UWYO TO value, e.g. 2612.")
    parser.add_argument("--stnm", default="78526", help="UWYO station number.")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output VVM sounding text file. Default is based on station and time.",
    )
    parser.add_argument(
        "--save-raw",
        type=Path,
        default=None,
        help="Path to save the raw UWYO response for inspection. Default is based on station and time.",
    )
    parser.add_argument("--timeout", type=positive_int, default=30)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    year, month, from_time, to_time, station = sounding_request_parts(args)
    if args.input_html:
        raw_text = args.input_html.read_text(encoding="utf-8", errors="replace")
        url = None
    else:
        url = args.url or build_uwyo_url(
            region=args.region,
            year=year,
            month=month,
            from_time=from_time,
            to_time=to_time,
            station=station,
        )
        raw_text = download_text(url, timeout=args.timeout)

    output_stem = default_output_stem(
        year=year,
        month=month,
        from_time=from_time,
        to_time=to_time,
        station=station,
        observation_times=parse_observation_times(raw_text),
    )

    if args.output is None:
        args.output = Path(f"{output_stem}_vvm.txt")
    if args.save_raw is None:
        args.save_raw = Path(f"{output_stem}_raw.html")

    args.save_raw.parent.mkdir(parents=True, exist_ok=True)
    args.save_raw.write_text(raw_text, encoding="utf-8")

    parse_result = parse_uwyo_text(raw_text)
    vvm_levels = convert_to_vvm(parse_result.levels)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(format_vvm(vvm_levels), encoding="utf-8")

    source = url if url else str(args.input_html)
    print(f"source: {source}")
    print(f"levels: {len(vvm_levels)}")
    print_skipped_rows(parse_result.skipped_rows)
    print(f"raw: {args.save_raw}")
    print(f"output: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
