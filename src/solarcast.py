"""
src/solarcast.py - SolarCast Day-Ahead Weather & State-of-Charge Forecaster
SIMULATED: Pulls Open-Meteo weather data or cached fallback to pre-position BESS SoC at 06:00.
Strategies:
- CLEAR (<20% cloud): Target 30% SoC (soak up daytime solar surplus)
- HEDGE (20-60% cloud): Target 60% SoC (balanced protection)
- PRECHARGE (>60% cloud): Target 90% SoC (overnight grid charge at off-peak ₹3.50/kWh)
- OFFLINE / FAILURE: Target 60% SoC (safe conservative default)
"""

import json
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
import requests
from src.config import GridShieldConfig, CFG


def get_solar_forecast(
    lat: float = None,
    lon: float = None,
    use_live_api: bool = False,
    manual_preset: Optional[str] = None,
    force_failure: bool = False,
    cfg: GridShieldConfig = CFG
) -> Dict[str, Any]:
    """
    Retrieves weather forecast and determines BESS SoC pre-positioning strategy.
    """
    if force_failure:
        return {
            "avg_cloud_cover_pct": 50.0,
            "peak_irradiance_wm2": 700.0,
            "target_soc_pct": cfg.SOLARCAST_API_FAIL_SOC_PCT,
            "strategy": "HEDGE",
            "is_fallback": True,
            "source": "SIMULATED_FAILURE_FALLBACK"
        }

    latitude = lat if lat is not None else cfg.SOLARCAST_LAT
    longitude = lon if lon is not None else cfg.SOLARCAST_LON

    # 1. Manual Preset Override (for demo dashboard and unit testing)
    if manual_preset is not None:
        preset_map = {
            "Clear": 12.0,
            "Partly cloudy": 45.0,
            "Overcast": 82.0
        }
        avg_cloud = preset_map.get(manual_preset, 45.0)
        strategy, target_soc = _evaluate_strategy(avg_cloud, cfg)
        return {
            "avg_cloud_cover_pct": float(avg_cloud),
            "peak_irradiance_wm2": 950.0 if avg_cloud < 50 else 350.0,
            "target_soc_pct": target_soc,
            "strategy": strategy,
            "is_fallback": False,
            "source": f"MANUAL_PRESET_{manual_preset.upper()}"
        }

    # 2. Live API Call to Open-Meteo (Free, no key required)
    if use_live_api:
        try:
            url = "https://api.open-meteo.com/v1/forecast"
            params = {
                "latitude": latitude,
                "longitude": longitude,
                "hourly": "shortwave_radiation,cloud_cover,temperature_2m",
                "forecast_days": 1,
                "timezone": "Asia/Kolkata"
            }
            resp = requests.get(url, params=params, timeout=4.0)
            if resp.status_code == 200:
                data = resp.json()
                hourly = data["hourly"]
                avg_cloud = float(np_mean := sum(hourly["cloud_cover"]) / len(hourly["cloud_cover"]))
                peak_rad = float(max(hourly["shortwave_radiation"]))
                strategy, target_soc = _evaluate_strategy(avg_cloud, cfg)
                return {
                    "avg_cloud_cover_pct": avg_cloud,
                    "peak_irradiance_wm2": peak_rad,
                    "target_soc_pct": target_soc,
                    "strategy": strategy,
                    "is_fallback": False,
                    "source": "OPEN_METEO_API"
                }
        except Exception:
            pass  # Fall through to cached offline asset

    # 3. Offline Cached Asset Fallback
    cache_path = Path("data/weather_cache_chennai.json")
    if not cache_path.exists():
        # Check parent or specs dir
        cache_path = Path(__file__).resolve().parent.parent / "data" / "weather_cache_chennai.json"

    if cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            hourly = data["hourly"]
            avg_cloud = float(sum(hourly["cloud_cover"]) / len(hourly["cloud_cover"]))
            peak_rad = float(max(hourly["shortwave_radiation"]))
            strategy, target_soc = _evaluate_strategy(avg_cloud, cfg)
            return {
                "avg_cloud_cover_pct": avg_cloud,
                "peak_irradiance_wm2": peak_rad,
                "target_soc_pct": target_soc,
                "strategy": strategy,
                "is_fallback": True,
                "source": "OFFLINE_CACHE_JSON"
            }
        except Exception:
            pass

    # 4. Total Failure Safe Default (Hedge mode at 60% SoC)
    return {
        "avg_cloud_cover_pct": 50.0,
        "peak_irradiance_wm2": 700.0,
        "target_soc_pct": cfg.SOLARCAST_API_FAIL_SOC_PCT,
        "strategy": "HEDGE",
        "is_fallback": True,
        "source": "FAILSAFE_DEFAULT"
    }


def _evaluate_strategy(avg_cloud_pct: float, cfg: GridShieldConfig) -> Tuple[str, float]:
    """Applies exact decision thresholds from PROJECT_REPORT.md §6."""
    if avg_cloud_pct < cfg.SOLARCAST_CLEAR_THRESHOLD_PCT:
        return "CLEAR", 30.0
    elif avg_cloud_pct <= cfg.SOLARCAST_HEDGE_THRESHOLD_PCT:
        return "HEDGE", 60.0
    else:
        return "PRECHARGE", cfg.SOLARCAST_PRECHARGE_SOC_PCT
