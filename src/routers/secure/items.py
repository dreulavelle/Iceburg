"""Items router"""
import asyncio
from datetime import datetime
from typing import Literal, Optional

import Levenshtein
from fastapi import APIRouter, Depends, HTTPException, Request, status
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import NoResultFound, SQLAlchemyError
from sqlalchemy.orm import Session, with_polymorphic

from program.db.db import get_db
from program.media.item import MediaItem, MediaType, Movie, Show, Season, Episode
from program.media.state import States
from program.services.content import Overseerr
from program.symlink import Symlinker
from program.types import Event

# Response Models
class MessageResponse(BaseModel):
    message: str

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


def handle_ids(ids: Optional[str]) -> list[str]:
    """Handle comma-separated IDs or single ID"""
    if not ids:
        raise HTTPException(status_code=400, detail="No item ID provided")
    return [str(id) for id in ids.split(",")] if "," in ids else [str(ids)]


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
    response_model=MessageResponse
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

    return MessageResponse(message=f"Added {len(valid_ids)} item(s) to the queue")


@router.get(
    "/{imdb_ids}",
    summary="Retrieve Media Items By IMDb IDs",
    description="Fetch media items by IMDb IDs",
    operation_id="get_items_by_imdb_ids",
)
async def get_items_by_imdb_ids(
    imdb_ids: str,
    session: Session = Depends(get_db)
) -> list[dict]:
    """Get media items by IMDb IDs"""
    try:
        items = []
        for imdb_id in imdb_ids.split(","):
            item = session.execute(
                select(MediaItem)
                .where(MediaItem.imdb_id == imdb_id)
            ).scalar_one_or_none()
            
            if item:
                items.append(item)
                
        return [item.to_dict() for item in items]
    except SQLAlchemyError as e:
        logger.error(f"Database error: {e}")
        raise HTTPException(status_code=500, detail="Database error")


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

# Pause-related endpoints (must come before generic /{id} routes)
@router.get("/paused", response_model=AllPausedResponse)
async def get_all_paused(
    type: Optional[str] = None,
    session: Session = Depends(get_db)
) -> AllPausedResponse:
    """Get all paused items"""
    try:
        # Build base query with explicit joins
        query = (
            select(MediaItem)
            .outerjoin(Movie, Movie.id == MediaItem.id)
            .outerjoin(Show, Show.id == MediaItem.id)
            .outerjoin(Season, Season.id == MediaItem.id)
            .outerjoin(Episode, Episode.id == MediaItem.id)
            .distinct()
            .where(MediaItem.is_paused == True)
        )

        if type:
            type_lower = type.lower()
            valid_types = [t.value.lower() for t in MediaType]
            if type_lower not in valid_types:
                raise HTTPException(
                    status_code=400, 
                    detail=f"Invalid type. Must be one of: {', '.join(valid_types)}"
                )
            query = query.where(func.lower(MediaItem.type) == type_lower)

        logger.debug(f"Executing query: {query}")
        logger.debug(f"Query parameters: {query.compile().params}")
        
        result = session.execute(query).scalars().unique().all()
        logger.debug(f"Found {len(result)} paused items")
        
        # Add debug logging for each item
        for item in result:
            logger.debug(f"Paused item: id={item.id}, type={item.type}, is_paused={item.is_paused}, class={item.__class__.__name__}")
            logger.debug(f"Item dict before conversion: {vars(item)}")
            
        items = [item.to_dict() for item in result]
        logger.debug(f"Converted items to dict: {items}")

        return AllPausedResponse(
            count=len(result),
            items=items
        )
    except SQLAlchemyError as e:
        logger.error(f"Database error: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Database error occurred: {str(e)}"
        )

@router.get("/paused/count", response_model=dict)
async def get_paused_count(
    type: Optional[str] = None,
    session: Session = Depends(get_db)
) -> dict:
    """Get count of paused items"""
    try:
        # Use same explicit join structure
        query = (
            select(func.count(func.distinct(MediaItem.id)))
            .select_from(MediaItem)
            .outerjoin(Movie, Movie.id == MediaItem.id)
            .outerjoin(Show, Show.id == MediaItem.id)
            .outerjoin(Season, Season.id == MediaItem.id)
            .outerjoin(Episode, Episode.id == MediaItem.id)
            .where(MediaItem.is_paused == True)
        )

        if type:
            type_lower = type.lower()
            query = query.where(func.lower(MediaItem.type) == type_lower)

        logger.debug(f"Executing count query: {query}")
        count = session.execute(query).scalar()
        logger.debug(f"Found {count} paused items")

        return {"count": count}
    except SQLAlchemyError as e:
        logger.error(f"Database error: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Database error occurred: {str(e)}"
        )

@router.get("/pause/{id}", response_model=PauseStateResponse)
async def get_pause_state(
    id: str,
    session: Session = Depends(get_db)
) -> PauseStateResponse:
    """Check if a media item is paused"""
    try:
        item = session.execute(
            select(MediaItem).where(MediaItem.id == id)
        ).scalar_one_or_none()
        
        if not item:
            raise HTTPException(status_code=404, detail=f"Item {id} not found")
        
        return PauseStateResponse(
            is_paused=item.is_paused,
            paused_at=str(item.paused_at) if item.paused_at else None,
            item_id=item.id,
            title=item.title
        )
    except SQLAlchemyError as e:
        logger.error(f"Database error: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Database error occurred: {str(e)}"
        )

@router.post("/{id}/pause", response_model=PauseResponse)
@router.post("/pause", response_model=PauseResponse)
async def pause_items(
    request: Request,
    id: Optional[str] = None,  # Path parameter
    ids: Optional[str] = None,  # Query parameter
    session: Session = Depends(get_db)
) -> PauseResponse:
    """Pause one or more media items"""
    try:
        item_ids = []
        if id:
            item_ids = [id]
        elif ids:
            item_ids = [i.strip() for i in ids.split(",") if i.strip()]
        
        if not item_ids:
            raise HTTPException(status_code=400, detail="No item IDs provided")
            
        query = select(MediaItem).where(MediaItem.id.in_(item_ids))
        items = session.execute(query).scalars().all()
        
        found_ids = {item.id for item in items}
        missing_ids = set(item_ids) - found_ids
        
        if not items:
            raise HTTPException(status_code=404, detail="No items found")
            
        for item in items:
            if not item.is_paused:  # Only pause if not already paused
                item.is_paused = True
                item.paused_at = datetime.utcnow()
                item.paused_by = request.state.user.username if hasattr(request.state, 'user') else None
        
        session.commit()
        
        message = f"Successfully paused {len(items)} items"
        if missing_ids:
            message += f". {len(missing_ids)} items not found: {', '.join(missing_ids)}"
        
        return PauseResponse(
            message=message,
            ids=list(found_ids)
        )
    except SQLAlchemyError as e:
        session.rollback()
        logger.error(f"Database error: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Database error occurred: {str(e)}"
        )

@router.post("/{id}/unpause", response_model=PauseResponse)
@router.post("/unpause", response_model=PauseResponse)
async def unpause_items(
    request: Request,
    id: Optional[str] = None,  # Path parameter
    ids: Optional[str] = None,  # Query parameter
    session: Session = Depends(get_db)
) -> PauseResponse:
    """Unpause one or more media items"""
    try:
        item_ids = []
        if id:
            item_ids = [id]
        elif ids:
            item_ids = [i.strip() for i in ids.split(",") if i.strip()]
        
        if not item_ids:
            raise HTTPException(status_code=400, detail="No item IDs provided")
            
        query = select(MediaItem).where(MediaItem.id.in_(item_ids))
        items = session.execute(query).scalars().all()
        
        found_ids = {item.id for item in items}
        missing_ids = set(item_ids) - found_ids
        
        if not items:
            raise HTTPException(status_code=404, detail="No items found")
            
        for item in items:
            item.is_paused = False
            item.unpaused_at = datetime.utcnow()
        
        session.commit()
        
        message = f"Successfully unpaused {len(items)} items"
        if missing_ids:
            message += f". {len(missing_ids)} items not found: {', '.join(missing_ids)}"
        
        return PauseResponse(
            message=message,
            ids=list(found_ids)
        )
    except SQLAlchemyError as e:
        session.rollback()
        logger.error(f"Database error: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Database error occurred: {str(e)}"
        )

# Generic item routes (must come after specific routes)
@router.get(
    "/{id}",
    summary="Retrieve Media Item",
    description="Fetch a single media item by ID",
    operation_id="get_item",
    response_model=ItemsResponse
)
async def get_item(
    id: str,
    session: Session = Depends(get_db)
) -> ItemsResponse:
    """Get a specific media item"""
    try:
        query = (
            select(MediaItem)
            .where(MediaItem.id == id)
        )
        
        item = session.execute(query).scalar_one_or_none()
        
        if not item:
            raise HTTPException(status_code=404, detail=f"Item {id} not found")
            
        return ItemsResponse(
            success=True,
            items=[item.to_dict()],
            page=1,
            limit=1,
            total_items=1,
            total_pages=1
        )
    except SQLAlchemyError as e:
        logger.error(f"Database error: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Database error occurred: {str(e)}"
        )
