"""Trakt content module"""
import time
from types import SimpleNamespace
from urllib.parse import urlencode, urlparse

import regex
from program.media.item import MediaItem, Movie, Show
from program.settings.manager import settings_manager
from requests import RequestException
from utils.logger import logger
from utils.request import RateLimiter, get, post


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
        self.items_already_seen = set()
        self.missing()
        logger.success("Trakt initialized!")

    def validate(self) -> bool:
        """Validate Trakt settings."""
        try:
            if not self.settings.enabled:
                logger.warning("Trakt is set to disabled.")
                return False
            if not self.settings.api_key:
                logger.error("Trakt API key is not set.")
                return False
            response = get(f"{self.api_url}/lists/2", additional_headers=self.headers)
            if not getattr(response.data, 'name', None):
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
        current_time = time.time()
        if current_time < self.next_run_time:
            return

        self.next_run_time = current_time + self.settings.update_interval
        watchlist_ids = self._get_watchlist(self.settings.watchlist)
        collection_ids = self._get_collection(self.settings.collection)
        user_list_ids = self._get_list(self.settings.user_lists)
        trending_ids = self._get_trending_items() if self.settings.fetch_trending else []
        popular_ids = self._get_popular_items() if self.settings.fetch_popular else []

        # Combine all IMDb IDs and types
        all_items = {
            "Watchlist": watchlist_ids,
            "Collection": collection_ids,
            "User Lists": user_list_ids,
            "Trending": trending_ids,
            "Popular": popular_ids
        }

        total_new_items = 0

        for source, items in all_items.items():
            new_items_count = 0
            for imdb_id, item_type in items:
                if imdb_id in self.items_already_seen or not imdb_id:
                    continue
                self.items_already_seen.add(imdb_id)
                new_items_count += 1

                if source == "Popular":
                    media_item = MediaItem({
                        "imdb_id": imdb_id,
                        "requested_by": self.key
                    })
                elif item_type == "movie":
                    media_item = Movie({
                        "imdb_id": imdb_id,
                        "requested_by": self.key
                    })
                else:
                    media_item = Show({
                        "imdb_id": imdb_id,
                        "requested_by": self.key
                    })

                yield media_item

            if new_items_count > 0:
                logger.log("TRAKT", f"New items fetched from {source}: {new_items_count}")
            total_new_items += new_items_count
        if total_new_items > 0:
            logger.log("TRAKT", f"Total new items fetched: {total_new_items}")

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
            match = regex.match(r'https://trakt.tv/users/([^/]+)/lists/([^/]+)', url)
            if not match:
                logger.error(f"Invalid list URL: {url}")
                continue
            user, list_name = match.groups()
            list_name = urlparse(url).path.split('/')[-1]
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
        return self._extract_imdb_ids(trending_movies + trending_shows)

    def _get_popular_items(self) -> list:
        """Get IMDb IDs from Trakt popular items"""
        popular_movies = get_popular_items(self.api_url, self.headers, "movies", self.settings.popular_count)
        popular_shows = get_popular_items(self.api_url, self.headers, "shows", self.settings.popular_count)
        return self._extract_imdb_ids_with_none_type(popular_movies + popular_shows)

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
        auth_url = f"{self.api_url}/oauth/authorize?{urlencode(params)}"
        return auth_url

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
                if not params.get("limit") or len(data) <= params["limit"]:
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