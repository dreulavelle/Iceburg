"""Overseerr content module"""

from typing import Union

from requests.exceptions import ConnectionError, RetryError
from urllib3.exceptions import MaxRetryError, NewConnectionError

from program.db.db_functions import _filter_existing_items
from program.indexers.trakt import get_imdbid_from_tmdb
from program.media.item import MediaItem
from program.settings.manager import settings_manager
from utils.logger import logger
from utils.request import delete, get, ping, post


class Overseerr:
    """Content class for overseerr"""

    def __init__(self):
        self.key = "overseerr"
        self.settings = settings_manager.settings.content.overseerr
        self.headers = {"X-Api-Key": self.settings.api_key}
        self.initialized = self.validate()
        self.run_once = False
        if not self.initialized:
            return
        self.recurring_items: set[str] = set()
        logger.success("Overseerr initialized!")

    def validate(self) -> bool:
        if not self.settings.enabled:
            return False
        if self.settings.api_key == "" or len(self.settings.api_key) != 68:
            logger.error("Overseerr api key is not set.")
            return False
        try:
            response = ping(
                self.settings.url + "/api/v1/auth/me",
                additional_headers=self.headers,
                timeout=30,
            )
            if response.status_code >= 201:
                logger.error(
                    f"Overseerr ping failed - Status Code: {response.status_code}, Reason: {response.response.reason}"
                )
                return False
            return response.is_ok
        except (ConnectionError, RetryError, MaxRetryError, NewConnectionError):
            logger.error("Overseerr URL is not reachable, or it timed out")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during Overseerr validation: {str(e)}")
            return False

    def run(self):
        """Fetch new media from `Overseerr`"""
        if self.settings.use_webhook and self.run_once:
            return

        overseerr_items: list[MediaItem] = self.get_media_requests()
        non_existing_items = _filter_existing_items(overseerr_items)
        new_non_recurring_items = [item for item in non_existing_items if item.ids["imdb_id"] not in self.recurring_items and isinstance(item, MediaItem)]
        self.recurring_items.update([item.ids["imdb_id"] for item in new_non_recurring_items])

        if self.settings.use_webhook:
            logger.debug("Webhook is enabled. Running Overseerr once before switching to webhook only mode")
            self.run_once = True

        if new_non_recurring_items:
            logger.info(f"Fetched {len(new_non_recurring_items)} new items from Overseerr")

        yield new_non_recurring_items

    def get_media_requests(self) -> list[MediaItem]:
        """Get media requests from `Overseerr`"""
        try:
            response = get(self.settings.url + f"/api/v1/request?take={10000}&filter=approved&sort=added", additional_headers=self.headers)
            if not response.is_ok:
                logger.error(f"Failed to fetch requests from overseerr: {response.data}")
                return []
        except (ConnectionError, RetryError, MaxRetryError) as e:
            logger.error(f"Failed to fetch requests from overseerr: {str(e)}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error during fetching requests: {str(e)}")
            return []

        if not hasattr(response.data, "pageInfo") or getattr(response.data.pageInfo, "results", 0) == 0:
            return []

        # Lets look at approved items only that are only in the pending state
        pending_items = [
            item for item in response.data.results
            if item.status == 2 and item.media.status == 3
        ]

        return [
            MediaItem({
                "imdb_id": self.get_imdb_id(item.media),
                "requested_by": self.key,
                "overseerr_id": item.media.id,
                "requested_id": item.id
            })
            for item in pending_items
        ]


    def get_imdb_id(self, data) -> str:
        """Get imdbId for item from overseerr"""
        if data.mediaType == "show":
            external_id = data.tvdbId
            data.mediaType = "tv"
        else:
            external_id = data.tmdbId

        try:
            response = get(
                self.settings.url + f"/api/v1/{data.mediaType}/{external_id}?language=en",
                additional_headers=self.headers,
            )
        except (ConnectionError, RetryError, MaxRetryError) as e:
            logger.error(f"Failed to fetch media details from overseerr: {str(e)}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error during fetching media details: {str(e)}")
            return None

        if not response.is_ok or not hasattr(response.data, "externalIds"):
            return None

        imdb_id = getattr(response.data.externalIds, "imdbId", None)
        if imdb_id:
            return imdb_id

        # Try alternate IDs if IMDb ID is not available
        alternate_ids = [("tmdbId", get_imdbid_from_tmdb)]
        for id_attr, fetcher in alternate_ids:
            external_id_value = getattr(response.data.externalIds, id_attr, None)
            if external_id_value:
                _type = data.media_type
                if _type == "tv":
                    _type = "show"
                try:
                    new_imdb_id: Union[str, None] = fetcher(external_id_value, type=_type)
                    if not new_imdb_id:
                        continue
                    return new_imdb_id
                except Exception as e:
                    logger.error(f"Error fetching alternate ID: {str(e)}")
                    continue

    @staticmethod
    def delete_request(mediaId: int) -> bool:
        """Delete request from `Overseerr`"""
        settings = settings_manager.settings.content.overseerr
        headers = {"X-Api-Key": settings.api_key}
        try:
            response = delete(
                settings.url + f"/api/v1/request/{mediaId}",
                additional_headers=headers,
            )
            logger.debug(f"Deleted request {mediaId} from overseerr")
            return response.is_ok == True
        except Exception as e:
            logger.error(f"Failed to delete request from overseerr: {str(e)}")
            return False

    @staticmethod
    def mark_processing(mediaId: int) -> bool:
        """Mark item as processing in overseerr"""
        settings = settings_manager.settings.content.overseerr
        headers = {"X-Api-Key": settings.api_key}
        try:
            response = post(
                settings.url + f"/api/v1/media/{mediaId}/pending",
                additional_headers=headers,
                data={"is4k": False},
            )
            logger.info(f"Marked media {mediaId} as processing in overseerr")
            return response.is_ok
        except Exception as e:
            logger.error(f"Failed to mark media as processing in overseerr with id {mediaId}: {str(e)}")
            return False

    @staticmethod
    def mark_partially_available(mediaId: int) -> bool:
        """Mark item as partially available in overseerr"""
        settings = settings_manager.settings.content.overseerr
        headers = {"X-Api-Key": settings.api_key}
        try:
            response = post(
                settings.url + f"/api/v1/media/{mediaId}/partial",
                additional_headers=headers,
                data={"is4k": False},
            )
            logger.info(f"Marked media {mediaId} as partially available in overseerr")
            return response.is_ok
        except Exception as e:
            logger.error(f"Failed to mark media as partially available in overseerr with id {mediaId}: {str(e)}")
            return False

    @staticmethod
    def mark_completed(mediaId: int) -> bool:
        """Mark item as completed in overseerr"""
        settings = settings_manager.settings.content.overseerr
        headers = {"X-Api-Key": settings.api_key}
        try:
            response = post(
                settings.url + f"/api/v1/media/{mediaId}/available",
                additional_headers=headers,
                data={"is4k": False},
            )
            logger.info(f"Marked media {mediaId} as completed in overseerr")
            return response.is_ok
        except Exception as e:
            logger.error(f"Failed to mark media as completed in overseerr with id {mediaId}: {str(e)}")
            return False


# Statuses for Media Requests endpoint /api/v1/request:
# item.status:
# 1 = PENDING APPROVAL, 
# 2 = APPROVED, 
# 3 = DECLINED

# Statuses for Media Info endpoint /api/v1/media:
# item.media.status:
# 1 = UNKNOWN, 
# 2 = PENDING, 
# 3 = PROCESSING, 
# 4 = PARTIALLY_AVAILABLE, 
# 5 = AVAILABLE