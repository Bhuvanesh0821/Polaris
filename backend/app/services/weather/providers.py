"""LIVE weather providers.

POLARIS pulls real observations from real sources. Every provider returns
`WeatherReading` objects carrying the raw provider payload, so any value the
dashboard shows can be traced to the bytes the source returned.

Provider chain (lowest `priority` is tried first)
-------------------------------------------------
10  OgimetSynopProvider   AUTHORITATIVE. Maitri (WMO 89514), an Indian
                          Antarctic Programme station, via WMO GTS SYNOP
                          bulletins relayed by OGIMET. 6-hourly. Carries no
                          radiation group and usually no dewpoint.
20  OpenMeteoProvider     Hourly resolution, solar radiation and the forecast
                          horizon the AI models need, at Maitri's real
                          coordinates.
25  MetNorwayProvider     Independent NWP forecast, so a rate-limited
                          Open-Meteo cannot take the forecast offline.
30  NoaaMetarProvider     NOAA Aviation Weather Center METAR from Antarctic
                          aerodromes. Independent failover for the core
                          variables.

There is deliberately no synthetic provider. If every provider fails, the
ingestor records the failure and the API serves the last genuinely observed
row, flagged stale.
"""

from __future__ import annotations

import asyncio
import logging
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import settings
from app.core.station import SITE
from app.models.base import DataProvenance
from app.services.weather.synop import parse_ogimet_csv, relative_humidity

log = logging.getLogger("polaris.weather")

USER_AGENT = "POLARIS/1.0 (polar station energy research)"
KNOTS_TO_MS = 0.5144444


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass
class WeatherReading:
    """One observation or forecast hour from one provider."""

    observed_at: datetime
    provenance: DataProvenance
    source: str
    source_provider: str
    source_station_code: str | None = None

    temperature_c: float | None = None
    apparent_temperature_c: float | None = None
    wind_speed_ms: float | None = None
    wind_gust_ms: float | None = None
    wind_direction_deg: float | None = None
    solar_radiation_wm2: float | None = None
    direct_radiation_wm2: float | None = None
    diffuse_radiation_wm2: float | None = None
    humidity_pct: float | None = None
    pressure_hpa: float | None = None
    cloud_cover_pct: float | None = None
    snowfall_mm: float | None = None
    weather_text: str | None = None
    is_blizzard: bool = False

    raw_payload: dict[str, Any] = field(default_factory=dict)

    @property
    def is_usable(self) -> bool:
        """POLARIS only persists readings with the two variables the energy
        model cannot run without."""
        return self.temperature_c is not None and self.wind_speed_ms is not None

    @property
    def is_forecast(self) -> bool:
        return self.provenance == DataProvenance.REAL_FORECAST


@dataclass
class ProviderOutcome:
    provider_key: str
    provider_label: str
    ok: bool
    readings: list[WeatherReading] = field(default_factory=list)
    endpoint: str = ""
    http_status: int | None = None
    latency_ms: float = 0.0
    attempts: int = 1
    error: str | None = None

    @property
    def usable_readings(self) -> list[WeatherReading]:
        return [r for r in self.readings if r.is_usable]


# ---------------------------------------------------------------------------
# Base provider
# ---------------------------------------------------------------------------


class WeatherProvider(ABC):
    key: str = "base"
    label: str = "Base provider"
    priority: int = 100
    supports_solar_radiation: bool = False
    freshness_window_s: int = 3600
    max_attempts: int = 3
    backoff_base_s: float = 1.5
    #: Rate limits need a far longer wait than a transient network blip, and
    #: they are worth more attempts. This matters on shared-IP hosting (free
    #: PaaS tiers), where another tenant can exhaust a per-IP quota that this
    #: application never comes close to on its own.
    rate_limit_attempts: int = 5
    rate_limit_backoff_s: tuple[float, ...] = (5.0, 15.0, 45.0, 90.0)
    max_retry_after_s: float = 120.0

    def __init__(self, timeout_s: float | None = None) -> None:
        self.timeout_s = timeout_s or settings.weather_timeout_s

    @abstractmethod
    async def _fetch(self, client: httpx.AsyncClient) -> tuple[list[WeatherReading], int, str]:
        """Return (readings, http_status, endpoint). Raise on failure."""

    @staticmethod
    def _retry_after_seconds(exc: httpx.HTTPStatusError) -> float | None:
        """Honour a server-supplied Retry-After, when it gives one."""
        raw = exc.response.headers.get("retry-after")
        if not raw:
            return None
        try:
            return float(raw)  # delta-seconds form
        except ValueError:
            try:  # HTTP-date form
                from email.utils import parsedate_to_datetime

                when = parsedate_to_datetime(raw)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
            except Exception:
                return None

    async def fetch(self) -> ProviderOutcome:
        """Fetch with bounded retry, backing off hard on a rate limit."""
        started = datetime.now(timezone.utc)
        last_error: str | None = None
        status: int | None = None
        endpoint = ""

        attempt = 0
        max_attempts = self.max_attempts
        while attempt < max_attempts:
            attempt += 1
            try:
                async with httpx.AsyncClient(
                    timeout=self.timeout_s,
                    headers={"User-Agent": USER_AGENT},
                    follow_redirects=True,
                ) as client:
                    readings, status, endpoint = await self._fetch(client)
                latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
                log.info(
                    "%s: %d readings in %.0f ms (attempt %d)",
                    self.key, len(readings), latency, attempt,
                )
                return ProviderOutcome(
                    provider_key=self.key,
                    provider_label=self.label,
                    ok=True,
                    readings=readings,
                    endpoint=endpoint,
                    http_status=status,
                    latency_ms=round(latency, 1),
                    attempts=attempt,
                )
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                rate_limited = False
                if isinstance(exc, httpx.HTTPStatusError):
                    status = exc.response.status_code
                    rate_limited = status == 429

                if rate_limited:
                    # Allow more attempts and much longer waits than a plain
                    # network error deserves.
                    max_attempts = max(max_attempts, self.rate_limit_attempts)
                    idx = min(attempt - 1, len(self.rate_limit_backoff_s) - 1)
                    delay = self.rate_limit_backoff_s[idx]
                    server_hint = self._retry_after_seconds(exc)
                    if server_hint is not None:
                        delay = min(max(server_hint, 1.0), self.max_retry_after_s)
                    log.warning(
                        "%s rate-limited (429) on attempt %d/%d; waiting %.0fs. "
                        "On shared-IP hosting this quota can be consumed by "
                        "other tenants.", self.key, attempt, max_attempts, delay,
                    )
                else:
                    delay = self.backoff_base_s ** attempt
                    log.warning("%s attempt %d/%d failed: %s",
                                self.key, attempt, max_attempts, last_error)

                if attempt < max_attempts:
                    # Jitter so concurrent retries do not synchronise.
                    await asyncio.sleep(delay * (0.8 + 0.4 * random.random()))

        latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
        return ProviderOutcome(
            provider_key=self.key,
            provider_label=self.label,
            ok=False,
            endpoint=endpoint,
            http_status=status,
            latency_ms=round(latency, 1),
            attempts=self.max_attempts,
            error=last_error,
        )


# ---------------------------------------------------------------------------
# 1. OGIMET / WMO SYNOP  -  Maitri, Indian Antarctic Programme
# ---------------------------------------------------------------------------


class OgimetSynopProvider(WeatherProvider):
    """Real surface observations from an Indian Antarctic station.

    Maitri transmits FM-12 SYNOP bulletins onto the WMO Global
    Telecommunication System; OGIMET relays them verbatim. This is as close to
    first-party Indian Antarctic observation data as is publicly retrievable.
    """

    key = "ogimet_synop"
    label = "IMD/NCPOR Maitri (WMO 89514) - SYNOP via OGIMET"
    priority = 10
    supports_solar_radiation = False
    #: Maitri reports at 00/06/12/18 UTC, so allow a generous window.
    freshness_window_s = 8 * 3600

    URL = "https://www.ogimet.com/cgi-bin/getsynop"

    def __init__(self, wmo_index: str | None = None, lookback_h: int = 48,
                 station_label: str | None = None, **kw) -> None:
        super().__init__(**kw)
        self.wmo_index = wmo_index or SITE.wmo_index
        self.lookback_h = lookback_h
        if station_label:
            self.label = station_label

    async def _fetch(self, client):
        now = datetime.now(timezone.utc)
        params = {
            "block": self.wmo_index,
            "begin": (now - timedelta(hours=self.lookback_h)).strftime("%Y%m%d%H%M"),
            "end": now.strftime("%Y%m%d%H%M"),
        }
        resp = await client.get(self.URL, params=params)
        resp.raise_for_status()
        body = resp.text

        if "wait" in body.lower()[:200] and "," not in body[:200]:
            raise RuntimeError(f"OGIMET throttling response: {body[:120]!r}")

        reports = parse_ogimet_csv(body)
        if not reports:
            raise RuntimeError(
                f"No SYNOP bulletins decoded for WMO {self.wmo_index} "
                f"in the last {self.lookback_h}h"
            )

        readings: list[WeatherReading] = []
        for rp in reports:
            if not rp.is_usable:
                continue
            readings.append(
                WeatherReading(
                    observed_at=rp.observed_at,
                    provenance=DataProvenance.LIVE_OBSERVED,
                    source=self.label,
                    source_provider=self.key,
                    source_station_code=self.wmo_index,
                    temperature_c=rp.temperature_c,
                    wind_speed_ms=rp.wind_speed_ms,
                    wind_direction_deg=rp.wind_direction_deg,
                    humidity_pct=rp.humidity_pct,
                    pressure_hpa=rp.pressure_msl_hpa or rp.pressure_station_hpa,
                    cloud_cover_pct=rp.cloud_cover_pct,
                    snowfall_mm=rp.precipitation_mm,
                    weather_text=rp.present_weather,
                    # Antarctic blizzard criterion: blowing snow + strong wind
                    is_blizzard=bool(
                        rp.is_blowing_snow
                        and (rp.wind_speed_ms or 0) >= 17.0
                    ),
                    solar_radiation_wm2=None,  # SYNOP carries no radiation group
                    raw_payload={
                        "raw_synop": rp.raw,
                        "wmo_index": rp.station_index,
                        "observed_at": rp.observed_at.isoformat(),
                        "decoded_groups": rp.decoded_groups,
                        "undecoded_groups": rp.undecoded_groups,
                        "dewpoint_c": rp.dewpoint_c,
                        "visibility_m": rp.visibility_m,
                        "present_weather_code": rp.present_weather_code,
                        "temp_max_c": rp.temp_max_c,
                        "temp_min_c": rp.temp_min_c,
                        "pressure_tendency_hpa": rp.pressure_tendency_hpa,
                        "provider": self.key,
                    },
                )
            )
        if not readings:
            raise RuntimeError("SYNOP bulletins decoded but none were usable")
        return readings, resp.status_code, str(resp.url)


# ---------------------------------------------------------------------------
# 2. Open-Meteo  -  hourly resolution, solar radiation, forecast horizon
# ---------------------------------------------------------------------------


class OpenMeteoProvider(WeatherProvider):
    """Real analysis + NWP forecast at the station's true coordinates.

    Supplies the two things SYNOP cannot: hourly resolution and shortwave
    radiation. Past/current hours are LIVE_OBSERVED, future hours are
    REAL_FORECAST - the two are never conflated.
    """

    key = "open_meteo"
    label = "Open-Meteo (ECMWF/GFS analysis + forecast)"
    priority = 20
    supports_solar_radiation = True
    freshness_window_s = 2 * 3600

    HOURLY = [
        "temperature_2m", "apparent_temperature", "relative_humidity_2m",
        "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m",
        "shortwave_radiation", "direct_radiation", "diffuse_radiation",
        "surface_pressure", "cloud_cover", "snowfall", "weather_code",
    ]
    CURRENT = [
        "temperature_2m", "apparent_temperature", "relative_humidity_2m",
        "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m",
        "surface_pressure", "cloud_cover", "snowfall", "weather_code", "is_day",
    ]

    WMO_CODES = {
        0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
        45: "Fog", 48: "Depositing rime fog", 51: "Light drizzle",
        61: "Slight rain", 63: "Moderate rain", 71: "Slight snowfall",
        73: "Moderate snowfall", 75: "Heavy snowfall", 77: "Snow grains",
        85: "Slight snow showers", 86: "Heavy snow showers",
    }

    def __init__(self, latitude: float | None = None, longitude: float | None = None,
                 past_days: int = 2, forecast_days: int = 7, **kw) -> None:
        super().__init__(**kw)
        self.latitude = latitude if latitude is not None else SITE.latitude
        self.longitude = longitude if longitude is not None else SITE.longitude
        self.past_days = past_days
        self.forecast_days = forecast_days

    async def _fetch(self, client):
        params = {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "hourly": ",".join(self.HOURLY),
            "current": ",".join(self.CURRENT),
            "past_days": self.past_days,
            "forecast_days": self.forecast_days,
            "wind_speed_unit": "ms",
            "timezone": "UTC",
        }
        resp = await client.get(settings.weather_forecast_url, params=params)
        resp.raise_for_status()
        data = resp.json()

        hourly = data.get("hourly") or {}
        times = hourly.get("time") or []
        if not times:
            raise RuntimeError("Open-Meteo returned no hourly series")

        now = datetime.now(timezone.utc)
        current_hour = now.replace(minute=0, second=0, microsecond=0)

        def col(name):
            return hourly.get(name) or [None] * len(times)

        cols = {n: col(n) for n in self.HOURLY}
        readings: list[WeatherReading] = []

        for i, tstr in enumerate(times):
            try:
                ts = datetime.fromisoformat(tstr).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            temp = cols["temperature_2m"][i]
            wind = cols["wind_speed_10m"][i]
            if temp is None or wind is None:
                continue

            provenance = (
                DataProvenance.REAL_FORECAST if ts > current_hour
                else DataProvenance.LIVE_OBSERVED
            )
            wcode = cols["weather_code"][i]
            snow_cm = cols["snowfall"][i]
            readings.append(
                WeatherReading(
                    observed_at=ts,
                    provenance=provenance,
                    source=self.label,
                    source_provider=self.key,
                    source_station_code=f"{self.latitude:.4f},{self.longitude:.4f}",
                    temperature_c=temp,
                    apparent_temperature_c=cols["apparent_temperature"][i],
                    wind_speed_ms=wind,
                    wind_gust_ms=cols["wind_gusts_10m"][i],
                    wind_direction_deg=cols["wind_direction_10m"][i],
                    solar_radiation_wm2=cols["shortwave_radiation"][i],
                    direct_radiation_wm2=cols["direct_radiation"][i],
                    diffuse_radiation_wm2=cols["diffuse_radiation"][i],
                    humidity_pct=cols["relative_humidity_2m"][i],
                    pressure_hpa=cols["surface_pressure"][i],
                    cloud_cover_pct=cols["cloud_cover"][i],
                    snowfall_mm=(snow_cm * 10.0) if snow_cm is not None else None,
                    weather_text=self.WMO_CODES.get(wcode) if wcode is not None else None,
                    is_blizzard=bool(wind >= 17.0 and (snow_cm or 0) > 0),
                    raw_payload={
                        "provider": self.key,
                        "time": tstr,
                        "latitude": data.get("latitude"),
                        "longitude": data.get("longitude"),
                        "elevation": data.get("elevation"),
                        "units": data.get("hourly_units"),
                        "values": {n: cols[n][i] for n in self.HOURLY},
                    },
                )
            )

        # The `current` block is a genuine sub-hourly nowcast; keep it as the
        # freshest LIVE_OBSERVED point.
        cur = data.get("current") or {}
        if cur.get("temperature_2m") is not None and cur.get("wind_speed_10m") is not None:
            try:
                cts = datetime.fromisoformat(cur["time"]).replace(tzinfo=timezone.utc)
            except (ValueError, KeyError):
                cts = now
            snow_cm = cur.get("snowfall")
            # Radiation is not in the `current` block; borrow the matching hour.
            rad = None
            chour = cts.replace(minute=0, second=0, microsecond=0)
            for r in readings:
                if r.observed_at == chour:
                    rad = r.solar_radiation_wm2
                    break
            readings.append(
                WeatherReading(
                    observed_at=cts.replace(second=0, microsecond=0),
                    provenance=DataProvenance.LIVE_OBSERVED,
                    source=self.label + " [current]",
                    source_provider=self.key,
                    source_station_code=f"{self.latitude:.4f},{self.longitude:.4f}",
                    temperature_c=cur.get("temperature_2m"),
                    apparent_temperature_c=cur.get("apparent_temperature"),
                    wind_speed_ms=cur.get("wind_speed_10m"),
                    wind_gust_ms=cur.get("wind_gusts_10m"),
                    wind_direction_deg=cur.get("wind_direction_10m"),
                    solar_radiation_wm2=rad,
                    humidity_pct=cur.get("relative_humidity_2m"),
                    pressure_hpa=cur.get("surface_pressure"),
                    cloud_cover_pct=cur.get("cloud_cover"),
                    snowfall_mm=(snow_cm * 10.0) if snow_cm is not None else None,
                    weather_text=self.WMO_CODES.get(cur.get("weather_code")),
                    is_blizzard=bool(
                        (cur.get("wind_speed_10m") or 0) >= 17.0 and (snow_cm or 0) > 0
                    ),
                    raw_payload={"provider": self.key, "current": cur,
                                 "units": data.get("current_units")},
                )
            )
        return readings, resp.status_code, str(resp.url)


class OpenMeteoArchiveProvider(OpenMeteoProvider):
    """ERA5 reanalysis, used once to backfill real history for ML training."""

    key = "open_meteo_archive"
    label = "Open-Meteo ERA5 reanalysis archive"
    priority = 90

    ARCHIVE_HOURLY = [
        "temperature_2m", "apparent_temperature", "relative_humidity_2m",
        "wind_speed_10m", "wind_direction_10m",
        "shortwave_radiation", "direct_radiation", "diffuse_radiation",
        "surface_pressure", "cloud_cover", "snowfall",
    ]

    def __init__(self, start_date: str, end_date: str, **kw) -> None:
        super().__init__(**kw)
        self.start_date = start_date
        self.end_date = end_date

    async def _fetch(self, client):
        params = {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "hourly": ",".join(self.ARCHIVE_HOURLY),
            "wind_speed_unit": "ms",
            "timezone": "UTC",
        }
        resp = await client.get(settings.weather_archive_url, params=params)
        resp.raise_for_status()
        data = resp.json()
        hourly = data.get("hourly") or {}
        times = hourly.get("time") or []
        if not times:
            raise RuntimeError("Open-Meteo archive returned no hourly series")

        cols = {n: (hourly.get(n) or [None] * len(times)) for n in self.ARCHIVE_HOURLY}
        readings = []
        for i, tstr in enumerate(times):
            temp, wind = cols["temperature_2m"][i], cols["wind_speed_10m"][i]
            if temp is None or wind is None:
                continue
            ts = datetime.fromisoformat(tstr).replace(tzinfo=timezone.utc)
            snow_cm = cols["snowfall"][i]
            readings.append(
                WeatherReading(
                    observed_at=ts,
                    provenance=DataProvenance.REAL_OBSERVED,
                    source=self.label,
                    source_provider=self.key,
                    source_station_code=f"{self.latitude:.4f},{self.longitude:.4f}",
                    temperature_c=temp,
                    apparent_temperature_c=cols["apparent_temperature"][i],
                    wind_speed_ms=wind,
                    wind_direction_deg=cols["wind_direction_10m"][i],
                    solar_radiation_wm2=cols["shortwave_radiation"][i],
                    direct_radiation_wm2=cols["direct_radiation"][i],
                    diffuse_radiation_wm2=cols["diffuse_radiation"][i],
                    humidity_pct=cols["relative_humidity_2m"][i],
                    pressure_hpa=cols["surface_pressure"][i],
                    cloud_cover_pct=cols["cloud_cover"][i],
                    snowfall_mm=(snow_cm * 10.0) if snow_cm is not None else None,
                    is_blizzard=bool(wind >= 17.0 and (snow_cm or 0) > 0),
                    raw_payload={"provider": self.key, "time": tstr},
                )
            )
        return readings, resp.status_code, str(resp.url)


# ---------------------------------------------------------------------------
# 3. NOAA Aviation Weather METAR
# ---------------------------------------------------------------------------


class NoaaMetarProvider(WeatherProvider):
    """Real METAR observations from Antarctic aerodromes, hosted by NOAA."""

    key = "noaa_metar"
    label = "NOAA Aviation Weather Center - Antarctic METAR"
    priority = 30
    supports_solar_radiation = False
    freshness_window_s = 4 * 3600

    URL = "https://aviationweather.gov/api/data/metar"

    def __init__(self, icao_ids: list[str] | None = None, hours: int = 12, **kw) -> None:
        super().__init__(**kw)
        self.icao_ids = icao_ids or ["NZSP"]
        self.hours = hours

    async def _fetch(self, client):
        params = {"ids": ",".join(self.icao_ids), "format": "json", "hours": self.hours}
        resp = await client.get(self.URL, params=params)
        resp.raise_for_status()
        try:
            rows = resp.json()
        except Exception as exc:
            raise RuntimeError(f"METAR response was not JSON: {exc}") from exc
        if not isinstance(rows, list) or not rows:
            raise RuntimeError(f"No METAR rows for {self.icao_ids}")

        readings = []
        for row in rows:
            temp = row.get("temp")
            wspd_kt = row.get("wspd")
            if temp is None or wspd_kt is None:
                continue
            try:
                ts = datetime.fromisoformat(
                    str(row["reportTime"]).replace("Z", "+00:00")
                )
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except (ValueError, KeyError):
                continue

            dewp = row.get("dewp")
            rh = (
                round(relative_humidity(float(temp), float(dewp)), 1)
                if dewp is not None else None
            )
            wx = (row.get("wxString") or "").upper()
            wind_ms = round(float(wspd_kt) * KNOTS_TO_MS, 2)
            cover = row.get("cover")
            cover_pct = {"CLR": 0.0, "SKC": 0.0, "FEW": 18.0, "SCT": 44.0,
                         "BKN": 75.0, "OVC": 100.0}.get(cover)

            readings.append(
                WeatherReading(
                    observed_at=ts,
                    provenance=DataProvenance.LIVE_OBSERVED,
                    source=f"{self.label} [{row.get('icaoId')}]",
                    source_provider=self.key,
                    source_station_code=row.get("icaoId"),
                    temperature_c=float(temp),
                    wind_speed_ms=wind_ms,
                    wind_direction_deg=(
                        float(row["wdir"])
                        if isinstance(row.get("wdir"), (int, float)) else None
                    ),
                    humidity_pct=rh,
                    pressure_hpa=row.get("altim"),
                    cloud_cover_pct=cover_pct,
                    solar_radiation_wm2=None,  # METAR carries no radiation
                    weather_text=row.get("wxString"),
                    is_blizzard=bool(
                        ("BLSN" in wx or "DRSN" in wx) and wind_ms >= 17.0
                    ),
                    raw_payload={"provider": self.key, "metar": row},
                )
            )
        if not readings:
            raise RuntimeError("METAR rows present but none usable")
        return readings, resp.status_code, str(resp.url)


# ---------------------------------------------------------------------------
# 4. MET Norway  -  independent forecast failover
# ---------------------------------------------------------------------------


def _station_pressure(sea_level_hpa: float | None, temp_c: float | None,
                      elevation_m: float) -> float | None:
    """Reduce sea-level pressure to station level (barometric formula).

    POLARIS stores station pressure, which drives air density and hence wind
    turbine output; MET Norway reports only the sea-level value.
    """
    if sea_level_hpa is None:
        return None
    t = 0.0 if temp_c is None else temp_c
    lapse = 0.0065 * elevation_m
    return round(sea_level_hpa * (1.0 - lapse / (t + lapse + 273.15)) ** 5.257, 1)


class MetNorwayProvider(WeatherProvider):
    """Real NWP forecast from the Norwegian Meteorological Institute.

    Failover for the forecast horizon. Open-Meteo is the primary forecast,
    but on free PaaS hosting it shares a per-IP quota with every other
    tenant, and when that quota is exhausted POLARIS would have no
    forward-looking weather at all. MET Norway is a separate service with
    separate limits.

    Every row is REAL_FORECAST - nothing from here is labelled observed.
    Its limitations are published in PROVIDER_DESCRIPTIONS:

    - hourly for roughly the first 60 h, then 6-hourly. The 6-hourly tail is
      linearly interpolated to hourly, as Open-Meteo itself does for coarse
      model steps, and every interpolated row says so in its raw payload;
    - no solar radiation - estimated downstream from forecast cloud cover;
    - sea-level pressure only - reduced to station level here.
    """

    key = "met_norway"
    label = "MET Norway Locationforecast (NWP forecast)"
    priority = 25
    supports_solar_radiation = False
    freshness_window_s = 6 * 3600

    URL = "https://api.met.no/weatherapi/locationforecast/2.0/complete"
    #: api.met.no refuses anonymous clients: its terms require a User-Agent
    #: that identifies the application and how to reach its maintainer.
    CLIENT_ID = "POLARIS/1.0 https://github.com/Bhuvanesh0821/Polaris"

    SCALARS = {
        "temperature_c": "air_temperature",
        "apparent_temperature_c": "apparent_air_temperature",
        "wind_speed_ms": "wind_speed",
        "humidity_pct": "relative_humidity",
        "cloud_cover_pct": "cloud_area_fraction",
        "sea_level_hpa": "air_pressure_at_sea_level",
    }

    def __init__(self, latitude: float | None = None, longitude: float | None = None,
                 horizon_h: int | None = None, **kw) -> None:
        super().__init__(**kw)
        self.latitude = latitude if latitude is not None else SITE.latitude
        self.longitude = longitude if longitude is not None else SITE.longitude
        self.horizon_h = horizon_h or settings.forecast_horizon_h

    async def _fetch(self, client):
        params = {"lat": round(self.latitude, 4), "lon": round(self.longitude, 4),
                  "altitude": int(SITE.elevation_m)}
        resp = await client.get(self.URL, params=params,
                                headers={"User-Agent": self.CLIENT_ID})
        resp.raise_for_status()
        data = resp.json()
        series = (data.get("properties") or {}).get("timeseries") or []
        if not series:
            raise RuntimeError("MET Norway returned no timeseries")
        model_run = ((data.get("properties") or {}).get("meta") or {}).get("updated_at")

        steps: list[tuple[datetime, dict]] = []
        for entry in series:
            try:
                ts = datetime.fromisoformat(entry["time"].replace("Z", "+00:00"))
            except (KeyError, ValueError):
                continue
            det = ((entry.get("data") or {}).get("instant") or {}).get("details") or {}
            if det.get("air_temperature") is None or det.get("wind_speed") is None:
                continue
            steps.append((ts, det))
        if not steps:
            raise RuntimeError("MET Norway timeseries had no usable steps")

        current_hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        last_hour = current_hour + timedelta(hours=self.horizon_h)
        readings: list[WeatherReading] = []

        def emit(ts: datetime, vals: dict, interpolated_from: tuple | None) -> None:
            # Forecast hours only: the current hour is not an observation.
            if not (current_hour < ts <= last_hour):
                return
            readings.append(WeatherReading(
                observed_at=ts,
                provenance=DataProvenance.REAL_FORECAST,
                source=self.label,
                source_provider=self.key,
                source_station_code=f"{self.latitude:.4f},{self.longitude:.4f}",
                temperature_c=vals["temperature_c"],
                apparent_temperature_c=vals["apparent_temperature_c"],
                wind_speed_ms=vals["wind_speed_ms"],
                wind_direction_deg=vals["wind_direction_deg"],
                humidity_pct=vals["humidity_pct"],
                cloud_cover_pct=vals["cloud_cover_pct"],
                pressure_hpa=_station_pressure(vals["sea_level_hpa"],
                                               vals["temperature_c"], SITE.elevation_m),
                solar_radiation_wm2=None,  # not provided by this service
                raw_payload={
                    "provider": self.key,
                    "model_run": model_run,
                    "interpolated": interpolated_from is not None,
                    **({"between": [t.isoformat() for t in interpolated_from]}
                       if interpolated_from else {}),
                    "values": vals,
                },
            ))

        def pick(det: dict) -> dict:
            v = {k: det.get(src) for k, src in self.SCALARS.items()}
            v["wind_direction_deg"] = det.get("wind_from_direction")
            return v

        for (t0, d0), nxt in zip(steps, steps[1:] + [None]):
            v0 = pick(d0)
            emit(t0, v0, None)
            if nxt is None:
                break
            t1, d1 = nxt
            gap_h = int((t1 - t0).total_seconds() // 3600)
            if gap_h <= 1:
                continue
            v1 = pick(d1)
            for k in range(1, gap_h):
                f = k / gap_h
                vals = {
                    name: (None if v0[name] is None or v1[name] is None
                           else round(v0[name] + f * (v1[name] - v0[name]), 2))
                    for name in self.SCALARS
                }
                vals["wind_direction_deg"] = _interp_direction(
                    v0["wind_direction_deg"], v1["wind_direction_deg"], f)
                emit(t0 + timedelta(hours=k), vals, (t0, t1))

        if not readings:
            raise RuntimeError("MET Norway returned no future hours")
        return readings, resp.status_code, str(resp.url)


def _interp_direction(d0: float | None, d1: float | None, f: float) -> float | None:
    """Interpolate a compass bearing along the shorter arc (350 -> 10 via 0)."""
    if d0 is None or d1 is None:
        return None
    delta = ((d1 - d0 + 180.0) % 360.0) - 180.0
    return round((d0 + f * delta) % 360.0, 1)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def default_providers() -> list[WeatherProvider]:
    """The live chain used by the scheduled refresh, in priority order."""
    return sorted(
        [OgimetSynopProvider(), OpenMeteoProvider(), MetNorwayProvider(),
         NoaaMetarProvider()],
        key=lambda p: p.priority,
    )


PROVIDER_DESCRIPTIONS = {
    OgimetSynopProvider.key: {
        "label": OgimetSynopProvider.label,
        "kind": "REAL station observation (WMO GTS)",
        "station": "Maitri, Indian Antarctic Programme (WMO 89514)",
        "cadence": "6-hourly synoptic (00/06/12/18 UTC)",
        "variables": ["temperature", "wind speed", "wind direction", "pressure",
                      "cloud cover", "visibility", "present weather"],
        "missing": ["solar radiation", "humidity (Maitri omits the dewpoint group)"],
        "authoritative": True,
    },
    OpenMeteoProvider.key: {
        "label": OpenMeteoProvider.label,
        "kind": "REAL analysis + NWP forecast at station coordinates",
        "station": f"{SITE.latitude:.4f}, {SITE.longitude:.4f} (Maitri)",
        "cadence": "hourly, updated continuously",
        "variables": ["temperature", "wind", "solar radiation", "humidity",
                      "pressure", "cloud cover", "snowfall"],
        "missing": [],
        "authoritative": False,
    },
    MetNorwayProvider.key: {
        "label": MetNorwayProvider.label,
        "kind": "REAL NWP forecast (forecast failover)",
        "station": f"{SITE.latitude:.4f}, {SITE.longitude:.4f} (Maitri)",
        "cadence": "hourly to ~+60 h, 6-hourly beyond (linearly interpolated "
                   "to hourly; interpolated rows are flagged)",
        "variables": ["temperature", "wind speed", "wind direction", "humidity",
                      "pressure (reduced from sea level)", "cloud cover"],
        "missing": ["solar radiation (estimated from forecast cloud cover)",
                    "snowfall"],
        "authoritative": False,
    },
    NoaaMetarProvider.key: {
        "label": NoaaMetarProvider.label,
        "kind": "REAL aerodrome observation",
        "station": "Antarctic METAR stations (NZSP Amundsen-Scott)",
        "cadence": "hourly to 6-hourly",
        "variables": ["temperature", "wind", "pressure", "cloud", "present weather"],
        "missing": ["solar radiation"],
        "authoritative": False,
    },
}
