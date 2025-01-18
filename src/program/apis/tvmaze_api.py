"""TVMaze API client for fetching show information."""
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from loguru import logger
from program.utils.request import (
    BaseRequestHandler,
    HttpMethod,
    ResponseType,
    create_service_session,
    get_cache_params,
    get_rate_limit_params,
)
from requests.exceptions import HTTPError

class TVMazeAPI:
    """Handles TVMaze API communication."""
    
    BASE_URL = "https://api.tvmaze.com"
    
    def __init__(self):
        rate_limit_params = get_rate_limit_params(max_calls=20, period=10)  # TVMaze allows 20 requests per 10 seconds
        tvmaze_cache = get_cache_params("tvmaze", 86400)  # Cache for 24 hours
        session = create_service_session(rate_limit_params=rate_limit_params, use_cache=True, cache_params=tvmaze_cache)
        self.request_handler = BaseRequestHandler(session, response_type=ResponseType.SIMPLE_NAMESPACE)
    
    def get_show_by_imdb(self, imdb_id: str, show_name: Optional[str] = None, season_number: Optional[int] = None, episode_number: Optional[int] = None) -> Optional[datetime]:
        """Get show information from TVMaze using IMDB ID.
        
        Args:
            imdb_id: IMDB ID of the show or episode (with or without 'tt' prefix)
            show_name: Optional show name to use for search if IMDB lookup fails
            season_number: Optional season number to find specific episode
            episode_number: Optional episode number to find specific episode
            
        Returns:
            Next episode airtime in local time if available, None otherwise
        """
        try:
            # Add 'tt' prefix if not present
            if not imdb_id.startswith('tt'):
                imdb_id = f'tt{imdb_id}'
            
            show = None
            
            # Try singlesearch by show name first if provided, since episode IDs won't work with lookup
            if show_name:
                logger.debug(f"Trying singlesearch by name: {show_name}")
                try:
                    response = self.request_handler._request(HttpMethod.GET, f"{self.BASE_URL}/singlesearch/shows", params={'q': show_name})
                    show = response.data if response.is_ok else None
                except HTTPError as e:
                    if e.response.status_code == 404:
                        show = None
                    else:
                        raise
            
            # If show name search fails or wasn't provided, try direct lookup
            # This will only work for show-level IMDB IDs, not episode IDs
            if not show:
                try:
                    response = self.request_handler._request(HttpMethod.GET, f"{self.BASE_URL}/lookup/shows", params={'imdb': imdb_id})
                    show = response.data if response.is_ok else None
                except HTTPError as e:
                    if e.response.status_code == 404:
                        show = None
                    else:
                        raise
            
            # If that fails too, try regular search
            if not show and show_name:
                logger.debug(f"Singlesearch failed for {show_name}, trying regular search")
                try:
                    response = self.request_handler._request(HttpMethod.GET, f"{self.BASE_URL}/search/shows", params={'q': show_name})
                    if response.is_ok and response.data:
                        # Take the first result with highest score
                        show = response.data[0].show if response.data else None
                except HTTPError as e:
                    if e.response.status_code == 404:
                        show = None
                    else:
                        raise
            
            if not show:
                logger.debug(f"Could not find show for {imdb_id} / {show_name}")
                return None
            
            # Get next episode
            try:
                response = self.request_handler._request(HttpMethod.GET, f"{self.BASE_URL}/shows/{show.id}/episodes")
                episodes = response.data if response.is_ok else None
            except HTTPError as e:
                if e.response.status_code == 404:
                    episodes = None
                else:
                    raise
            
            if not episodes:
                return None
            
            # Find all unreleased episodes and the next episode
            current_time = datetime.now().astimezone()  # Make sure current_time has timezone info
            unreleased_episodes = []
            next_episode = None
            target_episode_time = None
            
            for episode in sorted(episodes, key=lambda x: (getattr(x, 'season', 0), getattr(x, 'number', 0))):
                try:
                    if not episode.airstamp:
                        continue
                        
                    # First try to get air time using network timezone
                    air_time = None
                    if (hasattr(show, 'network') and show.network and 
                        hasattr(show.network, 'country') and show.network.country and 
                        hasattr(show.network.country, 'timezone') and show.network.country.timezone and
                        episode.airdate and episode.airtime):
                        
                        # Combine airdate and airtime in network timezone
                        network_tz = ZoneInfo(show.network.country.timezone)
                        air_datetime = f"{episode.airdate}T{episode.airtime}"
                        try:
                            # Parse the time in network timezone
                            air_time = datetime.fromisoformat(air_datetime).replace(tzinfo=network_tz)
                            # Only log network time for the target episode
                            if (season_number is not None and episode_number is not None and
                                hasattr(episode, 'number') and hasattr(episode, 'season') and
                                episode.season == season_number and episode.number == episode_number):
                                logger.debug(f"Network airs show at {air_time} ({show.network.country.timezone})")
                        except Exception as e:
                            logger.error(f"Failed to parse network air time: {e}")
                            air_time = None
                    
                    # Fallback to airstamp if needed
                    if not air_time and episode.airstamp:
                        try:
                            air_time = datetime.fromisoformat(episode.airstamp.replace('Z', '+00:00'))
                            if (season_number is not None and episode_number is not None and
                                hasattr(episode, 'number') and hasattr(episode, 'season') and
                                episode.season == season_number and episode.number == episode_number):
                                logger.debug(f"Using UTC airstamp: {air_time}")
                        except Exception as e:
                            logger.error(f"Failed to parse airstamp: {e}")
                            continue
                    
                    if not air_time:
                        continue

                    # Convert to local time
                    air_time = air_time.astimezone(current_time.tzinfo)
                    
                    # Check if this is the specific episode we want
                    if season_number is not None and episode_number is not None:
                        if (hasattr(episode, 'number') and hasattr(episode, 'season') and
                            episode.season == season_number and episode.number == episode_number):
                                # Found our target episode
                                if hasattr(episode, 'name'):
                                    logger.debug(f"Found S{season_number}E{episode_number} '{episode.name}' airing at {air_time}")
                                else:
                                    logger.debug(f"Found S{season_number}E{episode_number} airing at {air_time}")
                                target_episode_time = air_time
                    
                    # Add all unreleased episodes to our list
                    if air_time > current_time:
                        ep_info = {
                            'air_time': air_time,
                            'season': getattr(episode, 'season', 0),
                            'episode': getattr(episode, 'number', 0),
                            'name': getattr(episode, 'name', '')
                        }
                        unreleased_episodes.append(ep_info)
                        # Track next episode separately
                        if not next_episode or air_time < next_episode:
                            next_episode = air_time
                
                except Exception as e:
                    logger.error(f"Failed to process episode {getattr(episode, 'number', '?')}: {e}")
                    continue
            
            # Return target episode time if we found one
            if target_episode_time is not None:
                return target_episode_time
            
            # Log all unreleased episodes in sequence
            if unreleased_episodes:
                unreleased_episodes.sort(key=lambda x: (x['season'], x['episode']))
                for ep in unreleased_episodes:
                    logger.debug(f"Unreleased: S{ep['season']}E{ep['episode']} '{ep['name']}' airs at {ep['air_time']}")
            
            # Return next episode air time
            if next_episode:
                logger.debug(f"Next episode airs at {next_episode}")
            return next_episode
            
        except Exception as e:
            logger.error(f"Error fetching TVMaze data for {imdb_id}: {e}")
            return None
