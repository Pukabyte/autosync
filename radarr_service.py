from typing import Any, Dict, List
from utils import http_get, http_post, get_config, parse_time_string
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
                    logger.debug(f"Movie already exists (id={movie_id}) on {inst.name}")
                    results.append({"instance": inst.name, "existingMovieId": movie_id})

            except Exception as e:
                logger.error(f"Error processing instance {inst.name}: {str(e)}")
                results.append({"instance": inst.name, "error": str(e)})

        response = {
            "status": "ok",
            "radarrTitle": title,
            "tmdbId": tmdb_id,
            "results": results,
        }
        logger.debug(f"Completed processing with response: {response}")
        return response

    except Exception as e:
        logger.error(f"Error processing webhook: {str(e)}")
        return {"status": "error", "error": str(e)}


async def handle_radarr_import(payload: Dict[str, Any], instances: List[RadarrInstance]) -> Dict[str, Any]:
    """Handle Radarr import webhook with validated instances."""
    try:
        # Log the full payload for debugging
        logger.debug(f"Received Radarr webhook payload: {payload}")
        
        # Check event type
        event_type = payload.get("eventType", "")
        # Accept both "Import" and "Download" events
        if event_type not in ["Import", "Download"]:
            logger.debug(f"Ignoring non-Import event: {event_type}")
            return {"status": "ignored", "reason": f"Radarr event is {event_type}"}

        # Extract movie data
        movie_data = payload["movie"]
        movie_file = payload.get("movieFile", {})
        tmdb_id = movie_data.get("tmdbId")
        title = movie_data.get("title", "Unknown")
        year = movie_data.get("year")

        logger.info(
            f"Processing Radarr Import: Title={title}, TMDB={tmdb_id}, Year={year}"
        )

        # Get all possible paths from the payload
        folder_path = movie_data.get("folderPath")
        movie_path = movie_file.get("path")
        relative_path = movie_file.get("relativePath")

        logger.debug(f"Movie folder path: {folder_path}")
        logger.debug(f"Movie file path: {movie_path}")
        logger.debug(f"Movie relative path: {relative_path}")

        if not instances:
            logger.warning("No Radarr instances provided")
            return {"status": "error", "reason": "No Radarr instances configured"}

        logger.debug(f"Found {len(instances)} Radarr instance(s) to process")
        for inst in instances:
            logger.debug(f"Instance {inst.name}: URL={inst.url}, enabled_events={inst.enabled_events}")

        # Get sync interval from config
        config = get_config()
        sync_interval = parse_time_string(config.get("sync_interval", "0"))
        logger.debug(f"Using sync interval of {sync_interval} seconds between operations")

        results = []
        for i, inst in enumerate(instances):
            try:
                # Apply sync interval between instances (but not before the first one)
                if i > 0 and sync_interval > 0:
                    logger.debug(f"Waiting {sync_interval} seconds before processing next instance")
                    await asyncio.sleep(sync_interval)
                
                logger.debug(f"Processing Radarr instance: {inst.name}")

                # Check if movie exists
                logger.debug(f"Checking if movie exists in {inst.name} (TMDB ID: {tmdb_id})")
                existing = get_movie_by_tmdbid(inst.url, inst.api_key, tmdb_id)
                logger.debug(f"Existing movie check result: {existing}")

                if not existing:
                    logger.info(f"Movie not found in {inst.name}, adding new movie")
                    # Add movie
                    logger.debug(f"Adding movie to {inst.name} with path={inst.root_folder_path}, quality_profile={inst.quality_profile_id}")
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

                    results.append({
                        "instance": inst.name,
                        "action": "added_movie",
                        "movieId": movie_id
                    })
                else:
                    movie_id = existing[0]["id"]
                    logger.debug(f"Movie already exists (id={movie_id}) on {inst.name}")
                    results.append({
                        "instance": inst.name,
                        "action": "movie_exists",
                        "movieId": movie_id
                    })

            except Exception as e:
                logger.error(f"Error processing instance {inst.name}: {str(e)}", exc_info=True)
                results.append({"instance": inst.name, "error": str(e)})

        # Initialize scanner with media servers from config
        media_servers = config.get("media_servers", [])
        logger.debug(f"Found {len(media_servers)} media server(s) to scan")
        for server in media_servers:
            logger.debug(f"Media server config: name={server.get('name')}, type={server.get('type')}, enabled={server.get('enabled')}")
        
        # Apply sync interval before media server scanning
        if sync_interval > 0 and results:
            logger.debug(f"Waiting {sync_interval} seconds before scanning media servers")
            await asyncio.sleep(sync_interval)
            
        scanner = MediaServerScanner(media_servers)
        
        # Try to scan using the most specific path available
        scan_path = None
        if folder_path:  # Use folder path for better Plex library scanning
            scan_path = folder_path
            logger.debug(f"Using movie folder path for scanning: {scan_path}")
        elif movie_path:  # Fallback to file path if folder path not available
            scan_path = movie_path
            logger.debug(f"Using movie file path for scanning: {scan_path}")
        
        scan_results = []
        if scan_path:
            logger.info(f"Initiating scan for path: {scan_path}")
            scan_results = await scanner.scan_path(scan_path, is_series=False)
            logger.debug(f"Scan results: {scan_results}")
        else:
            logger.warning("No valid path available to scan")

        response = {
            "status": "ok",
            "radarrTitle": title,
            "tmdbId": tmdb_id,
            "results": results,
            "scanResults": scan_results,
            "scannedPath": scan_path
        }
        logger.debug(f"Completed processing with response: {response}")
        return response

    except Exception as e:
        logger.error(f"Error processing webhook: {str(e)}", exc_info=True)
        return {"status": "error", "error": str(e)}


async def process_instances(self, event_type, movie_id=None, path=None, movie_file=None, movie_data=None):
    """Process all Radarr instances for a given event type."""
    results = []
    
    # Get instances that are enabled for this event type
    instances = self.get_instances_for_event(event_type)
    logger.debug(f"Found {len(instances)} Radarr instance(s) configured for '{event_type}' event")
    
    if not instances:
        logger.warning(f"No Radarr instances configured for '{event_type}' event")
        return results
    
    for instance in instances:
        instance_name = instance.get("name", "Unknown")
        instance_url = instance.get("url", "")
        
        try:
            logger.debug(f"Processing Radarr instance: {instance_name} ({instance_url})")
            
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
                logger.warning(f"Unsupported event type: {event_type}")
        except Exception as e:
            logger.error(f"Error processing Radarr instance {instance_name}: {str(e)}")
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
        logger.debug(f"Movie '{title}' already exists in Radarr instance: {instance_name}")
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
                logger.debug(f"Using default quality profile ID: {quality_profile_id}")
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
        logger.error(f"Error adding movie to Radarr instance {instance_name}: {str(e)}")
        return {
            "instance": instance_name,
            "status": "error",
            "error": str(e)
        }
