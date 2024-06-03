from fastapi import APIRouter, HTTPException, Request
from program.media.state import States
from utils.logger import logger

router = APIRouter(
    prefix="/items",
    tags=["items"],
    responses={404: {"description": "Not found"}},
)


@router.get("/states")
async def get_states():
    return {
        "success": True,
        "states": [state for state in States],
    }


@router.get("/")
async def get_items(request: Request):
    return {
        "success": True,
        "items": [item.to_dict() for item in request.app.program.media_items],
    }


@router.get("/extended/{item_id}")
async def get_extended_item_info(request: Request, item_id: str):
    item = request.app.program.media_items.get_item_by_id(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return {
        "success": True,
        "item": item.to_extended_dict(),
    }

@router.delete("/remove/id/{item_id}")
async def remove_item(request: Request, item_id: str):
    item = request.app.program.media_items.get_item_by_id(item_id)
    if item is None:
        logger.error(f"Item with ID {item_id} not found")
        raise HTTPException(status_code=404, detail="Item not found")
    
    request.app.program.media_items.remove(item)
    if item.symlinked:
        request.app.program.media_items.remove_symlink(item)
        logger.success(f"Removed symlink for item with ID {item_id}")
    
    overseerr_result = request.app.program.content.overseerr.delete_request(item_id)
    if overseerr_result:
        logger.success(f"Deleted Overseerr request for item with ID {item_id}")
    else:
        logger.error(f"Failed to delete Overseerr request for item with ID {item_id}")
    
    return {
        "success": True,
        "message": f"Removed {item_id}",
    }

@router.delete("/remove/imdb/{imdb_id}")
async def remove_item_by_imdb(request: Request, imdb_id: str):
    item = request.app.program.media_items.get_item_by_imdb_id(imdb_id)
    if item is None:
        logger.error(f"Item with IMDb ID {imdb_id} not found")
        raise HTTPException(status_code=404, detail="Item not found")
    
    request.app.program.media_items.remove(item)
    if item.symlinked:
        request.app.program.media_items.remove_symlink(item)
        logger.success(f"Removed symlink for item with IMDb ID {imdb_id}")
    
    overseerr_result = request.app.program.content.overseerr.delete_request(imdb_id)
    if overseerr_result:
        logger.success(f"Deleted Overseerr request for item with IMDb ID {imdb_id}")
    else:
        logger.error(f"Failed to delete Overseerr request for item with IMDb ID {imdb_id}")
    
    return {
        "success": True,
        "message": f"Removed item with IMDb ID {imdb_id}",
    }

@router.get("/imdb/{imdb_id}")
async def get_imdb_info(request: Request, imdb_id: str):
    item = request.app.program.media_items.get_item_by_imdb_id(imdb_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return {"success": True, "item": item.to_extended_dict()}
