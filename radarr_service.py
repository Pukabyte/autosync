from typing import Any, Dict, List
from utils import http_get, http_post, get_config, parse_time_string, rewrite_path
from models import RadarrInstance
import logging
import asyncio
from media_server_service import MediaServerScanner
import aiohttp

# Create module logger
logger = logging.getLogger(__name__)


# ------------------------------------------------------------------------------
# Radarr-Specific Functions
# ------------------------------------------------------------------------------
def get_movie_by_tmdbid(
    base_url: str, api_key: str, tmdb_id: int
) -> List[Dict[str, Any]]:
    """
    GET /api/v3/movie?tmdbId=<tmdb_id>
    Returns an array of movies (possibly empty).
    """
    url = f"{base_url}/api/v3/movie?tmdbId={tmdb_id}"
    return http_get(url, api_key)


def add_movie(
    base_url: str,
    api_key: str,
    tmdb_id: int,
    title: str,
    year: int,
    root_folder_path: str,
    quality_profile_id: int,
) -> Dict[str, Any]:
    """
    POST /api/v3/movie to add a new movie. We'll set monitored=true
    but won't automatically search unless we do it separately.
    """
    url = f"{base_url}/api/v3/movie"
    payload = {
        "tmdbId": tmdb_id,
        "title": title,
        "year": year,
        "qualityProfileId": quality_profile_id,
        "titleSlug": title.replace(" ", "-").lower(),
        "rootFolderPath": root_folder_path,
        "monitored": True,
        "addOptions": {"searchForMovie": False},
    }
    return http_post(url, api_key, payload)


def search_movie(base_url: str, api_key: str, movie_id: int) -> Dict[str, Any]:
    """
    POST /api/v3/command with name=MoviesSearch, movieIds=[<id>]
    """
    url = f"{base_url}/api/v3/command"
    payload = {"name": "MoviesSearch", "movieIds": [movie_id]}
    return http_post(url, api_key, payload)


# ------------------------------------------------------------------------------
# Handle Radarr "On Grab"
# ------------------------------------------------------------------------------
async def handle_radarr_grab(
    payload: Dict[str, Any], instances: List[RadarrInstance]
) -> Dict[str, Any]:
    """Handle Radarr grab webhook with validated instances."""
    try:
        # Check event type
        event_type = payload.get("eventType", "")
        if event_type != "Grab":
            logger.debug(f"Ignoring non-Grab event: {event_type}")
            return {"status": "ignored", "reason": f"Radarr event is {event_type}"}

        # Extract movie data
        movie_data = payload["movie"]
        tmdb_id = movie_data.get("tmdbId")
        title = movie_data.get("title", "Unknown")
        year = movie_data.get("year")

        logger.info(
            "Processing Radarr Grab: Title=%s, TMDB=%s, Year=%s", title, tmdb_id, year
        )

        if not instances:
            logger.warning("No Radarr instances provided")
            return {"status": "error", "reason": "No Radarr instances configured"}

        logger.debug(f"Processing {len(instances)} Radarr instances")

        results = []
        for inst in instances:
            try:
                logger.debug(f"Processing Radarr instance: {inst.name}")

                # Check if movie exists
                existing = get_movie_by_tmdbid(inst.url, inst.api_key, tmdb_id)

                if not existing:
                    logger.info(f"Movie not found in {inst.name}, adding new movie")
                    # Add movie
                    added = add_movie(
                        inst.url,
                        inst.api_key,
                        tmdb_id,
                        title,
                        year,
                        inst.root_folder_path,
                        inst.quality_profile_id,
                    )
                    movie_id = added["id"]
                    logger.info(f"Added new movie (id={movie_id}) to {inst.name}")

                    # Search if configured
                    if inst.search_on_sync:
                        search_movie(inst.url, inst.api_key, movie_id)
                        logger.info(
                            f"Triggered search for movieId={movie_id} on {inst.name}"
                        )

                    results.append({"instance": inst.name, "addedMovieId": movie_id})
                else:
                    movie_id = existing[0]["id"]
                    logger.debug(f"Movie already exists (id=\033[1m{movie_id}\033[0m) on \033[1m{inst.name}\033[0m")
                    results.append({"instance": inst.name, "existingMovieId": movie_id})

            except Exception as e:
                logger.error(f"Error processing instance \033[1m{inst.name}\033[0m: \033[1m{str(e)}\033[0m")
                results.append({"instance": inst.name, "error": str(e)})

        response = {
            "status": "ok",
            "radarrTitle": title,
            "tmdbId": tmdb_id,
            "results": results,
        }
        logger.debug(f"Completed processing with response: \033[1m{response}\033[0m")
        return response

    except Exception as e:
        logger.error(f"Error processing webhook: \033[1m{str(e)}\033[0m")
        return {"status": "error", "error": str(e)}


async def handle_radarr_import(payload: Dict[str, Any], instances: List[RadarrInstance]) -> Dict[str, Any]:
    """Handle movie import by syncing across instances and scanning media servers"""
    movie_id = payload.get("movie", {}).get("tmdbId")
    path = payload.get("movie", {}).get("folderPath") or payload.get("movie", {}).get("path")
    
    results = {
        "status": "ok",
        "event": "Import",
        "imports": [],
        "scanResults": []
    }
    
    # Get sync interval from config
    config = get_config()
    sync_interval = parse_time_string(config.get("sync_interval", "0"))
    
    # Sync import across instances
    for i, instance in enumerate(instances):
        try:
            # Apply sync interval between instances (but not before the first one)
            if i > 0 and sync_interval > 0:
                logger.info(f"Waiting {sync_interval} seconds before processing next instance")
                await asyncio.sleep(sync_interval)
            
            # Apply path rewriting if configured
            rewritten_path = rewrite_path(path, instance.rewrite)
            
            # Get the movie from the instance
            movie = await instance.get_movie_by_tmdb_id(movie_id)
            if movie:
                # Import movie to instance
                response = await instance.import_movie(movie_id, rewritten_path)
                
                # Trigger search if enabled
                if instance.search_on_sync:
                    logger.info(f"Search enabled for movie on {instance.name} (search_on_sync=True)")
                    search_movie(instance.url, instance.api_key, movie_id)
                    logger.info(f"Triggered search for movieId={movie_id} on {instance.name}")
                
                results["imports"].append({
                    "instance": instance.name,
                    "status": "success"
                })
            else:
                logger.warning(f"Movie not found in {instance.name}")
                results["imports"].append({
                    "instance": instance.name,
                    "status": "skipped",
                    "reason": "Movie not found"
                })
        except Exception as e:
            logger.error(f"Failed to import to {instance.name}: {str(e)}")
            results["imports"].append({
                "instance": instance.name,
                "status": "error",
                "error": str(e)
            })

    # Scan media servers if path exists
    if path:
        # Apply sync interval before media server scanning
        if sync_interval > 0 and results["imports"]:
            logger.info(f"Waiting {sync_interval} seconds before scanning media servers")
            await asyncio.sleep(sync_interval)
            
        scanner = MediaServerScanner(config.get("media_servers", []))
        results["scanResults"] = await scanner.scan_path(path, is_series=False)
    
    return results


async def process_instances(self, event_type, movie_id=None, path=None, movie_file=None, movie_data=None):
    """Process all Radarr instances for a given event type."""
    results = []
    
    # Get instances that are enabled for this event type
    instances = self.get_instances_for_event(event_type)
    logger.debug(f"Found \033[1m{len(instances)}\033[0m Radarr instance(s) configured for '\033[1m{event_type}\033[0m' event")
    
    if not instances:
        logger.warning(f"No Radarr instances configured for '\033[1m{event_type}\033[0m' event")
        return results
    
    for instance in instances:
        instance_name = instance.get("name", "Unknown")
        instance_url = instance.get("url", "")
        
        try:
            logger.info(f"Processing Radarr instance: \033[1m{instance_name}\033[0m (\033[1m{instance_url}\033[0m)")
            
            # Process based on event type
            if event_type == "Test":
                result = await self.test_instance(instance)
                results.append(result)
            elif event_type == "Rename":
                if movie_id is not None:
                    result = await self.process_rename(instance, movie_id)
                    results.append(result)
                else:
                    logger.warning(f"Cannot process Rename event: missing movie_id")
            elif event_type == "MovieDelete":
                if movie_id is not None:
                    result = await self.process_movie_delete(instance, movie_id)
                    results.append(result)
                else:
                    logger.warning(f"Cannot process MovieDelete event: missing movie_id")
            elif event_type == "Download" or event_type == "Import":
                if movie_data and movie_file:
                    result = await self.process_download_import(instance, movie_data, movie_file, event_type)
                    results.append(result)
                else:
                    logger.warning(f"Cannot process {event_type} event: missing movie data or file info")
            else:
                logger.warning(f"Unsupported event type: \033[1m{event_type}\033[0m")
        except Exception as e:
            logger.error(f"Error processing Radarr instance \033[1m{instance_name}\033[0m: \033[1m{str(e)}\033[0m")
            results.append({
                "instance": instance_name,
                "status": "error",
                "error": str(e)
            })
    
    return results


async def process_download_import(self, instance, movie_data, movie_file, event_type):
    """Process a Download or Import event by checking if the movie exists and adding it if not."""
    instance_name = instance.get("name", "Unknown")
    instance_url = instance.get("url", "")
    api_key = instance.get("api_key", "")
    
    # Extract movie details
    movie_id = movie_data.get("id")
    tmdb_id = movie_data.get("tmdbId")
    imdb_id = movie_data.get("imdbId")
    title = movie_data.get("title", "Unknown")
    
    logger.debug(f"Processing {event_type} for movie: {title} (TMDB: {tmdb_id}, IMDB: {imdb_id})")
    
    # Check if movie exists in this instance
    movie_exists = await self.check_movie_exists(instance, tmdb_id)
    
    if movie_exists:
        logger.debug(f"Movie '\033[1m{title}\033[0m' already exists in Radarr instance: \033[1m{instance_name}\033[0m")
        return {
            "instance": instance_name,
            "status": "skipped",
            "message": f"Movie already exists"
        }
    
    # If movie doesn't exist, add it
    try:
        # Get quality profile from instance config or use default
        quality_profile_id = instance.get("quality_profile_id")
        if not quality_profile_id:
            # Get profiles and use the first one
            profiles = await self.get_quality_profiles(instance)
            if profiles:
                quality_profile_id = profiles[0].get("id")
                logger.debug(f"Using default quality profile ID: \033[1m{quality_profile_id}\033[0m")
            else:
                raise Exception("No quality profiles available")
        
        # Get root folder from instance config or use default
        root_folder = instance.get("root_folder")
        if not root_folder:
            # Get root folders and use the first one
            folders = await self.get_root_folders(instance)
            if folders:
                root_folder = folders[0].get("path")
                logger.debug(f"Using default root folder: {root_folder}")
            else:
                raise Exception("No root folders available")
        
        # Prepare movie data for adding
        add_data = {
            "title": title,
            "qualityProfileId": quality_profile_id,
            "rootFolderPath": root_folder,
            "monitored": True,
            "addOptions": {
                "searchForMovie": False
            }
        }
        
        # Add TMDB or IMDB ID based on availability
        if tmdb_id:
            add_data["tmdbId"] = tmdb_id
        elif imdb_id:
            add_data["imdbId"] = imdb_id
        else:
            raise Exception("No TMDB or IMDB ID available for movie")
        
        # Add movie to Radarr
        logger.info(f"Adding movie '{title}' to Radarr instance: {instance_name}")
        
        url = f"{instance_url}/api/v3/movie"
        headers = {
            "X-Api-Key": api_key,
            "Content-Type": "application/json"
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=add_data) as response:
                if response.status == 201:
                    result = await response.json()
                    logger.info(f"Successfully added movie '{title}' to Radarr instance: {instance_name}")
                    return {
                        "instance": instance_name,
                        "status": "success",
                        "message": f"Movie added successfully",
                        "movie_id": result.get("id")
                    }
                else:
                    error_text = await response.text()
                    logger.error(f"Failed to add movie to Radarr: {error_text}")
                    return {
                        "instance": instance_name,
                        "status": "error",
                        "error": f"Failed to add movie: {error_text}"
                    }
    except Exception as e:
        logger.error(f"Error adding movie to Radarr instance \033[1m{instance_name}\033[0m: \033[1m{str(e)}\033[0m")
        return {
            "instance": instance_name,
            "status": "error",
            "error": str(e)
        }
