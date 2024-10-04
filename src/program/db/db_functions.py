import os
import shutil
from typing import TYPE_CHECKING, List

import alembic
import sqlalchemy
from sqlalchemy import cast, delete, func, insert, select, text, union_all
from sqlalchemy.orm import Session, aliased, joinedload

from program.libraries.symlink import fix_broken_symlinks
from program.media.stream import Stream, StreamBlacklistRelation, StreamRelation
from program.settings.manager import settings_manager
from loguru import logger

from .db import alembic, db, script_location, ensure_alembic_version_table

if TYPE_CHECKING:
    from program.media.item import MediaItem


def get_media_items_by_ids(media_item_ids: list[int]):
    """Retrieve multiple MediaItems by a list of MediaItem _ids using the _get_item_from_db method."""
    from program.media.item import Episode, MediaItem, Movie, Season, Show
    items = []

    with db.Session() as session:
        for media_item_id in media_item_ids:
            item_type = session.execute(select(MediaItem.type).where(MediaItem.id==media_item_id)).scalar_one()
            if not item_type:
                continue
            item = None
            match item_type:
                case "movie":
                    item = session.execute(
                        select(Movie)
                        .where(MediaItem.id == media_item_id)
                    ).unique().scalar_one()
                case "show":
                    item = session.execute(
                        select(Show)
                        .where(MediaItem.id == media_item_id)
                        .options(joinedload(Show.seasons).joinedload(Season.episodes))
                    ).unique().scalar_one()
                case "season":
                    item = session.execute(
                        select(Season)
                        .where(Season.id == media_item_id)
                        .options(joinedload(Season.episodes))
                    ).unique().scalar_one()
                case "episode":
                    item = session.execute(
                        select(Episode)
                        .where(Episode.id == media_item_id)
                    ).unique().scalar_one()
            if item:
                items.append(item)

    return items

def get_parent_items_by_ids(media_item_ids: list[int]):
    """Retrieve multiple MediaItems of type 'movie' or 'show' by a list of MediaItem _ids."""
    from program.media.item import MediaItem
    with db.Session() as session:
        items = []
        for media_item_id in media_item_ids:
            item = session.execute(select(MediaItem).where(MediaItem.id == media_item_id, MediaItem.type.in_(["movie", "show"]))).unique().scalar_one_or_none()
            if item:
                items.append(item)
    return items

def get_item_by_imdb_id(imdb_id: str):
    """Retrieve a MediaItem of type 'movie' or 'show' by an IMDb ID."""
    from program.media.item import MediaItem
    with db.Session() as session:
        item = session.execute(select(MediaItem).where(MediaItem.ids["imdb_id"] == imdb_id, MediaItem.type.in_(["movie", "show"]))).unique().scalar_one_or_none()
    return item

def delete_media_item(item: "MediaItem"):
    """Delete a MediaItem and all its associated relationships."""
    with db.Session() as session:
        item = session.merge(item)
        session.delete(item)
        session.commit()

def delete_media_item_by_id(media_item_id: int):
    """Delete a MediaItem and all its associated relationships by the MediaItem _id."""
    from program.media.item import MediaItem
    with db.Session() as session:
        item = session.query(MediaItem).filter_by(_id=media_item_id).first()

        if item:
            session.delete(item)
            session.commit()
        else:
            raise ValueError(f"MediaItem with id {media_item_id} does not exist.")

def delete_media_item_by_item_id(item_id: str):
    """Delete a MediaItem and all its associated relationships by the MediaItem _id."""
    from program.media.item import MediaItem
    with db.Session() as session:
        item = session.query(MediaItem).filter_by(item_id=item_id).first()

        if item:
            session.delete(item)
            session.commit()
        else:
            raise ValueError(f"MediaItem with item_id {item_id} does not exist.")

def delete_media_items_by_ids(media_item_ids: list[int]):
    """Delete multiple MediaItems and all their associated relationships by a list of MediaItem _ids."""
    try:
        for media_item_id in media_item_ids:
            delete_media_item_by_id(media_item_id)
    except Exception as e:
        error = f"Failed to delete media items: {e}"
        logger.error(error)
        raise ValueError(error)

def reset_media_item(item: "MediaItem"):
    """Reset a MediaItem."""
    with db.Session() as session:
        item = session.merge(item)
        item.reset()
        session.commit()

def reset_streams(item: "MediaItem", active_stream_hash: str = None):
    """Reset streams associated with a MediaItem."""
    with db.Session() as session:
        item.store_state()
        item = session.merge(item)
        if active_stream_hash:
            stream = session.query(Stream).filter(Stream.infohash == active_stream_hash).first()
            if stream:
                blacklist_stream(item, stream, session)

        session.execute(
            delete(StreamRelation).where(StreamRelation.parent_id == item.id)
        )

        session.execute(
            delete(StreamBlacklistRelation).where(StreamBlacklistRelation.media_item_id == item.id)
        )
        item.active_stream = {}
        session.commit()

def clear_streams(item: "MediaItem"):
    """Clear all streams for a media item."""
    with db.Session() as session:
        item = session.merge(item)
        session.execute(
            delete(StreamRelation).where(StreamRelation.parent_id == item.id)
        )
        session.execute(
            delete(StreamBlacklistRelation).where(StreamBlacklistRelation.media_item_id == item.id)
        )
        session.commit()

def blacklist_stream(item: "MediaItem", stream: Stream, session: Session = None) -> bool:
    """Blacklist a stream for a media item."""
    close_session = False
    if session is None:
        session = db.Session()
        item = session.execute(select(type(item)).where(type(item).id == item.id)).unique().scalar_one()
        close_session = True

    try:
        item = session.merge(item)
        association_exists = session.query(
            session.query(StreamRelation)
            .filter(StreamRelation.parent_id == item.id)
            .filter(StreamRelation.child_id == stream.id)
            .exists()
        ).scalar()

        if association_exists:
            session.execute(
                delete(StreamRelation)
                .where(StreamRelation.parent_id == item.id)
                .where(StreamRelation.child_id == stream.id)
            )
            session.execute(
                insert(StreamBlacklistRelation)
                .values(media_item_id=item.id, stream_id=stream.id)
            )
            item.store_state()
            session.commit()
            return True
        return False
    # except Exception as e:
    #     if close_session:
    #         session.rollback()
    #     logger.log("DATABASE", f"Failed to blacklist stream {stream.infohash} for {item.log_string}: {e}")
    #     raise e
    finally:
        if close_session:
            session.close()

def filter_existing_streams(media_item_id: int, scraped_streams: List[Stream]) -> List[Stream]:
    from program.media.item import MediaItem
    """Return streams that are not already associated with the media item."""
    scraped_hashes = [stream.infohash for stream in scraped_streams]

    with db.Session() as session:
        existing_streams = session.execute(
            select(Stream.infohash)
            .join(Stream.parents)
            .where(MediaItem.id == media_item_id)
            .where(Stream.infohash.in_(scraped_hashes))
        ).scalars().all()
        existing_hashes = set(existing_streams)
        new_streams = [stream for stream in scraped_streams if stream.infohash not in existing_hashes]
        return new_streams

def get_stream_count(media_item_id: int) -> int:
    from program.media.item import MediaItem
    """Get the count of streams for a given MediaItem."""
    with db.Session() as session:
        return session.execute(
            select(func.count(Stream.id))
            .filter(Stream.parents.any(MediaItem.id == media_item_id))
        ).scalar_one()

def load_streams_in_pages(session: Session, media_item_id: int, page_number: int, page_size: int = 5):
    """Load a specific page of streams for a given MediaItem."""
    from program.media.item import MediaItem
    stream_query = session.query(Stream.id, Stream.infohash).filter(Stream.parents.any(MediaItem.id == media_item_id))
    stream_chunk = stream_query.limit(page_size).offset(page_number * page_size).all()

    for stream_id, infohash in stream_chunk:
        stream = session.query(Stream).get(stream_id)
        yield stream_id, infohash, stream

def _get_item_ids(session, item):
    from program.media.item import Episode, Season
    if item.type == "show":
        show_id = item.id

        season_alias = aliased(Season, flat=True)
        season_query = select(Season.id.label('id')).where(Season.parent_id == show_id)
        episode_query = (
            select(Episode.id.label('id'))
            .join(season_alias, Episode.parent_id == season_alias.id)
            .where(season_alias.parent_id == show_id)
        )

        combined_query = union_all(season_query, episode_query)
        related_ids = session.execute(combined_query).scalars().all()
        return show_id, related_ids

    elif item.type == "season":
        season_id = item.id
        episode_ids = session.execute(
            select(Episode.id)
            .where(Episode.parent_id == season_id)
        ).scalars().all()
        return season_id, episode_ids
    elif item.type == "episode":
        return item.id, []
    elif hasattr(item, "parent"):
        parent_id = item.parent.id
        return parent_id, []

    return item.id, []

def _ensure_item_exists_in_db(item: "MediaItem") -> bool:
    from program.media.item import MediaItem, Movie, Show
    if isinstance(item, (Movie, Show)):
        with db.Session() as session:
            if item.id is None:
                return session.execute(select(func.count(MediaItem.id)).where(cast(MediaItem.ids["imdb_id"].astext, sqlalchemy.String) == item.ids["imdb_id"])).scalar_one() != 0
            return session.execute(select(func.count(MediaItem.id)).where(MediaItem.id == item.id)).scalar_one() != 0
    return bool(item and item.id)

def _filter_existing_items(items: list["MediaItem"]) -> list["MediaItem"]:
    """Return a list of MediaItems that do not exist in the database."""
    from program.media.item import MediaItem
    with db.Session() as session:
        existing_items = set(
            session.execute(
                select(MediaItem.ids["imdb_id"])
                .where(MediaItem.ids["imdb_id"].in_([item.ids["imdb_id"] for item in items]))
            ).scalars().all()
        )
        return [item for item in items if item.ids["imdb_id"] not in existing_items]

def _get_item_type_from_db(item: "MediaItem") -> str:
    from program.media.item import MediaItem
    with db.Session() as session:
        if item.id is None:
            return session.execute(select(MediaItem.type).where((cast(MediaItem.ids["imdb_id"].astext, sqlalchemy.String)==item.ids["imdb_id"]) & (MediaItem.type.in_(["show", "movie"])))).scalar_one()
        return session.execute(select(MediaItem.type).where(MediaItem.id==item.id)).scalar_one()

def _store_item(item: "MediaItem"):
    from program.media.item import Episode, Movie, Season, Show
    if isinstance(item, (Movie, Show, Season, Episode)) and item.id is not None:
        with db.Session() as session:
            item.store_state()
            session.merge(item)
            session.commit()
            logger.log("DATABASE", f"{item.log_string} Updated!")
    else:
        with db.Session() as session:
            _check_for_and_run_insertion_required(session, item)

def _imdb_exists_in_db(imdb_id: str) -> bool:
    from program.media.item import MediaItem
    with db.Session() as session:
        return session.execute(select(func.count(MediaItem.id)).where(cast(MediaItem.ids['imdb_id'].astext, sqlalchemy.String) == imdb_id)).scalar_one() != 0

def _get_item_from_db(session, item: "MediaItem"):
    from program.media.item import Episode, MediaItem, Movie, Season, Show
    if not _ensure_item_exists_in_db(item):
        return None
    session.expire_on_commit = False
    type = _get_item_type_from_db(item)
    match type:
        case "movie":
            r = session.execute(
                select(Movie)
                .where(cast(MediaItem.ids['imdb_id'].astext, sqlalchemy.String) == item.ids["imdb_id"])
            ).unique().scalar_one()
            return r
        case "show":
            r = session.execute(
                select(Show)
                .where(cast(MediaItem.ids['imdb_id'].astext, sqlalchemy.String) == item.ids["imdb_id"])
                .options(joinedload(Show.seasons).joinedload(Season.episodes))
            ).unique().scalar_one()
            return r
        case "season":
            r = session.execute(
                select(Season)
                .where(Season.id == item.id)
                .options(joinedload(Season.episodes))
            ).unique().scalar_one()
            return r
        case "episode":
            r = session.execute(
                select(Episode)
                .where(Episode.id == item.id)
            ).unique().scalar_one()
            return r
        case _:
            logger.error(f"_get_item_from_db Failed to create item from type: {type}")
            return None

def _check_for_and_run_insertion_required(session, item: "MediaItem") -> bool:
    from program.media.item import Episode, Movie, Season, Show
    if not _ensure_item_exists_in_db(item) and isinstance(item, (Show, Movie, Season, Episode)):
            item.store_state()
            session.add(item)
            session.commit()
            logger.log("DATABASE", f"{item.log_string} Inserted into the database.")
            return True
    return False

def _run_thread_with_db_item(fn, service, program, input_item: "MediaItem" = None):
    from program.media.item import Episode, MediaItem, Movie, Season, Show
    if input_item:
        with db.Session() as session:
            if isinstance(input_item, (Movie, Show, Season, Episode)):
                if not _check_for_and_run_insertion_required(session, input_item):
                    pass
                input_item = _get_item_from_db(session, input_item)
                for res in fn(input_item):
                    if isinstance(res, tuple):
                        item, _ = res
                    else:
                        item = res
                    if not isinstance(item, MediaItem):
                        logger.log("PROGRAM", f"Service {service.__name__} emitted {item} from input item {input_item} of type {type(item).__name__}, backing off.")
                        program.em.remove_item_from_running(input_item)

                    input_item.store_state()
                    session.commit()

                    session.expunge_all()
                    yield res
            else:
                #Content services
                for i in fn(input_item):
                    if isinstance(i, (MediaItem)):
                        with db.Session() as session:
                            _check_for_and_run_insertion_required(session, i)
                    yield i
        return
    else:
        # Content services
        for i in fn():
            if isinstance(i, (MediaItem)):
                program.em.add_item(i, service)
            elif isinstance(i, list) and all(isinstance(item, MediaItem) for item in i):
                for item in i:
                    program.em.add_item(item, service)
        return

def hard_reset_database():
    """Resets the database to a fresh state."""
    from sqlalchemy import create_engine
    from sqlalchemy.engine.url import make_url

    logger.log("DATABASE", "Starting Hard Reset of Database")

    # Get the database connection string from settings
    db_url = settings_manager.settings.database.host
    url = make_url(db_url)

    # Create an engine for the default database to drop and recreate the target database
    default_db_url = url.set(database="postgres")
    default_engine = create_engine(default_db_url, isolation_level="AUTOCOMMIT")

    try:
        # Drop and recreate the database
        with default_engine.connect() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS {url.database}"))
            connection.execute(text(f"CREATE DATABASE {url.database}"))
            logger.log("DATABASE", f"Database '{url.database}' dropped and recreated")
    except sqlalchemy.exc.OperationalError as e:
        logger.error(f"OperationalError: {e}")
        exit(0)

    # Reconnect to the new database
    db.engine.dispose()
    db.engine = create_engine(db_url)

    # Disable foreign key checks temporarily
    with db.engine.connect() as connection:
        if db.engine.name == 'sqlite':
            connection.execute(text("PRAGMA foreign_keys = OFF"))
        elif db.engine.name == 'postgresql':
            connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))

        try:
            db.Model.metadata.create_all(connection)
            logger.log("DATABASE", "All tables recreated")

            # Re-enable foreign key checks
            if db.engine.name == 'sqlite':
                connection.execute(text("PRAGMA foreign_keys = ON"))
            elif db.engine.name == 'postgresql':
                connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

            connection.commit()
        except Exception as e:
            connection.rollback()
            logger.log("DATABASE", f"Error during database reset: {str(e)}")
            raise

    try:
        logger.log("DATABASE", "Removing Alembic Directory")
        shutil.rmtree(script_location, ignore_errors=True)
        os.makedirs(script_location, exist_ok=True)
        alembic.init(script_location)
        ensure_alembic_version_table()
        logger.log("DATABASE", "Alembic reinitialized")
    except Exception as e:
        logger.log("DATABASE", f"Error reinitializing Alembic: {str(e)}")

    logger.log("DATABASE", "Hard Reset Complete")


# Hard Reset Database
reset = os.getenv("HARD_RESET", None)
if reset is not None and reset.lower() in ["true","1"]:
    hard_reset_database()
    exit(0)

# Repair Symlinks
if os.getenv("REPAIR_SYMLINKS", None) is not None and os.getenv("REPAIR_SYMLINKS").lower() in ["true","1"]:
    fix_broken_symlinks(settings_manager.settings.symlink.library_path, settings_manager.settings.symlink.rclone_path)
    exit(0)