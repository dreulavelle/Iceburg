from datetime import datetime
import os
import re
import secrets
import string
from pathlib import Path
from typing import Optional

from loguru import logger

root_dir = Path(__file__).resolve().parents[3]

data_dir_path = root_dir / "data"
alembic_dir = data_dir_path / "alembic"

def get_version() -> str:
    with open(root_dir / "pyproject.toml") as file:
        pyproject_toml = file.read()

    match = re.search(r'version = "(.+)"', pyproject_toml)
    if match:
        version = match.group(1)
    else:
        raise ValueError("Could not find version in pyproject.toml")
    return version

def generate_api_key():
    """Generate a secure API key of the specified length."""
    API_KEY = os.getenv("API_KEY", "")
    if len(API_KEY) != 32:
        logger.warning("env.API_KEY is not 32 characters long, generating a new one...")
        characters = string.ascii_letters + string.digits

        # Generate the API key
        api_key = "".join(secrets.choice(characters) for _ in range(32))
        logger.warning(f"New api key: {api_key}")
    else:
        api_key = API_KEY

    return api_key

def get_earliest_date(trakt_date: datetime, tvmaze_date: datetime) -> Optional[datetime]:
    """Get the earliest date from two datetime objects."""
    if trakt_date.tzinfo is None or tvmaze_date.tzinfo is None:
        logger.debug(f"Both datetime objects must be timezone-aware. trakt date: {trakt_date}, tvmaze date: {tvmaze_date}")
        return None
    return min(trakt_date, tvmaze_date)
