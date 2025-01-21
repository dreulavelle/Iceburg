import asyncio
import re
from datetime import datetime, timedelta
from typing import Dict, List, Literal, Optional, TypeAlias, Union
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, RootModel
from RTN import ParsedData
from sqlalchemy import select

from program.db import db_functions
from program.db.db import db
from program.media.item import Episode, MediaItem
from program.media.stream import Stream as ItemStream
from program.services.downloaders import Downloader
from program.services.indexers.trakt import TraktIndexer
from program.services.scrapers import Scraping
from program.services.scrapers.shared import rtn
from program.types import Event
from program.services.downloaders.models import TorrentContainer, TorrentInfo, DebridFile


class Stream(BaseModel):
    infohash: str
    raw_title: str
    parsed_title: str
    parsed_data: ParsedData
    rank: int
    lev_ratio: float
    is_cached: bool

class ScrapeItemResponse(BaseModel):
    message: str
    streams: Dict[str, Stream]

class StartSessionResponse(BaseModel):
    message: str
    session_id: str
    torrent_id: str
    torrent_info: TorrentInfo
    containers: Optional[List[TorrentContainer]]
    expires_at: str

class SelectFilesResponse(BaseModel):
    message: str
    download_type: Literal["cached", "uncached"]

class UpdateAttributesResponse(BaseModel):
    message: str

class SessionResponse(BaseModel):
    message: str

ContainerMap: TypeAlias = Dict[str, DebridFile]

class Container(RootModel[ContainerMap]):
    """
    Root model for container mapping file IDs to file information.

    Example:
    {
        "4": {
            "filename": "show.s01e01.mkv",
            "filesize": 30791392598
        },
        "5": {
            "filename": "show.s01e02.mkv",
            "filesize": 25573181861
        }
    }
    """
    root: ContainerMap

SeasonEpisodeMap: TypeAlias = Dict[int, Dict[int, DebridFile]]

class ShowFileData(RootModel[SeasonEpisodeMap]):
    """
    Root model for show file data that maps seasons to episodes to file data.

    Example:
    {
        1: {  # Season 1
            1: {"filename": "path/to/s01e01.mkv"},  # Episode 1
            2: {"filename": "path/to/s01e02.mkv"}   # Episode 2
        },
        2: {  # Season 2
            1: {"filename": "path/to/s02e01.mkv"}   # Episode 1
        }
    }
    """

    root: SeasonEpisodeMap

class ScrapingSession:
    def __init__(self, id: str, item_id: str, magnet: str):
        self.id = id
        self.item_id = item_id
        self.magnet = magnet
        self.torrent_id: Optional[Union[int, str]] = None
        self.torrent_info: Optional[TorrentInfo] = None
        self.containers: Optional[TorrentContainer] = None
        self.selected_files: Optional[Dict[str, Dict[str, Union[str, int]]]] = None
        self.created_at: datetime = datetime.now()
        self.expires_at: datetime = datetime.now() + timedelta(minutes=5)

class ScrapingSessionManager:
    def __init__(self):
        self.sessions: Dict[str, ScrapingSession] = {}
        self.downloader: Optional[Downloader] = None

    def set_downloader(self, downloader: Downloader):
        self.downloader = downloader

    def create_session(self, item_id: str, magnet: str) -> ScrapingSession:
        session_id = str(uuid4())
        session = ScrapingSession(session_id, item_id, magnet)
        self.sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> Optional[ScrapingSession]:
        session = self.sessions.get(session_id)
        if not session:
            return None

        if datetime.now() > session.expires_at:
            self.abort_session(session_id)
            return None

        return session

    def update_session(self, session_id: str, **kwargs) -> Optional[ScrapingSession]:
        session = self.get_session(session_id)
        if not session:
            return None

        for key, value in kwargs.items():
            if hasattr(session, key):
                setattr(session, key, value)

        return session

    def abort_session(self, session_id: str):
        session = self.sessions.pop(session_id, None)
        if session and session.torrent_id and self.downloader:
            try:
                self.downloader.delete_torrent(session.torrent_id)
                logger.debug(f"Deleted torrent for aborted session {session_id}")
            except Exception as e:
                logger.error(f"Failed to delete torrent for session {session_id}: {e}")
        if session:
            logger.debug(f"Aborted session {session_id} for item {session.item_id}")

    def complete_session(self, session_id: str):
        session = self.get_session(session_id)
        if not session:
            return

        logger.debug(f"Completing session {session_id} for item {session.item_id}")
        self.sessions.pop(session_id)

    def cleanup_expired(self, background_tasks: BackgroundTasks):
        current_time = datetime.now()
        expired = [
            session_id for session_id, session in self.sessions.items()
            if current_time > session.expires_at
        ]
        for session_id in expired:
            background_tasks.add_task(self.abort_session, session_id)

session_manager = ScrapingSessionManager()

router = APIRouter(prefix="/scrape", tags=["scrape"])

def initialize_downloader(downloader: Downloader):
    """Initialize downloader if not already set"""
    if not session_manager.downloader:
        session_manager.set_downloader(downloader)

@router.get(
    "/scrape/{id}",
    summary="Get streams for an item",
    operation_id="scrape_item"
)
def scrape_item(request: Request, id: str) -> ScrapeItemResponse:

    if id.startswith("tt"):
        imdb_id = id
        item_id = None
    else:
        imdb_id = None
        item_id = id

    if services := request.app.program.services:
        indexer = services[TraktIndexer]
        scraper = services[Scraping]
        downloader = services[Downloader]
    else:
        raise HTTPException(status_code=412, detail="Scraping services not initialized")

    log_string = None
    with db.Session() as db_session:

        if imdb_id:
            prepared_item = MediaItem({"imdb_id": imdb_id})
            item = next(indexer.run(prepared_item))
        else:
            item: MediaItem = (
                db_session.execute(
                    select(MediaItem)
                    .where(MediaItem.id == item_id)
                )
                .unique()
                .scalar_one_or_none()
            )
        streams: Dict[str, Stream] = scraper.scrape(item)
        for stream in streams.values():
            container = downloader.get_instant_availability(stream.infohash, item.type)
            stream.is_cached = bool(container and container.cached)
        log_string = item.log_string

    return {
        "message": f"Manually scraped streams for item {log_string}",
        "streams": streams
    }

@router.post("/scrape/start_session")
async def start_manual_session(
    request: Request,
    background_tasks: BackgroundTasks,
    item_id: str,
    magnet: str
) -> StartSessionResponse:
    session_manager.cleanup_expired(background_tasks)

    def get_info_hash(magnet: str) -> str:
        pattern = r"[A-Fa-f0-9]{40}"
        match = re.search(pattern, magnet)
        return match.group(0) if match else None

    info_hash = get_info_hash(magnet)
    if not info_hash:
        raise HTTPException(status_code=400, detail="Invalid magnet URI")

    # Identify item based on IMDb or database ID
    if item_id.startswith("tt"):
        imdb_id = item_id
        item_id = None
    else:
        imdb_id = None
        item_id = item_id

    if services := request.app.program.services:
        indexer = services[TraktIndexer]
        downloader = services[Downloader]
    else:
        raise HTTPException(status_code=412, detail="Required services not initialized")

    initialize_downloader(downloader)

    if imdb_id:
        prepared_item = MediaItem({"imdb_id": imdb_id})
        item = next(indexer.run(prepared_item))
    else:
        item = db_functions.get_item_by_id(item_id)

    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    session = session_manager.create_session(item_id or imdb_id, info_hash)

    try:
        torrent_id: str = downloader.add_torrent(info_hash)
        torrent_info: TorrentInfo = downloader.get_torrent_info(torrent_id)
        container: Optional[TorrentContainer] = downloader.get_instant_availability(info_hash, item.type)
        session_manager.update_session(session.id, torrent_id=torrent_id, torrent_info=torrent_info, containers=container)
    except Exception as e:
        background_tasks.add_task(session_manager.abort_session, session.id)
        raise HTTPException(status_code=500, detail=str(e))

    data = {
        "message": "Started manual scraping session",
        "session_id": session.id,
        "torrent_id": torrent_id,
        "torrent_info": torrent_info,
        "containers": [container] if container else None,
        "expires_at": session.expires_at.isoformat()
    }

    return StartSessionResponse(**data)

@router.post(
    "/scrape/select_files/{session_id}",
    summary="Select files for torrent id, for this to be instant it requires files to be one of /manual/instant_availability response containers",
    operation_id="manual_select"
)
def manual_select_files(request: Request, session_id, files: Container) -> SelectFilesResponse:
    downloader: Downloader = request.app.program.services.get(Downloader)
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    if not session.torrent_id:
        session_manager.abort_session(session_id)
        raise HTTPException(status_code=500, detail="No torrent ID found")

    download_type = "uncached"
    if files.model_dump() in session.containers:
        download_type = "cached"

    try:
        downloader.select_files(session.torrent_id, files.model_dump())
        session.selected_files = files.model_dump()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "message": f"Selected files for {session.item_id}",
        "download_type": download_type
    }

@router.post(
    "/scrape/update_attributes/{session_id}",
    summary="Match container files to item",
    operation_id="manual_update_attributes"
)
async def manual_update_attributes(request: Request, session_id, data: Union[DebridFile, ShowFileData]) -> UpdateAttributesResponse:
    session = session_manager.get_session(session_id)
    log_string = None
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    if not session.item_id:
        session_manager.abort_session(session_id)
        raise HTTPException(status_code=500, detail="No item ID found")

    with db.Session() as db_session:
        if str(session.item_id).startswith("tt") and not db_functions.get_item_by_external_id(imdb_id=session.item_id) and not db_functions.get_item_by_id(session.item_id):
            prepared_item = MediaItem({"imdb_id": session.item_id})
            item = next(TraktIndexer().run(prepared_item))
            db_session.merge(item)
            db_session.commit()
        else:
          item = db_functions.get_item_by_id(session.item_id)

        if not item:
            raise HTTPException(status_code=404, detail="Item not found")

        item_ids_to_submit = []

        if item.type == "movie":
            request.app.program.em.cancel_job(item.id)
            item.reset()
            item.file = data.filename
            item.folder = data.filename
            item.alternative_folder = session.torrent_info.alternative_filename
            item.active_stream = {"infohash": session.magnet, "id": session.torrent_info.id}
            torrent = rtn.rank(session.magnet, session.magnet)
            item.streams.append(ItemStream(torrent))
            item_ids_to_submit.append(item.id)
        else:
            request.app.program.em.cancel_job(item.id)
            await asyncio.sleep(0.2)
            for season in item.seasons:
                request.app.program.em.cancel_job(season.id)
                await asyncio.sleep(0.2)
            for season, episodes in data.root.items():
                for episode, episode_data in episodes.items():
                    item_episode: Episode = next((_episode for _season in item.seasons if _season.number == season for _episode in _season.episodes if _episode.number == episode), None)
                    if item_episode:
                        request.app.program.em.cancel_job(item_episode.id)
                        await asyncio.sleep(0.2)
                        item_episode.reset()
                        item_episode.file = episode_data.filename
                        item_episode.folder = episode_data.filename
                        item_episode.alternative_folder = session.torrent_info.alternative_filename
                        item_episode.active_stream = {"infohash": session.magnet, "id": session.torrent_info.id}
                        torrent = rtn.rank(session.magnet, session.magnet)
                        item_episode.streams.append(ItemStream(torrent))
                        item_ids_to_submit.append(item_episode.id)
        item.store_state()
        log_string = item.log_string
        db_session.merge(item)
        db_session.commit()

    for item_id in item_ids_to_submit:
        request.app.program.em.add_event(Event("ManualAPI", item_id))

    return {"message": f"Updated given data to {log_string}"}

@router.post("/scrape/abort_session/{session_id}")
async def abort_manual_session(
    _: Request,
    background_tasks: BackgroundTasks,
    session_id: str
) -> SessionResponse:
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired")

    background_tasks.add_task(session_manager.abort_session, session_id)
    return {"message": f"Aborted session {session_id}"}

@router.post(
    "/scrape/complete_session/{session_id}",
    summary="Complete a manual scraping session",
    operation_id="complete_manual_session"
)
async def complete_manual_session(_: Request, session_id: str) -> SessionResponse:
    session = session_manager.get_session(session_id)

    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired")

    if not all([session.torrent_id, session.selected_files]):
        raise HTTPException(status_code=400, detail="Session is incomplete")

    session_manager.complete_session(session_id)
    return {"message": f"Completed session {session_id}"}