"""Approximate country suggestions without transmitting location externally."""
from importlib.resources import files


def country_for_timezone(timezone):
    timezone = {'Asia/Calcutta': 'Asia/Kolkata', 'US/Eastern': 'America/New_York',
                'US/Pacific': 'America/Los_Angeles'}.get(timezone, timezone)
    try:
        for line in files('tzdata.zoneinfo').joinpath('zone.tab').read_text().splitlines():
            if line and not line.startswith('#'):
                parts = line.split('\t')
                if len(parts) >= 3 and parts[2] == timezone:
                    return parts[0]
    except (ImportError, FileNotFoundError):
        pass
    return None
