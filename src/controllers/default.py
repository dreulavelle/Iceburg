from typing import Literal

import requests
from controllers.models.shared import MessageResponse
from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from program.content.trakt import TraktContent
from program.db.db import db
from program.media.item import Episode, MediaItem, Movie, Season, Show
from program.media.state import States
from program.settings.manager import settings_manager
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from utils.event_manager import EventUpdate

router = APIRouter(
    responses={404: {"description": "Not found"}},
)


class RootResponse(MessageResponse):
    version: str


@router.get("/", operation_id="root")
async def root() -> RootResponse:
    return {
        "message": "Riven is running!",
        "version": settings_manager.settings.version,
    }


@router.get("/health", operation_id="health")
async def health(request: Request) -> MessageResponse:
    return {
        "message": request.app.program.initialized,
    }


class RDUser(BaseModel):
    id: int
    username: str
    email: str
    points: int = Field(description="User's RD points")
    locale: str
    avatar: str = Field(description="URL to the user's avatar")
    type: Literal["free", "premium"]
    premium: int = Field(description="Premium subscription left in seconds")


@router.get("/rd", operation_id="rd")
async def get_rd_user() -> RDUser:
    api_key = settings_manager.settings.downloaders.real_debrid.api_key
    headers = {"Authorization": f"Bearer {api_key}"}

    proxy = (
        settings_manager.settings.downloaders.real_debrid.proxy_url
        if settings_manager.settings.downloaders.real_debrid.proxy_enabled
        else None
    )

    response = requests.get(
        "https://api.real-debrid.com/rest/1.0/user",
        headers=headers,
        proxies=proxy if proxy else None,
        timeout=10,
    )

    if response.status_code != 200:
        return {"success": False, "message": response.json()}

    return response.json()


@router.get("/torbox", operation_id="torbox")
async def get_torbox_user():
    api_key = settings_manager.settings.downloaders.torbox.api_key
    headers = {"Authorization": f"Bearer {api_key}"}
    response = requests.get(
        "https://api.torbox.app/v1/api/user/me", headers=headers, timeout=10
    )
    return response.json()


@router.get("/services", operation_id="services")
async def get_services(request: Request) -> dict[str, bool]:
    data = {}
    if hasattr(request.app.program, "services"):
        for service in request.app.program.all_services.values():
            data[service.key] = service.initialized
            if not hasattr(service, "services"):
                continue
            for sub_service in service.services.values():
                data[sub_service.key] = sub_service.initialized
    return data


class TraktOAuthInitiateResponse(BaseModel):
    auth_url: str


@router.get("/trakt/oauth/initiate", operation_id="trakt_oauth_initiate")
async def initiate_trakt_oauth(request: Request) -> TraktOAuthInitiateResponse:
    trakt = request.app.program.services.get(TraktContent)
    if trakt is None:
        raise HTTPException(status_code=404, detail="Trakt service not found")
    auth_url = trakt.perform_oauth_flow()
    return {"auth_url": auth_url}


@router.get("/trakt/oauth/callback", operation_id="trakt_oauth_callback")
async def trakt_oauth_callback(code: str, request: Request) -> MessageResponse:
    trakt = request.app.program.services.get(TraktContent)
    if trakt is None:
        raise HTTPException(status_code=404, detail="Trakt service not found")
    success = trakt.handle_oauth_callback(code)
    if success:
        return {"message": "OAuth token obtained successfully"}
    else:
        raise HTTPException(status_code=400, detail="Failed to obtain OAuth token")


class StatsResponse(BaseModel):
    total_items: int
    total_movies: int
    total_shows: int
    total_seasons: int
    total_episodes: int
    total_symlinks: int
    incomplete_items: int
    incomplete_retries: dict[str, int] = Field(
        description="Media item log string: number of retries"
    )
    states: dict[States, int]


@router.get("/stats", operation_id="stats")
async def get_stats(_: Request) -> StatsResponse:
    payload = {}
    with db.Session() as session:
        movies_symlinks = session.execute(
            select(func.count(Movie._id)).where(Movie.symlinked == True)
        ).scalar_one()
        episodes_symlinks = session.execute(
            select(func.count(Episode._id)).where(Episode.symlinked == True)
        ).scalar_one()
        total_symlinks = movies_symlinks + episodes_symlinks

        total_movies = session.execute(select(func.count(Movie._id))).scalar_one()
        total_shows = session.execute(select(func.count(Show._id))).scalar_one()
        total_seasons = session.execute(select(func.count(Season._id))).scalar_one()
        total_episodes = session.execute(select(func.count(Episode._id))).scalar_one()
        total_items = session.execute(select(func.count(MediaItem._id))).scalar_one()

        # Select only the IDs of incomplete items
        _incomplete_items = (
            session.execute(
                select(MediaItem._id).where(MediaItem.last_state != States.Completed)
            )
            .scalars()
            .all()
        )

        incomplete_retries = {}
        if _incomplete_items:
            media_items = (
                session.query(MediaItem)
                .filter(MediaItem._id.in_(_incomplete_items))
                .all()
            )
            for media_item in media_items:
                incomplete_retries[media_item.log_string] = media_item.scraped_times

        states = {}
        for state in States:
            states[state] = session.execute(
                select(func.count(MediaItem._id)).where(MediaItem.last_state == state)
            ).scalar_one()

        payload["total_items"] = total_items
        payload["total_movies"] = total_movies
        payload["total_shows"] = total_shows
        payload["total_seasons"] = total_seasons
        payload["total_episodes"] = total_episodes
        payload["total_symlinks"] = total_symlinks
        payload["incomplete_items"] = len(_incomplete_items)
        payload["incomplete_retries"] = incomplete_retries
        payload["states"] = states
        return payload


class LogsResponse(BaseModel):
    logs: str


@router.get("/logs", operation_id="logs")
async def get_logs() -> str:
    log_file_path = None
    for handler in logger._core.handlers.values():
        if ".log" in handler._name:
            log_file_path = handler._sink._path
            break

    if not log_file_path:
        return {"success": False, "message": "Log file handler not found"}

    try:
        with open(log_file_path, "r") as log_file:
            log_contents = log_file.read()
        return {"logs": log_contents}
    except Exception as e:
        logger.error(f"Failed to read log file: {e}")
        raise HTTPException(status_code=500, detail="Failed to read log file")


@router.get("/events", operation_id="events")
async def get_events(
    request: Request,
) -> dict[str, list[EventUpdate]]:
    return request.app.program.em.get_event_updates()


@router.get("/mount", operation_id="mount")
async def get_rclone_files() -> dict[str, str]:
    """Get all files in the rclone mount."""
    import os

    rclone_dir = settings_manager.settings.symlink.rclone_path
    file_map = {}

    def scan_dir(path):
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.is_file():
                    file_map[entry.name] = entry.path
                elif entry.is_dir():
                    scan_dir(entry.path)

    scan_dir(rclone_dir)  # dict of `filename: filepath``
    return file_map
