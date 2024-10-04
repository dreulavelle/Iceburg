"""Trakt content module"""
import re
import time
from typing import Union
from urllib.parse import urlencode

from requests import RequestException

from program.db.db_functions import _filter_existing_items
from program.media.item import MediaItem
from program.settings.manager import settings_manager
from utils.logger import logger
from utils.ratelimiter import RateLimiter
from utils.request import get, post


class TraktContent:
    """Content class for Trakt"""

    def __init__(self):
        self.key = "trakt"
        self.api_url = "https://api.trakt.tv"
        self.settings = settings_manager.settings.content.trakt
        self.headers = {
            "Content-type": "application/json",
            "trakt-api-key": self.settings.api_key,
            "trakt-api-version": "2"
        }
        self.initialized = self.validate()
        if not self.initialized:
            return
        self.next_run_time = 0
        self.recurring_items = set()
        self.items_already_seen = set()
        self.missing()
        logger.success("Trakt initialized!")

    def validate(self) -> bool:
        """Validate Trakt settings."""
        try:
            if not self.settings.enabled:
                return False
            if not self.settings.api_key:
                logger.error("Trakt API key is not set.")
                return False
            response = get(f"{self.api_url}/lists/2", additional_headers=self.headers)
            if not getattr(response.data, "name", None):
                logger.error("Invalid user settings received from Trakt.")
                return False
            return True
        except ConnectionError:
            logger.error("Connection error during Trakt validation.")
            return False
        except TimeoutError:
            logger.error("Timeout error during Trakt validation.")
            return False
        except RequestException as e:
            logger.error(f"Request exception during Trakt validation: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Exception during Trakt validation: {str(e)}")
            return False

    def missing(self):
        """Log missing items from Trakt"""
        if not self.settings.watchlist:
            logger.log("TRAKT", "No watchlist configured.")
        if not self.settings.collection:
            logger.log("TRAKT", "No collection configured.")
        if not self.settings.user_lists:
            logger.log("TRAKT", "No user lists configured.")
        if not self.settings.fetch_trending:
            logger.log("TRAKT", "Trending fetching is disabled.")
        if not self.settings.fetch_popular:
            logger.log("TRAKT", "Popular fetching is disabled.")

    def run(self):
        """Fetch media from Trakt and yield Movie, Show, or MediaItem instances."""

        watchlist_ids = self._get_watchlist(self.settings.watchlist) if self.settings.watchlist else []
        collection_ids = self._get_collection(self.settings.collection) if self.settings.collection else []
        user_list_ids = self._get_list(self.settings.user_lists) if self.settings.user_lists else []
        trending_ids = self._get_trending_items() if self.settings.fetch_trending else []
        popular_ids = self._get_popular_items() if self.settings.fetch_popular else []

        # Combine all IMDb IDs and types into a set to avoid duplicates
        all_ids = set(watchlist_ids + collection_ids + user_list_ids + trending_ids + popular_ids)

        items_to_yield = []
        for imdb_id, _ in all_ids:
            items_to_yield.append(MediaItem({"imdb_id": imdb_id, "requested_by": self.key}))

        if not items_to_yield:
            return

        non_existing_items = _filter_existing_items(items_to_yield)
        new_non_recurring_items = [
            item
            for item in non_existing_items
            if item.ids["imdb_id"] not in self.recurring_items
            and isinstance(item, MediaItem)
        ]
        self.recurring_items.update(item.ids["imdb_id"] for item in new_non_recurring_items)

        if new_non_recurring_items:
            logger.log("TRAKT", f"Found {len(new_non_recurring_items)} new items to fetch")

        yield new_non_recurring_items

    def _get_watchlist(self, watchlist_users: list) -> list:
        """Get IMDb IDs from Trakt watchlist"""
        if not watchlist_users:
            return []
        imdb_ids = []
        for user in watchlist_users:
            items = get_watchlist_items(self.api_url, self.headers, user)
            imdb_ids.extend(self._extract_imdb_ids(items))
        return imdb_ids

    def _get_collection(self, collection_users: list) -> list:
        """Get IMDb IDs from Trakt collection"""
        if not collection_users:
            return []
        imdb_ids = []
        for user in collection_users:
            items = get_collection_items(self.api_url, self.headers, user, "movies")
            items.extend(get_collection_items(self.api_url, self.headers, user, "shows"))
            imdb_ids.extend(self._extract_imdb_ids(items))
        return imdb_ids


    def _get_list(self, list_items: list) -> list:
        """Get IMDb IDs from Trakt user list"""
        if not list_items or not any(list_items):
            return []
        imdb_ids = []
        for url in list_items:
            user, list_name = _extract_user_list_from_url(url)
            if not user or not list_name:
                logger.error(f"Invalid list URL: {url}")
                continue

            items = get_user_list(self.api_url, self.headers, user, list_name)
            for item in items:
                if hasattr(item, "movie"):
                    imdb_id = getattr(item.movie.ids, "imdb", None)
                    if imdb_id:
                        imdb_ids.append((imdb_id, "movie"))
                elif hasattr(item, "show"):
                    imdb_id = getattr(item.show.ids, "imdb", None)
                    if imdb_id:
                        imdb_ids.append((imdb_id, "show"))
        return imdb_ids

    def _get_trending_items(self) -> list:
        """Get IMDb IDs from Trakt trending items"""
        trending_movies = get_trending_items(self.api_url, self.headers, "movies", self.settings.trending_count)
        trending_shows = get_trending_items(self.api_url, self.headers, "shows", self.settings.trending_count)
        return self._extract_imdb_ids(trending_movies[:self.settings.trending_count] + trending_shows[:self.settings.trending_count])

    def _get_popular_items(self) -> list:
        """Get IMDb IDs from Trakt popular items"""
        popular_movies = get_popular_items(self.api_url, self.headers, "movies", self.settings.popular_count)
        popular_shows = get_popular_items(self.api_url, self.headers, "shows", self.settings.popular_count)
        return self._extract_imdb_ids_with_none_type(popular_movies[:self.settings.popular_count] + popular_shows[:self.settings.popular_count])

    def _extract_imdb_ids(self, items: list) -> list:
        """Extract IMDb IDs and types from a list of items"""
        imdb_ids = []
        for item in items:
            if hasattr(item, "show"):
                ids = getattr(item.show, "ids", None)
                if ids:
                    imdb_id = getattr(ids, "imdb", None)
                    if imdb_id:
                        imdb_ids.append((imdb_id, "show"))
            elif hasattr(item, "movie"):
                ids = getattr(item.movie, "ids", None)
                if ids:
                    imdb_id = getattr(ids, "imdb", None)
                    if imdb_id:
                        imdb_ids.append((imdb_id, "movie"))
        return imdb_ids

    def _extract_imdb_ids_with_none_type(self, items: list) -> list:
        """Extract IMDb IDs from a list of items, returning None for type"""
        imdb_ids = []
        for item in items:
            ids = getattr(item, "ids", None)
            if ids:
                imdb_id = getattr(ids, "imdb", None)
                if imdb_id:
                    imdb_ids.append((imdb_id, None))
        return imdb_ids

    def perform_oauth_flow(self) -> str:
        """Initiate the OAuth flow and return the authorization URL."""
        params = {
            "response_type": "code",
            "client_id": self.settings.oauth_client_id,
            "redirect_uri": self.settings.oauth_redirect_uri,
        }
        return f"{self.api_url}/oauth/authorize?{urlencode(params)}"

    def handle_oauth_callback(self, code: str) -> bool:
        """Handle the OAuth callback and exchange the code for an access token."""
        token_url = f"{self.api_url}/oauth/token"
        payload = {
            "code": code,
            "client_id": self.settings.oauth_client_id,
            "client_secret": self.settings.oauth_client_secret,
            "redirect_uri": self.settings.oauth_redirect_uri,
            "grant_type": "authorization_code",
        }
        response = post(token_url, data=payload, additional_headers=self.headers)
        if response.is_ok:
            token_data = response.data
            self.settings.access_token = token_data.get("access_token")
            self.settings.refresh_token = token_data.get("refresh_token")
            settings_manager.save()  # Save the tokens to settings
            return True
        else:
            logger.error(f"Failed to obtain OAuth token: {response.status_code}")
            return False

## API functions for Trakt

rate_limiter = RateLimiter(max_calls=1000, period=300)

def _fetch_data(url, headers, params):
    """Fetch paginated data from Trakt API with rate limiting."""
    all_data = []
    page = 1

    while True:
        try:
            with rate_limiter:
                response = get(url, params={**params, "page": page}, additional_headers=headers)
            if response.is_ok:
                data = response.data
                if not data:
                    break
                all_data.extend(data)
                if "X-Pagination-Page-Count" not in response.response.headers:
                    break
                if params.get("limit") and len(all_data) >= params["limit"]:
                    break
                page += 1
            elif response.status_code == 429:
                logger.warning("Rate limit exceeded. Retrying after rate limit period.")
                rate_limiter.limit_hit()
            else:
                logger.error(f"Failed to fetch data: {response.status_code}")
                break
        except Exception as e:
            logger.error(f"Error fetching data: {str(e)}")
            break
    return all_data

def get_watchlist_items(api_url, headers, user):
    """Get watchlist items from Trakt with pagination support."""
    url = f"{api_url}/users/{user}/watchlist"
    return _fetch_data(url, headers, {})

def get_user_list(api_url, headers, user, list_name):
    """Get user list items from Trakt with pagination support."""
    url = f"{api_url}/users/{user}/lists/{list_name}/items"
    return _fetch_data(url, headers, {})

def get_collection_items(api_url, headers, user, media_type):
    """Get collections from Trakt with pagination support."""
    url = f"{api_url}/users/{user}/collection/{media_type}"
    return _fetch_data(url, headers, {})

# UNUSED
def get_liked_lists(api_url, headers):
    """Get liked lists from Trakt with pagination support."""
    url = f"{api_url}/users/likes/lists"
    return _fetch_data(url, headers, {})

def get_trending_items(api_url, headers, media_type, limit=10):
    """Get trending items from Trakt with pagination support."""
    url = f"{api_url}/{media_type}/trending"
    return _fetch_data(url, headers, {"limit": limit})

def get_popular_items(api_url, headers, media_type, limit=10):
    """Get popular items from Trakt with pagination support."""
    url = f"{api_url}/{media_type}/popular"
    return _fetch_data(url, headers, {"limit": limit})

# UNUSED
def get_favorited_items(api_url, headers, user, limit=10):
    """Get favorited items from Trakt with pagination support."""
    url = f"{api_url}/users/{user}/favorites"
    return _fetch_data(url, headers, {"limit": limit})


def _extract_user_list_from_url(url) -> tuple:
    """Extract user and list name from Trakt URL"""

    def match_full_url(url: str) -> tuple:
        """Helper function to match full URL format"""
        match = patterns["user_list"].match(url)
        if match:
            return match.groups()
        return None, None

    # First try to match the original URL
    user, list_name = match_full_url(url)
    if user and list_name:
        return user, list_name

    # If it's a short URL, resolve it and try to match again
    match = patterns["short_list"].match(url)
    if match:
        full_url = _resolve_short_url(url)
        if full_url:
            user, list_name = match_full_url(full_url)
            if user and list_name:
                return user, list_name

    return None, None

def _resolve_short_url(short_url) -> Union[str, None]:
    """Resolve short URL to full URL"""
    try:
        response = get(short_url, additional_headers={"Content-Type": "application/json", "Accept": "text/html"})
        if response.is_ok:
            return response.response.url
        else:
            logger.error(f"Failed to resolve short URL: {short_url} (with status code: {response.status_code})")
            return None
    except RequestException as e:
        logger.error(f"Error resolving short URL: {str(e)}")
        return None

patterns: dict[str, re.Pattern] = {
    "user_list": re.compile(r"https://trakt.tv/users/([^/]+)/lists/([^/]+)"),
    "short_list": re.compile(r"https://trakt.tv/lists/\d+")
}