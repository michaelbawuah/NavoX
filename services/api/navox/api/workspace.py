"""User-chosen display context; weather is optional and never creates attention."""

from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from navox.api.auth import CurrentAccountDependency, DatabaseSession
from navox.db.models import WorkspaceDisplayPreference

router = APIRouter(prefix="/workspace", tags=["workspace"])


class DisplayPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    clock_format: Literal["12h", "24h"] = "12h"
    temperature_unit: Literal["celsius", "fahrenheit"] = "celsius"
    weather_visible: bool = False
    weather_city: str | None = Field(default=None, max_length=128)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as error:
            raise ValueError("Choose a valid IANA timezone") from error
        return value


async def display_preferences(
    database: DatabaseSession, account: CurrentAccountDependency
) -> DisplayPreferences:
    preference = await database.get(
        WorkspaceDisplayPreference, (account.workspace.id, account.user.id)
    )
    if preference is None:
        return DisplayPreferences(timezone=account.user.timezone)
    return DisplayPreferences.model_validate(
        {
            "timezone": account.user.timezone,
            "clock_format": preference.clock_format,
            "temperature_unit": preference.temperature_unit,
            "weather_visible": preference.weather_visible,
            "weather_city": preference.weather_city,
        }
    )


@router.get("/preferences")
async def get_preferences(
    database: DatabaseSession, current_account: CurrentAccountDependency
) -> DisplayPreferences:
    return await display_preferences(database, current_account)


@router.post("/preferences")
async def save_preferences(
    payload: DisplayPreferences,
    database: DatabaseSession,
    current_account: CurrentAccountDependency,
) -> DisplayPreferences:
    if payload.weather_visible and not payload.weather_city:
        raise HTTPException(422, "Choose a city to show weather")
    preference = await database.get(
        WorkspaceDisplayPreference, (current_account.workspace.id, current_account.user.id)
    )
    if preference is None:
        preference = WorkspaceDisplayPreference(
            workspace_id=current_account.workspace.id, user_id=current_account.user.id
        )
        database.add(preference)
    current_account.user.timezone = payload.timezone
    preference.clock_format = payload.clock_format
    preference.temperature_unit = payload.temperature_unit
    preference.weather_visible = payload.weather_visible
    # Turning weather off also forgets the city; no precise location is collected.
    preference.weather_city = payload.weather_city if payload.weather_visible else None
    await database.commit()
    return await display_preferences(database, current_account)


class WeatherResponse(BaseModel):
    status: Literal["ready", "disabled", "unavailable"]
    temperature: float | None = None
    unit: Literal["celsius", "fahrenheit"]
    description: str | None = None
    city: str | None = None
    observed_at: str | None = None


# Short-lived public city forecasts, not user identifiers or precise device location.
_weather_cache: dict[tuple[str, str], tuple[datetime, WeatherResponse]] = {}


async def city_weather(
    city: str, unit: Literal["celsius", "fahrenheit"], client: httpx.AsyncClient
) -> WeatherResponse:
    geocoded = await client.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": city, "count": 1, "language": "en", "format": "json"},
    )
    geocoded.raise_for_status()
    places = geocoded.json().get("results", [])
    if not places:
        return WeatherResponse(status="unavailable", unit=unit, city=city)
    place = places[0]
    forecast = await client.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": "temperature_2m,weather_code",
            "temperature_unit": unit,
            "timezone": "UTC",
            "forecast_days": 1,
        },
    )
    forecast.raise_for_status()
    current = forecast.json()["current"]
    temperature = float(current["temperature_2m"])
    if not isfinite(temperature):
        raise ValueError("Invalid weather reading")
    code = int(current["weather_code"])
    description = (
        "Clear"
        if code == 0
        else "Cloudy"
        if code <= 3
        else "Fog"
        if code <= 48
        else "Drizzle"
        if code <= 57
        else "Rain"
        if code <= 67
        else "Snow"
        if code <= 77
        else "Showers"
        if code <= 86
        else "Thunderstorms"
    )
    return WeatherResponse(
        status="ready",
        temperature=temperature,
        unit=unit,
        description=description,
        city=str(place.get("name", city)),
        observed_at=str(current["time"]) + "Z",
    )


@router.get("/weather")
async def get_weather(
    database: DatabaseSession, current_account: CurrentAccountDependency
) -> WeatherResponse:
    prefs = await display_preferences(database, current_account)
    if not prefs.weather_visible or not prefs.weather_city:
        return WeatherResponse(status="disabled", unit=prefs.temperature_unit)
    key = (prefs.weather_city.casefold(), prefs.temperature_unit)
    now = datetime.now(UTC)
    cached = _weather_cache.get(key)
    if cached and cached[0] > now:
        return cached[1]
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
            result = await city_weather(prefs.weather_city, prefs.temperature_unit, client)
    except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError):
        result = WeatherResponse(
            status="unavailable", unit=prefs.temperature_unit, city=prefs.weather_city
        )
    if len(_weather_cache) >= 256:
        _weather_cache.clear()
    _weather_cache[key] = (now + timedelta(minutes=15), result)
    return result
