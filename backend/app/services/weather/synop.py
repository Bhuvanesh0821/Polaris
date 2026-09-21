"""FM-12 SYNOP decoder.

Indian Antarctic stations (Maitri, WMO 89514) transmit surface observations
as FM-12 SYNOP bulletins onto the WMO Global Telecommunication System. This
module decodes the subset of groups POLARIS needs.

A real bulletin from Maitri looks like:

    AAXX 21124 89514 32698 21622 11193 49646 57020 82020 333 11191 21231 82440=

    AAXX   land-station SYNOP
    21124  YY=21 (day)  GG=12 (hour UTC)  iw=4 (wind in knots, measured)
    89514  WMO station index (Maitri)
    32698  iR iX h VV     precip indicator / station type / cloud base / vis
    21622  N dd ff        cloud 2/8, wind from 160 deg at 22 kt
    11193  1 sn TTT       air temperature -19.3 C
    49646  4 PPPP         mean sea-level pressure 964.6 hPa
    57020  5 a ppp        3-hour pressure tendency
    82020  8 Nh Cl Cm Ch  cloud detail
    333    section 3 follows (max/min temperature, etc.)

Design notes
------------
* Decoding is strictly positional for section 0 and keyed on the leading digit
  for section 1, which is how FM-12 is actually specified. Unknown or
  malformed groups are skipped rather than aborting the whole report - a
  partially decoded real observation is still a real observation.
* Every field is Optional. A missing group yields None; it is never guessed,
  substituted or interpolated.
* `//` and `/` mean "not observed" and decode to None.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

KNOTS_TO_MS = 0.5144444

#: WMO code table 4677 (present weather ww) - abbreviated to the codes that
#: actually matter for polar station operations.
WW_DESCRIPTIONS: dict[int, str] = {
    0: "Clear sky",
    1: "Clouds dissolving",
    2: "State of sky unchanged",
    3: "Clouds developing",
    4: "Visibility reduced by smoke",
    10: "Mist",
    11: "Shallow fog patches",
    12: "Shallow continuous fog",
    18: "Squalls",
    22: "Snow (ended in last hour)",
    26: "Snow shower (ended in last hour)",
    28: "Fog (ended in last hour)",
    36: "Slight/moderate drifting snow, below eye level",
    37: "Heavy drifting snow, below eye level",
    38: "Slight/moderate blowing snow, above eye level",
    39: "Heavy blowing snow, above eye level",
    41: "Fog in patches",
    42: "Fog, sky visible, thinning",
    44: "Fog, sky obscured, no change",
    45: "Fog",
    48: "Fog depositing rime",
    49: "Fog depositing rime, sky obscured",
    50: "Drizzle",
    60: "Rain",
    70: "Intermittent slight snowfall",
    71: "Continuous slight snowfall",
    72: "Intermittent moderate snowfall",
    73: "Continuous moderate snowfall",
    74: "Intermittent heavy snowfall",
    75: "Continuous heavy snowfall",
    76: "Diamond dust",
    77: "Snow grains",
    78: "Isolated snow crystals",
    79: "Ice pellets",
    83: "Slight rain/snow shower",
    85: "Slight snow shower",
    86: "Moderate/heavy snow shower",
    87: "Slight snow pellet shower",
    88: "Moderate/heavy snow pellet shower",
    93: "Thunderstorm with snow/hail, slight",
    94: "Thunderstorm with snow/hail, heavy",
}

#: Blowing/drifting snow and fog codes that matter for blizzard detection.
BLOWING_SNOW_CODES = {36, 37, 38, 39}
SNOWFALL_CODES = set(range(70, 80)) | {85, 86, 87, 88}


@dataclass
class SynopReport:
    """One decoded FM-12 SYNOP observation. Every field is measured or None."""

    station_index: str
    observed_at: datetime
    raw: str

    temperature_c: float | None = None
    dewpoint_c: float | None = None
    humidity_pct: float | None = None
    wind_speed_ms: float | None = None
    wind_direction_deg: float | None = None
    wind_is_estimated: bool = False
    pressure_station_hpa: float | None = None
    pressure_msl_hpa: float | None = None
    pressure_tendency_hpa: float | None = None
    cloud_cover_octas: int | None = None
    cloud_cover_pct: float | None = None
    visibility_m: float | None = None
    present_weather_code: int | None = None
    present_weather: str | None = None
    precipitation_mm: float | None = None
    temp_max_c: float | None = None
    temp_min_c: float | None = None
    is_blowing_snow: bool = False
    is_snowfall: bool = False

    decoded_groups: list[str] = field(default_factory=list)
    undecoded_groups: list[str] = field(default_factory=list)

    @property
    def is_usable(self) -> bool:
        """A report POLARIS can drive the energy model with."""
        return self.temperature_c is not None and self.wind_speed_ms is not None


def _num(token: str) -> int | None:
    """Return int(token) or None if the group carries missing-data slashes."""
    if not token or "/" in token:
        return None
    if not token.isdigit():
        return None
    return int(token)


def _signed_temp(sn: str, value: str) -> float | None:
    """1snTTT / 2snTdTdTd -> tenths of a degree, sn=1 means negative."""
    v = _num(value)
    if v is None or sn not in ("0", "1"):
        return None
    t = v / 10.0
    return -t if sn == "1" else t


def relative_humidity(temp_c: float, dewpoint_c: float) -> float:
    """Magnus-Tetens RH from air and dewpoint temperature (over water)."""
    a, b = 17.625, 243.04
    alpha_d = (a * dewpoint_c) / (b + dewpoint_c)
    alpha_t = (a * temp_c) / (b + temp_c)
    rh = 100.0 * math.exp(alpha_d - alpha_t)
    return max(0.0, min(100.0, rh))


def _decode_visibility(vv: int | None) -> float | None:
    """WMO code table 4377 -> metres."""
    if vv is None:
        return None
    if vv == 0:
        return 50.0
    if 1 <= vv <= 50:
        return vv * 100.0
    if 56 <= vv <= 80:
        return (vv - 50) * 1000.0
    if 81 <= vv <= 88:
        return 30000.0 + (vv - 80) * 5000.0
    if vv == 89:
        return 70000.0
    # 90-99 is the coarse scale used when no precise measurement exists
    coarse = {90: 25.0, 91: 50.0, 92: 200.0, 93: 500.0, 94: 1000.0,
              95: 2000.0, 96: 4000.0, 97: 10000.0, 98: 20000.0, 99: 50000.0}
    return coarse.get(vv)


def _report_timestamp(day: int, hour: int, reference: datetime) -> datetime:
    """Resolve SYNOP's day-of-month + hour against a reference instant."""
    ref = reference.astimezone(timezone.utc)
    for month_shift in (0, -1):
        base = ref.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if month_shift:
            base = (base - timedelta(days=1)).replace(day=1)
        try:
            candidate = base.replace(day=day, hour=hour % 24)
        except ValueError:
            continue
        # A report can never be meaningfully in the future.
        if candidate <= ref + timedelta(hours=2):
            return candidate
    return ref.replace(minute=0, second=0, microsecond=0)


def decode_synop(raw: str, reference_time: datetime | None = None) -> SynopReport | None:
    """Decode one FM-12 SYNOP bulletin. Returns None if it is not decodable."""
    if not raw:
        return None
    text = raw.strip().rstrip("=").strip()
    text = re.sub(r"\s+", " ", text)
    groups = text.split(" ")

    # --- Section 0 --------------------------------------------------------
    if not groups or groups[0].upper() not in ("AAXX", "BBXX"):
        return None
    if len(groups) < 4:
        return None

    header = groups[1]
    if len(header) != 5 or not header[:4].isdigit():
        return None
    day, hour, iw_char = int(header[0:2]), int(header[2:4]), header[4]
    station_index = groups[2]
    if not station_index.isdigit():
        return None

    ref = reference_time or datetime.now(timezone.utc)
    observed_at = _report_timestamp(day, hour, ref)

    rpt = SynopReport(station_index=station_index, observed_at=observed_at, raw=text)

    # iw: 0/1 -> m/s, 3/4 -> knots. 0 and 3 mean estimated rather than measured.
    wind_in_knots = iw_char in ("3", "4")
    rpt.wind_is_estimated = iw_char in ("0", "3")

    body = groups[3:]

    # --- Section 1 --------------------------------------------------------
    section = 1
    for idx, g in enumerate(body):
        if g in ("333", "444", "555"):
            section = int(g[0])
            continue
        if len(g) != 5:
            rpt.undecoded_groups.append(g)
            continue

        if section == 1:
            # iRiXhVV is positional: it is the first group of section 1.
            if idx == 0:
                vv = _num(g[3:5])
                rpt.visibility_m = _decode_visibility(vv)
                rpt.decoded_groups.append(f"iRiXhVV={g}")
                continue
            # Nddff is the second group of section 1.
            if idx == 1:
                n = _num(g[0])
                if n is not None and n <= 8:
                    rpt.cloud_cover_octas = n
                    rpt.cloud_cover_pct = round(n / 8.0 * 100.0, 1)
                dd = _num(g[1:3])
                ff = _num(g[3:5])
                if dd is not None and 1 <= dd <= 36:
                    rpt.wind_direction_deg = float(dd * 10)
                elif dd == 0:
                    rpt.wind_direction_deg = 0.0  # calm
                if ff is not None:
                    speed = float(ff)
                    rpt.wind_speed_ms = round(
                        speed * KNOTS_TO_MS if wind_in_knots else speed, 2
                    )
                rpt.decoded_groups.append(f"Nddff={g}")
                continue

            lead = g[0]
            if lead == "1":
                rpt.temperature_c = _signed_temp(g[1], g[2:5])
                rpt.decoded_groups.append(f"1snTTT={g}")
            elif lead == "2":
                if g[1] == "9":  # 29UUU is relative humidity, not dewpoint
                    uuu = _num(g[2:5])
                    if uuu is not None and uuu <= 100:
                        rpt.humidity_pct = float(uuu)
                else:
                    rpt.dewpoint_c = _signed_temp(g[1], g[2:5])
                rpt.decoded_groups.append(f"2snTd={g}")
            elif lead == "3":
                p = _num(g[1:5])
                if p is not None:
                    val = p / 10.0
                    rpt.pressure_station_hpa = val + 1000.0 if val < 500 else val
                rpt.decoded_groups.append(f"3P0={g}")
            elif lead == "4":
                p = _num(g[1:5])
                if p is not None:
                    val = p / 10.0
                    rpt.pressure_msl_hpa = val + 1000.0 if val < 500 else val
                rpt.decoded_groups.append(f"4PPPP={g}")
            elif lead == "5":
                ppp = _num(g[2:5])
                a = _num(g[1])
                if ppp is not None and a is not None:
                    change = ppp / 10.0
                    rpt.pressure_tendency_hpa = -change if a >= 5 else change
                rpt.decoded_groups.append(f"5appp={g}")
            elif lead == "6":
                rrr = _num(g[1:4])
                if rrr is not None:
                    if rrr <= 989:
                        rpt.precipitation_mm = float(rrr)
                    elif rrr == 990:
                        rpt.precipitation_mm = 0.0  # trace
                    else:
                        rpt.precipitation_mm = (rrr - 990) / 10.0
                rpt.decoded_groups.append(f"6RRRtR={g}")
            elif lead == "7":
                ww = _num(g[1:3])
                if ww is not None:
                    rpt.present_weather_code = ww
                    rpt.present_weather = WW_DESCRIPTIONS.get(ww)
                    rpt.is_blowing_snow = ww in BLOWING_SNOW_CODES
                    rpt.is_snowfall = ww in SNOWFALL_CODES
                rpt.decoded_groups.append(f"7wwW1W2={g}")
            elif lead == "8":
                rpt.decoded_groups.append(f"8NhClCmCh={g}")
            else:
                rpt.undecoded_groups.append(g)

        elif section == 3:
            lead = g[0]
            if lead == "1":
                rpt.temp_max_c = _signed_temp(g[1], g[2:5])
            elif lead == "2":
                rpt.temp_min_c = _signed_temp(g[1], g[2:5])
            else:
                rpt.undecoded_groups.append(g)
        else:
            rpt.undecoded_groups.append(g)

    # Derive RH when the station sent dewpoint instead of an explicit 29UUU.
    if (
        rpt.humidity_pct is None
        and rpt.temperature_c is not None
        and rpt.dewpoint_c is not None
    ):
        rpt.humidity_pct = round(
            relative_humidity(rpt.temperature_c, rpt.dewpoint_c), 1
        )

    return rpt


def parse_ogimet_csv(
    body: str, reference_time: datetime | None = None
) -> list[SynopReport]:
    """Parse OGIMET's getsynop CSV.

    Each line is:  index,YYYY,MM,DD,HH,mm,<raw SYNOP bulletin>
    The embedded timestamp is authoritative, so it overrides the day/hour
    resolved from the bulletin header.
    """
    reports: list[SynopReport] = []
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(",", 6)
        if len(parts) < 7:
            continue
        idx, y, mo, d, hh, mi, raw = parts
        try:
            stamp = datetime(
                int(y), int(mo), int(d), int(hh), int(mi), tzinfo=timezone.utc
            )
        except (ValueError, TypeError):
            continue
        rpt = decode_synop(raw, reference_time=stamp)
        if rpt is None:
            continue
        rpt.observed_at = stamp  # trust OGIMET's explicit timestamp
        rpt.station_index = idx.strip() or rpt.station_index
        reports.append(rpt)
    reports.sort(key=lambda r: r.observed_at)
    return reports
