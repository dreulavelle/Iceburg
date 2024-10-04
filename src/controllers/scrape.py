"""Scrape controller."""
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from program.scrapers import Scraping
from program.indexers.trakt import TraktIndexer
from program.media.item import MediaItem
from program.db.db import db

router = APIRouter(
    prefix="/scrape",
    tags=["scrape"]
)


@router.get(
    "",
    summary="Scrape Media Item",
    description="Scrape media item based on IMDb ID."
)
async def scrape(request: Request, imdb_id: str, season: int = None, episode: int = None):
    """
    Scrape media item based on IMDb ID.

    - **imdb_id**: IMDb ID of the media item.
    """
    if (services := request.app.program.services):
        scraping = services[Scraping]
        indexer = services[TraktIndexer]
    else:
        raise HTTPException(status_code=412, detail="Scraping services not initialized")

    try:
        with db.Session() as session:
            media_item = session.execute(
                select(MediaItem).where(
                    MediaItem.ids["imdb_id"] == imdb_id,
                    MediaItem.type.in_(["movie", "show"])
                )
            ).unique().scalar_one_or_none()
            if not media_item:
                indexed_item = MediaItem({"imdb_id": imdb_id})
                media_item = next(indexer.run(indexed_item))
                if not media_item:
                    raise HTTPException(status_code=204, detail="Media item not found")
                session.add(media_item)
                session.commit()
            session.refresh(media_item)

            if media_item.type == "show":
                if season and episode:
                    media_item = next((ep for ep in media_item.seasons[season - 1].episodes if ep.number == episode), None)
                    if not media_item:
                        raise HTTPException(status_code=204, detail="Episode not found")
                elif season:
                    media_item = media_item.seasons[season - 1]
                    if not media_item:
                        raise HTTPException(status_code=204, detail="Season not found")
            elif media_item.type == "movie" and (season or episode):
                raise HTTPException(status_code=204, detail="Item type returned movie, cannot scrape season or episode")

            results = scraping.scrape(media_item, log=False)
            if not results:
                return {"success": True, "data": []}

            data = [
                {
                    "raw_title": stream.raw_title,
                    "infohash": stream.infohash,
                    "rank": stream.rank
                } for stream in results.values()
            ]

    except StopIteration as e:
        raise HTTPException(status_code=204, detail="Media item not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"success": True, "data": data}
