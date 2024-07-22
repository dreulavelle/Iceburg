from datetime import datetime
from enum import Enum
from typing import List, Optional

import Levenshtein
from fastapi import APIRouter, HTTPException, Request
from program.content.overseerr import Overseerr
from program.media.item import Episode, MediaItem, Movie, Season, Show
from program.media.state import States
from program.symlink import Symlinker
from pydantic import BaseModel
from utils.logger import logger

router = APIRouter(
    prefix="/items",
    tags=["items"],
    responses={404: {"description": "Not found"}},
)


class IMDbIDs(BaseModel):
    imdb_ids: Optional[List[str]] = None


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
    request: Request,
    limit: Optional[int] = 50,
    page: Optional[int] = 1,
    type: Optional[str] = None,
    state: Optional[str] = None,
    sort: Optional[str] = "desc",
    search: Optional[str] = None,
):
    if page < 1:
        raise HTTPException(status_code=400, detail="Page number must be 1 or greater.")

    if limit < 1:
        raise HTTPException(status_code=400, detail="Limit must be 1 or greater.")

    items = list(request.app.program.media_items._items.values())
    total_items = len(items)

    if search: # TODO: fix for search
        search_lower = search.lower()
        filtered_items = []
        if search_lower.startswith("tt"):
            item = request.app.program.media_items.get_item(search_lower)
            if item:
                filtered_items.append(item)
            else:
                raise HTTPException(status_code=404, detail="Item not found.")
        else:
            for item in items:
                if isinstance(item, MediaItem):
                    title_match = (
                        item.title
                        and Levenshtein.distance(search_lower, item.title.lower())
                        <= 0.90
                    )
                    imdb_match = (
                        item.imdb_id
                        and Levenshtein.distance(search_lower, item.imdb_id.lower())
                        <= 1
                    )
                    if title_match or imdb_match:
                        filtered_items.append(item)
        items = filtered_items

    if state:
        filter_lower = state.lower()
        filter_state = None
        for state in States:
            if Levenshtein.distance(filter_lower, state.name.lower()) <= 0.82:
                filter_state = state
                break
        if filter_state:
            items = [item for item in items if item.state == filter_state]
        else:
            valid_states = [state.name for state in States]
            raise HTTPException(
                status_code=400,
                detail=f"Invalid filter state: {state}. Valid states are: {valid_states}",
            )

    if type:
        type_lower = type.lower()
        if type_lower == "movie":
            items = list(request.app.program.media_items.movies.values())
            total_items = len(items)
        elif type_lower == "show":
            items = list(request.app.program.media_items.shows.values())
            total_items = len(items)
        elif type_lower == "season":
            items = list(request.app.program.media_items.seasons.values())
            total_items = len(items)
        elif type_lower == "episode":
            items = list(request.app.program.media_items.episodes.values())
            total_items = len(items)
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid type: {type}. Valid types are: ['movie', 'show', 'season', 'episode']",
            )

    if (
        sort and not search
    ):  # we don't want to sort search results as they are already sorted by relevance
        if sort.lower() == "asc":
            items = sorted(items, key=lambda x: x.requested_at)
        elif sort.lower() == "desc":
            items = sorted(items, key=lambda x: x.requested_at, reverse=True)
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid sort: {sort}. Valid sorts are: ['asc', 'desc']",
            )

    start = (page - 1) * limit
    end = start + limit
    items = items[start:end]
    total_pages = (total_items + limit - 1) // limit

    return {
        "success": True,
        "items": [item.to_dict() for item in items],
        "page": page,
        "limit": limit,
        "total_items": total_items,
        "total_pages": total_pages,
    }


@router.get("/extended/{item_id}")
async def get_extended_item_info(request: Request, item_id: str):
    mic: MediaItemContainer = request.app.program.media_items
    item = mic.get(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return {
        "success": True,
        "item": item.to_extended_dict(),
    }


@router.post("/add/imdb/{imdb_id}")
@router.post("/add/imdb/")
async def add_items(
    request: Request, imdb_id: Optional[str] = None, imdb_ids: Optional[IMDbIDs] = None
):
    if imdb_id:
        imdb_ids = IMDbIDs(imdb_ids=[imdb_id])
    elif (
        not imdb_ids or not imdb_ids.imdb_ids or any(not id for id in imdb_ids.imdb_ids)
    ):
        raise HTTPException(status_code=400, detail="No IMDb ID(s) provided")

    valid_ids = []
    for id in imdb_ids.imdb_ids:
        if not id.startswith("tt"):
            logger.warning(f"Invalid IMDb ID {id}, skipping")
        else:
            valid_ids.append(id)

    if not valid_ids:
        raise HTTPException(status_code=400, detail="No valid IMDb ID(s) provided")

    for id in valid_ids:
        item = MediaItem({"imdb_id": id, "requested_by": "riven"})
        request.app.program.add_to_queue(item)

    return {"success": True, "message": f"Added {len(valid_ids)} item(s) to the queue"}


@router.delete("/remove/")
async def remove_item(
    request: Request, item_id: Optional[str] = None, imdb_id: Optional[str] = None
):
    if item_id:
        item = request.app.program.media_items.get(item_id)
        id_type = "ID"
    elif imdb_id:
        item = next(
            (i for i in request.app.program.media_items if i.imdb_id == imdb_id), None
        )
        id_type = "IMDb ID"
    else:
        raise HTTPException(status_code=400, detail="No item ID or IMDb ID provided")

    if not item:
        logger.error(f"Item with {id_type} {item_id or imdb_id} not found")
        return {
            "success": False,
            "message": f"Item with {id_type} {item_id or imdb_id} not found. No action taken.",
        }

    try:
        # Remove the item from the media items container
        request.app.program.media_items.remove([item])
        logger.log("API", f"Removed item with {id_type} {item_id or imdb_id}")

        # Remove the symlinks associated with the item
        symlinker = request.app.program.service[Symlinker]
        symlinker.delete_item_symlinks(item)
        logger.log(
            "API", f"Removed symlink for item with {id_type} {item_id or imdb_id}"
        )

        # Save and reload the media items to ensure consistency
        symlinker.save_and_reload_media_items(request.app.program.media_items)
        logger.log(
            "API",
            f"Saved and reloaded media items after removing item with {id_type} {item_id or imdb_id}",
        )

        return {
            "success": True,
            "message": f"Successfully removed item with {id_type} {item_id or imdb_id}.",
        }
    except Exception as e:
        logger.error(f"Failed to remove item with {id_type} {item_id or imdb_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/imdb/{imdb_id}")
async def get_imdb_info(
    request: Request,
    imdb_id: str,
    season: Optional[int] = None,
    episode: Optional[int] = None,
):
    """
    Get the item with the given IMDb ID.
    If the season and episode are provided, get the item with the given season and episode.
    """
    item_id = imdb_id
    if season is not None:
        item_id = str(season) #, parent_id=item_id)
    if episode is not None:
        item_id = str(episode) #,  parent_id=item_id)
    
    item = request.app.program.media_items.get_item(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")

    return {"success": True, "item": item.to_extended_dict()}


@router.get("/incomplete")
async def get_incomplete_items(request: Request):
    if not hasattr(request.app, "program") or not hasattr(
        request.app.program, "media_items"
    ):
        logger.error("Program or media_items not found in the request app")
        raise HTTPException(status_code=500, detail="Internal server error")

    incomplete_items = request.app.program.media_items.get_incomplete_items()
    if not incomplete_items:
        return {"success": True, "incomplete_items": []}

    return {
        "success": True,
        "incomplete_items": [item.to_dict() for item in incomplete_items.values()],
    }
