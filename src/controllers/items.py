import asyncio
from datetime import datetime
from typing import Optional

import Levenshtein
from RTN import RTN, Torrent
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.exc import NoResultFound

from program.content import Overseerr
from program.db.db import db
from program.db.db_functions import (
    clear_streams,
    delete_media_item,
    get_media_items_by_ids,
    get_parent_items_by_ids,
    reset_media_item,
)
from program.media.item import MediaItem
from program.media.state import States
from program.symlink import Symlinker
from program.downloaders import Downloader, get_needed_media
from program.downloaders.realdebrid import RealDebridDownloader, add_torrent_magnet, torrent_info
from program.settings.versions import models
from program.settings.manager import settings_manager
from program.media.stream import Stream
from program.scrapers.shared import rtn
from program.types import Event
from utils.logger import logger

router = APIRouter(
    prefix="/items",
    tags=["items"],
    responses={404: {"description": "Not found"}},
)

def handle_ids(ids: str) -> list[int]:
    ids = [int(id) for id in ids.split(",")] if "," in ids else [int(ids)]
    if not ids:
        raise HTTPException(status_code=400, detail="No item ID provided")
    return ids

@router.get("/states")
async def get_states():
    return {
        "success": True,
        "states": [state for state in States],
    }

@router.get(
    "",
    summary="Retrieve Media Items",
    description="Fetch media items with optional filters and pagination",
)
async def get_items(
    _: Request,
    limit: Optional[int] = 50,
    page: Optional[int] = 1,
    type: Optional[str] = None,
    state: Optional[str] = None,
    sort: Optional[str] = "date_desc",
    search: Optional[str] = None,
    extended: Optional[bool] = False,
):
    if page < 1:
        raise HTTPException(status_code=400, detail="Page number must be 1 or greater.")

    if limit < 1:
        raise HTTPException(status_code=400, detail="Limit must be 1 or greater.")

    query = select(MediaItem)

    if search:
        search_lower = search.lower()
        if search_lower.startswith("tt"):
            query = query.where(MediaItem.ids["imdb_id"] == search_lower)
        else:
            query = query.where(
                (func.lower(MediaItem.title).like(f"%{search_lower}%")) |
                (func.lower(MediaItem.ids["imdb_id"]).like(f"%{search_lower}%"))
            )

    if state:
        filter_lower = state.lower()
        filter_state = None
        for state_enum in States:
            if Levenshtein.ratio(filter_lower, state_enum.name.lower()) <= 0.82:
                filter_state = state_enum
                break
        if filter_state:
            query = query.where(MediaItem.last_state == filter_state)
        else:
            valid_states = [state_enum.name for state_enum in States]
            raise HTTPException(
                status_code=400,
                detail=f"Invalid filter state: {state}. Valid states are: {valid_states}",
            )

    if type:
        if "," in type:
            types = type.split(",")
            for type in types:
                if type not in ["movie", "show", "season", "episode"]:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid type: {type}. Valid types are: ['movie', 'show', 'season', 'episode']")
        else:
            types=[type]
        query = query.where(MediaItem.type.in_(types))

    if sort and not search:
        sort_lower = sort.lower()
        if sort_lower == "title_asc":
            query = query.order_by(MediaItem.title.asc())
        elif sort_lower == "title_desc":
            query = query.order_by(MediaItem.title.desc())
        elif sort_lower == "date_asc":
            query = query.order_by(MediaItem.requested_at.asc())
        elif sort_lower == "date_desc":
            query = query.order_by(MediaItem.requested_at.desc())
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid sort: {sort}. Valid sorts are: ['title_asc', 'title_desc', 'date_asc', 'date_desc']",
            )

    with db.Session() as session:
        total_items = session.execute(select(func.count()).select_from(query.subquery())).scalar_one()
        items = session.execute(query.offset((page - 1) * limit).limit(limit)).unique().scalars().all()

        total_pages = (total_items + limit - 1) // limit

        return {
            "success": True,
            "items": [item.to_extended_dict() if extended else item.to_dict() for item in items],
            "page": page,
            "limit": limit,
            "total_items": total_items,
            "total_pages": total_pages,
        }


@router.post(
        "/add",
        summary="Add Media Items",
        description="Add media items with bases on imdb IDs",
)
async def add_items(
    request: Request, imdb_ids: str = None
):

    if not imdb_ids:
        raise HTTPException(status_code=400, detail="No IMDb ID(s) provided")

    ids = imdb_ids.split(",")

    valid_ids = []
    for id in ids:
        if not id.startswith("tt"):
            logger.warning(f"Invalid IMDb ID {id}, skipping")
        else:
            valid_ids.append(id)

    if not valid_ids:
        raise HTTPException(status_code=400, detail="No valid IMDb ID(s) provided")

    with db.Session() as _:
        for id in valid_ids:
            item = MediaItem({"imdb_id": id, "requested_by": "riven", "requested_at": datetime.now()})
            request.app.program.em.add_item(item, "ApiAdd")

    return {"success": True, "message": f"Added {len(valid_ids)} item(s) to the queue"}

@router.get(
    "/{id}",
    summary="Retrieve Media Item",
    description="Fetch a single media item by ID",
)
async def get_item(request: Request, id: int):
    with db.Session() as session:
        try:
            item = session.execute(select(MediaItem).where(MediaItem.id == id)).unique().scalar_one()
        except NoResultFound:
            raise HTTPException(status_code=404, detail="Item not found")
        return {"success": True, "item": item.to_extended_dict()}

@router.get(
    "/{imdb_ids}",
    summary="Retrieve Media Items By IMDb IDs",
    description="Fetch media items by IMDb IDs",
)
async def get_items_by_imdb_ids(request: Request, imdb_ids: str):
    ids = imdb_ids.split(",")
    with db.Session() as session:
        items = []
        for id in ids:
            item = session.execute(select(MediaItem).where(MediaItem.ids["imdb_id"] == id)).unique().scalar_one()
            if item:
                items.append(item)
        return {"success": True, "items": [item.to_extended_dict() for item in items]}

@router.post(
        "/reset",
        summary="Reset Media Items",
        description="Reset media items with bases on item IDs",
)
async def reset_items(
    request: Request, ids: str
):
    ids = handle_ids(ids)
    try:
        media_items = get_media_items_by_ids(ids)
        if not media_items or len(media_items) != len(ids):
            raise ValueError("Invalid item ID(s) provided. Some items may not exist.")
        for media_item in media_items:
            try:
                request.app.program.em.cancel_job(media_item)
                clear_streams(media_item)
                reset_media_item(media_item)
            except Exception as e:
                logger.error(f"Failed to reset item with id {media_item.id}: {str(e)}")
                continue
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, "message": f"Reset items with id {ids}"}

@router.post(
        "/retry",
        summary="Retry Media Items",
        description="Retry media items with bases on item IDs",
)
async def retry_items(request: Request, ids: str):
    ids = handle_ids(ids)
    try:
        media_items = get_media_items_by_ids(ids)
        if not media_items or len(media_items) != len(ids):
            raise ValueError("Invalid item ID(s) provided. Some items may not exist.")
        for media_item in media_items:
            request.app.program.em.cancel_job(media_item)
            await asyncio.sleep(0.1) # Ensure cancellation is processed
            request.app.program.em.add_item(media_item)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"success": True, "message": f"Retried items with ids {ids}"}

@router.delete(
    "/remove",
    summary="Remove Media Items",
    description="Remove media items based on item IDs",
)
async def remove_item(request: Request, ids: str):
    ids = handle_ids(ids)
    try:
        media_items = get_parent_items_by_ids(ids)
        if not media_items:
            raise ValueError("Invalid item ID(s) provided. Some items may not exist.")
        for media_item in media_items:
            logger.debug(f"Removing item {media_item.title} with ID {media_item.id}")
            request.app.program.em.cancel_job(media_item)
            await asyncio.sleep(0.1) # Ensure cancellation is processed
            clear_streams(media_item)
            symlink_service = request.app.program.services.get(Symlinker)
            if symlink_service:
                symlink_service.delete_item_symlinks(media_item)
            if media_item.requested_by == "overseerr" and media_item.requested_id:
                logger.debug(f"Item was originally requested by Overseerr, deleting request within Overseerr...")
                Overseerr.delete_request(media_item.requested_id)
            delete_media_item(media_item)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"success": True, "message": f"Removed items with ids {ids}"}

@router.post("/{id}/set_torrent_rd_magnet", description="Set a torrent for a media item using a magnet link.")
def add_torrent(request: Request, id: int, magnet: str):
    torrent_id = ""
    try:
        torrent_id = add_torrent_magnet(magnet)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to add torrent.") from None
    
    return set_torrent_rd(request, id, torrent_id)

def reset_item_to_scraped(item: MediaItem):
    item.last_state = States.Scraped
    item.symlinked = False
    item.symlink_path = None
    item.symlinked_at = None
    item.symlinked_times = 0
    item.update_folder = None
    item.file = None
    item.folder = None

def create_stream(hash, torrent_info):
    try:
        torrent: Torrent = rtn.rank(
            raw_title=torrent_info["filename"],
            infohash=hash,
            remove_trash=False
        )
        return Stream(torrent)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to rank torrent: {e}") from e

@router.post("/{id}/set_torrent_rd", description="Set a torrent for a media item using RD torrent ID.")
def set_torrent_rd(request: Request, id: int, torrent_id: str):

    downloader: Downloader = request.app.program.services.get(Downloader)
    with db.Session() as session:
        item: MediaItem = session.execute(select(MediaItem).where(MediaItem._id == id).outerjoin(MediaItem.streams)).unique().scalar_one_or_none()

        if item is None:
            raise HTTPException(status_code=404, detail="Item not found")

        fetched_torrent_info = torrent_info(torrent_id)

        stream = session.execute(select(Stream).where(Stream.infohash == fetched_torrent_info["hash"])).scalars().first()
        hash = fetched_torrent_info["hash"]

        # Create stream if it doesn't exist
        if stream is None:
            stream = create_stream(hash, fetched_torrent_info)
            item.streams.append(stream)

        # check if the stream exists in the item
        stream_exists_in_item = next((stream for stream in item.streams if stream.infohash == hash), None)
        if stream_exists_in_item is None:
            item.streams.append(stream)
        
        reset_item_to_scraped(item)

        # reset episodes if it's a season
        if item.type == "season":
            logger.debug(f"Resetting episodes for {item.title}")
            for episode in item.episodes:
                reset_item_to_scraped(episode)

        needed_media = get_needed_media(item)
        cached_streams = downloader.get_cached_streams([hash], needed_media)

        if len(cached_streams) == 0:
            session.rollback()
            raise HTTPException(status_code=400, detail=f"No cached torrents found for {item.log_string}")

        item.active_stream = cached_streams[0]
        try:
            downloader.download(item, item.active_stream)
        except Exception as e:
            logger.error(f"Failed to download {item.log_string}: {e}")
            if item.active_stream.get("infohash", None):
                downloader._delete_and_reset_active_stream(item)
            session.rollback()
            raise HTTPException(status_code=500, detail=f"Failed to download {item.log_string}: {e}") from e

        session.commit()

        request.app.program.em.add_event(Event("Symlinker", item))

        return {"success": True, "message": f"Set torrent for {item.title} to {torrent_id}"}

# These require downloaders to be refactored

# @router.get("/cached")
# async def manual_scrape(request: Request, ids: str):
#     scraper = request.app.program.services.get(Scraping)
#     downloader = request.app.program.services.get(Downloader).service
#     if downloader.__class__.__name__ not in ["RealDebridDownloader", "TorBoxDownloader"]:
#         raise HTTPException(status_code=400, detail="Only Real-Debrid is supported for manual scraping currently")
#     ids = [int(id) for id in ids.split(",")] if "," in ids else [int(ids)]
#     if not ids:
#         raise HTTPException(status_code=400, detail="No item ID provided")
#     with db.Session() as session:
#         items = []
#         return_dict = {}
#         for id in ids:
#             items.append(session.execute(select(MediaItem).where(MediaItem.id == id)).unique().scalar_one())
#     if any(item for item in items if item.type in ["Season", "Episode"]):
#         raise HTTPException(status_code=400, detail="Only shows and movies can be manually scraped currently")
#     for item in items:
#         new_item = item.__class__({})
#         # new_item.parent = item.parent
#         new_item.copy(item)
#         new_item.copy_other_media_attr(item)
#         scraped_results = scraper.scrape(new_item, log=False)
#         cached_hashes = downloader.get_cached_hashes(new_item, scraped_results)
#         for hash, stream in scraped_results.items():
#             return_dict[hash] = {"cached": hash in cached_hashes, "name": stream.raw_title}
#         return {"success": True, "data": return_dict}

# @router.post("/download")
# async def download(request: Request, id: str, hash: str):
#     downloader = request.app.program.services.get(Downloader).service
#     with db.Session() as session:
#         item = session.execute(select(MediaItem).where(MediaItem.id == id)).unique().scalar_one()
#         item.reset(True)
#         downloader.download_cached(item, hash)
#         request.app.program.add_to_queue(item)
#         return {"success": True, "message": f"Downloading {item.title} with hash {hash}"}