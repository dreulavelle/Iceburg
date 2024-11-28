import asyncio
from datetime import datetime
from typing import Literal, Optional

import Levenshtein
from fastapi import APIRouter, Depends, HTTPException, Request, status
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.orm import Session

from program.db import db_functions
from program.db.db import db, get_db
from program.media.item import MediaItem, MediaType  # Import MediaType from item module
from program.media.state import States
from program.services.content import Overseerr
from program.symlink import Symlinker
from program.types import Event

from ..models.shared import MessageResponse

class StateResponse(BaseModel):
    success: bool
    states: list[str]

class ItemsResponse(BaseModel):
    success: bool
    items: list[dict]
    page: int
    limit: int
    total_items: int
    total_pages: int

class ResetResponse(BaseModel):
    message: str
    ids: list[str]

class RetryResponse(BaseModel):
    message: str
    ids: list[str]

class RemoveResponse(BaseModel):
    message: str
    ids: list[str]

class PauseResponse(BaseModel):
    """Response model for pause/unpause operations"""
    message: str
    ids: list[str]

class PauseStateResponse(BaseModel):
    """Response model for pause state check"""
    is_paused: bool
    paused_at: Optional[str]
    item_id: str
    title: Optional[str]

class AllPausedResponse(BaseModel):
    """Response model for getting all paused items"""
    count: int
    items: list[dict]

router = APIRouter(
    prefix="/items",
    tags=["items"],
)


def handle_ids(ids: str) -> list[str]:
    ids = [str(id) for id in ids.split(",")] if "," in ids else [str(ids)]
    if not ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No item ID provided")
    return ids


@router.get("/states", operation_id="get_states")
async def get_states() -> StateResponse:
    return {
        "success": True,
        "states": [state for state in States],
    }


@router.get(
    "",
    summary="Retrieve Media Items",
    description="Fetch media items with optional filters and pagination",
    operation_id="get_items",
)
async def get_items(
    _: Request,
    limit: Optional[int] = 50,
    page: Optional[int] = 1,
    type: Optional[str] = None,
    states: Optional[str] = None,
    sort: Optional[
        Literal["date_desc", "date_asc", "title_asc", "title_desc"]
    ] = "date_desc",
    search: Optional[str] = None,
    extended: Optional[bool] = False,
    is_anime: Optional[bool] = False,
) -> ItemsResponse:
    if page < 1:
        raise HTTPException(status_code=400, detail="Page number must be 1 or greater.")

    if limit < 1:
        raise HTTPException(status_code=400, detail="Limit must be 1 or greater.")

    query = select(MediaItem)

    if search:
        search_lower = search.lower()
        if search_lower.startswith("tt"):
            query = query.where(MediaItem.imdb_id == search_lower)
        else:
            query = query.where(
                (func.lower(MediaItem.title).like(f"%{search_lower}%"))
                | (func.lower(MediaItem.imdb_id).like(f"%{search_lower}%"))
            )

    if states:
        states = states.split(",")
        filter_states = []
        for state in states:
            filter_lower = state.lower()
            for state_enum in States:
                if Levenshtein.ratio(filter_lower, state_enum.name.lower()) >= 0.82:
                    filter_states.append(state_enum)
                    break
        if 'All' not in states:
            if len(filter_states) == len(states):
                query = query.where(MediaItem.last_state.in_(filter_states))
            else:
                valid_states = [state_enum.name for state_enum in States]
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid filter states: {states}. Valid states are: {valid_states}",
                )

    if type:
        if "," in type:
            types = type.split(",")
            for type in types:
                if type not in ["movie", "show", "season", "episode", "anime"]:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid type: {type}. Valid types are: ['movie', 'show', 'season', 'episode', 'anime']",
                    )
        else:
            types = [type]
        if "anime" in types:
            types = [type for type in types if type != "anime"]
            query = query.where(
                or_(
                    and_(
                        MediaItem.type.in_(["movie", "show"]),
                        MediaItem.is_anime == True,
                    ),
                    MediaItem.type.in_(types),
                )
            )
        else:
            query = query.where(MediaItem.type.in_(types))

    if is_anime:
        query = query.where(MediaItem.is_anime is True)

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
        total_items = session.execute(
            select(func.count()).select_from(query.subquery())
        ).scalar_one()
        items = (
            session.execute(query.offset((page - 1) * limit).limit(limit))
            .unique()
            .scalars()
            .all()
        )

        total_pages = (total_items + limit - 1) // limit

        return {
            "success": True,
            "items": [
                item.to_extended_dict() if extended else item.to_dict()
                for item in items
            ],
            "page": page,
            "limit": limit,
            "total_items": total_items,
            "total_pages": total_pages,
        }


@router.post(
    "/add",
    summary="Add Media Items",
    description="Add media items with bases on imdb IDs",
    operation_id="add_items",
)
async def add_items(request: Request, imdb_ids: str = None) -> MessageResponse:
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
            item = MediaItem(
                {"imdb_id": id, "requested_by": "riven", "requested_at": datetime.now()}
            )
            request.app.program.em.add_item(item)

    return {"message": f"Added {len(valid_ids)} item(s) to the queue"}

@router.get(
    "/{id}",
    summary="Retrieve Media Item",
    description="Fetch a single media item by ID",
    operation_id="get_item",
)
async def get_item(_: Request, id: str, use_tmdb_id: Optional[bool] = False) -> dict:
    with db.Session() as session:
        try:
            query = select(MediaItem)
            if use_tmdb_id:
                query = query.where(MediaItem.tmdb_id == id)
            else:
                query = query.where(MediaItem.id == id)
            item = session.execute(query).unique().scalar_one()
        except NoResultFound:
            raise HTTPException(status_code=404, detail="Item not found")
        return item.to_extended_dict(with_streams=False)


@router.get(
    "/{imdb_ids}",
    summary="Retrieve Media Items By IMDb IDs",
    description="Fetch media items by IMDb IDs",
    operation_id="get_items_by_imdb_ids",
)
async def get_items_by_imdb_ids(request: Request, imdb_ids: str) -> list[dict]:
    ids = imdb_ids.split(",")
    with db.Session() as session:
        items = []
        for id in ids:
            item = (
                session.execute(select(MediaItem).where(MediaItem.imdb_id == id))
                .unique()
                .scalar_one()
            )
            if item:
                items.append(item)
        return [item.to_extended_dict() for item in items]


@router.post(
    "/reset",
    summary="Reset Media Items",
    description="Reset media items with bases on item IDs",
    operation_id="reset_items",
)
async def reset_items(request: Request, ids: str) -> ResetResponse:
    ids = handle_ids(ids)
    try:
        for media_item in db_functions.get_items_by_ids(ids):
            try:
                request.app.program.em.cancel_job(media_item.id)
                db_functions.clear_streams(media_item)
                db_functions.reset_media_item(media_item)
            except ValueError as e:
                logger.error(f"Failed to reset item with id {media_item.id}: {str(e)}")
                continue
            except Exception as e:
                logger.error(f"Unexpected error while resetting item with id {media_item.id}: {str(e)}")
                continue
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"message": f"Reset items with id {ids}", "ids": ids}


@router.post(
    "/retry",
    summary="Retry Media Items",
    description="Retry media items with bases on item IDs",
    operation_id="retry_items",
)
async def retry_items(request: Request, ids: str) -> RetryResponse:
    """Re-add items to the queue"""
    ids = handle_ids(ids)
    for id in ids:
        try:
            item = db_functions.get_item_by_id(id)
            if item:
                with db.Session() as session:
                    item.scraped_at = None
                    item.scraped_times = 1
                    session.merge(item)
                    session.commit()
                request.app.program.em.add_event(Event("RetryItem", id))
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    return {"message": f"Retried items with ids {ids}", "ids": ids}


@router.delete(
    "/remove",
    summary="Remove Media Items",
    description="Remove media items based on item IDs",
    operation_id="remove_item",
)
async def remove_item(request: Request, ids: str) -> RemoveResponse:
    ids: list[str] = handle_ids(ids)
    try:
        media_items: list[MediaItem] = db_functions.get_items_by_ids(ids, ["movie", "show"])
        if not media_items:
            return HTTPException(status_code=404, detail="Item(s) not found")
        for item in media_items:
            logger.debug(f"Removing item with ID {item.id}")
            request.app.program.em.cancel_job(item.id)
            await asyncio.sleep(0.2)  # Ensure cancellation is processed
            if item.type == "show":
                for season in item.seasons:
                    for episode in season.episodes:
                        request.app.program.em.cancel_job(episode.id)
                        await asyncio.sleep(0.2)
                        db_functions.delete_media_item_by_id(episode.id)
                    request.app.program.em.cancel_job(season.id)
                    await asyncio.sleep(0.2)
                    db_functions.delete_media_item_by_id(season.id)

            db_functions.clear_streams_by_id(item.id)

            symlink_service = request.app.program.services.get(Symlinker)
            if symlink_service:
                symlink_service.delete_item_symlinks_by_id(item.id)

            if item.overseerr_id:
                overseerr: Overseerr = request.app.program.services.get(Overseerr)
                if overseerr:
                    overseerr.delete_request(item.overseerr_id)
                    logger.debug(f"Deleted request from Overseerr with ID {item.overseerr_id}")

            logger.debug(f"Deleting item from database with ID {item.id}")
            db_functions.delete_media_item_by_id(item.id)
            logger.info(f"Successfully removed item with ID {item.id}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"message": f"Removed items with ids {ids}", "ids": ids}

@router.get(
    "/{item_id}/streams"
)
async def get_item_streams(_: Request, item_id: str, db: Session = Depends(get_db)):
    item: MediaItem = (
        db.execute(
            select(MediaItem)
            .where(MediaItem.id == item_id)
        )
        .unique()
        .scalar_one_or_none()
    )

    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")

    return {
        "message": f"Retrieved streams for item {item_id}",
        "streams": item.streams,
        "blacklisted_streams": item.blacklisted_streams
    }

@router.post(
    "/{item_id}/streams/{stream_id}/blacklist"
)
async def blacklist_stream(_: Request, item_id: str, stream_id: int, db: Session = Depends(get_db)):
    item: MediaItem = (
        db.execute(
            select(MediaItem)
            .where(MediaItem.id == item_id)
        )
        .unique()
        .scalar_one_or_none()
    )
    stream = next((stream for stream in item.streams if stream.id == stream_id), None)

    if not item or not stream:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item or stream not found")

    db_functions.blacklist_stream(item, stream, db)

    return {
        "message": f"Blacklisted stream {stream_id} for item {item_id}",
    }

@router.post(
    "{item_id}/streams/{stream_id}/unblacklist"
)
async def unblacklist_stream(_: Request, item_id: str, stream_id: int, db: Session = Depends(get_db)):
    item: MediaItem = (
        db.execute(
            select(MediaItem)
            .where(MediaItem.id == item_id)
        )
        .unique()
        .scalar_one_or_none()
    )

    stream = next((stream for stream in item.blacklisted_streams if stream.id == stream_id), None)

    if not item or not stream:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item or stream not found")

    db_functions.unblacklist_stream(item, stream, db)

    return {
        "message": f"Unblacklisted stream {stream_id} for item {item_id}",
    }

@router.post("/{id}/pause", response_model=PauseResponse, operation_id="pause_item")
@router.post("/pause", response_model=PauseResponse, operation_id="pause_items")
async def pause_items(request: Request, ids: str = None, db: Session = Depends(get_db)):
    """Pause media items from being processed"""
    item_ids = handle_ids(ids)
    
    items = db.execute(
        select(MediaItem).where(MediaItem.id.in_(item_ids))
    ).unique().scalars().all()
    
    if not items:
        raise HTTPException(status_code=404, detail="No items found")
    
    try:
        for item in items:
            item.pause()
        db.commit()
    except SQLAlchemyError as e:
        db.rollback()
        logger.error(f"Failed to pause items: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to pause items")
    
    return {
        "message": f"Successfully paused {len(items)} items",
        "ids": item_ids
    }

@router.post("/{id}/unpause", response_model=PauseResponse, operation_id="unpause_item")
@router.post("/unpause", response_model=PauseResponse, operation_id="unpause_items")
async def unpause_items(request: Request, ids: str = None, db: Session = Depends(get_db)):
    """Unpause media items to resume processing"""
    item_ids = handle_ids(ids)
    
    items = db.execute(
        select(MediaItem).where(MediaItem.id.in_(item_ids))
    ).unique().scalars().all()
    
    if not items:
        raise HTTPException(status_code=404, detail="No items found")
    try:
        for item in items:
            item.unpause()
        db.commit()
    except SQLAlchemyError as e:
        db.rollback()
        logger.error(f"Failed to unpause items: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to unpause items")
    
    return {
        "message": f"Successfully unpaused {len(items)} items",
        "ids": item_ids
    }

@router.get("/{id}/pause", response_model=PauseStateResponse, operation_id="get_pause_state")
async def get_pause_state(request: Request, id: str, db: Session = Depends(get_db)):
    """Check if a media item is paused"""
    item = db.execute(
        select(MediaItem).where(MediaItem.id == id)
    ).unique().scalar_one_or_none()
    
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    
    return {
        "is_paused": item.is_paused,
        "paused_at": str(item.paused_at) if item.paused_at else None,
        "item_id": item.id,
        "title": item.title
    }

@router.get("/paused", response_model=AllPausedResponse, operation_id="get_all_paused", responses={200: {"description": "Success, even if no items found"}}, response_model_exclude_none=True)
async def get_all_paused(
    request: Request, 
    type: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Get all paused media items, optionally filtered by type"""
    # Use raw SQL to verify data
    query = select(
        MediaItem.id,
        MediaItem.title,
        MediaItem.type,
        MediaItem.is_paused,
        MediaItem.paused_at
    ).where(MediaItem.is_paused.is_(True))
    
    if type:
        valid_types = [t.value for t in MediaType]
        if type not in valid_types:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid type. Must be one of: {', '.join(valid_types)}"
            )
        query = query.where(MediaItem.type == type)
    
    logger.debug(f"Executing query: {str(query)}")
    result = db.execute(query)
    rows = result.fetchall()
    logger.debug(f"Raw SQL found {len(rows)} rows")
    
    # Map results to response model
    items = [
        {
            "id": row.id,
            "title": row.title,
            "type": row.type,
            "paused_at": str(row.paused_at) if row.paused_at else None
        }
        for row in rows
    ]
    
    return AllPausedResponse(count=len(items), items=items)

@router.get("/paused/count", response_model=dict, operation_id="get_paused_count")
async def get_paused_count(
    request: Request,
    type: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Get total count of paused items, optionally filtered by type"""
    try:
        # Build query using SQLAlchemy
        query = select(func.count(MediaItem.id)).where(MediaItem.is_paused.is_(True))
        
        if type:
            valid_types = [t.value for t in MediaType]
            if type not in valid_types:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid type. Must be one of: {', '.join(valid_types)}"
                )
            query = query.where(MediaItem.type == type)
        
        logger.debug(f"Executing count query: {str(query)}")
        count = db.scalar(query) or 0  # Default to 0 if None
        logger.debug(f"Found {count} paused items")
        
        return {
            "total": count,
            "type": type if type else "all"
        }
    except SQLAlchemyError as e:
        logger.error(f"Error in get_paused_count: {str(e)}")
        # Return 0 count on error
        return {
            "total": 0,
            "type": type if type else "all",
            "error": "Failed to get count"
        }
