from typing import Any, Dict, List
from utils import http_get, http_post, get_config, parse_time_string
from models import RadarrInstance
import logging
import asyncio
from media_server_service import MediaServerScanner

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
            logger.info(f"Ignoring non-Grab event: {event_type}")
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

        logger.info(f"Processing {len(instances)} Radarr instances")

        results = []
        for inst in instances:
            try:
                logger.info(f"Processing Radarr instance: {inst.name}")

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
                    logger.info(f"Movie already exists (id={movie_id}) on {inst.name}")
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
        logger.info(f"Completed processing with response: {response}")
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
            logger.info(f"Ignoring non-Import event: {event_type}")
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

        logger.info(f"Movie folder path: {folder_path}")
        logger.info(f"Movie file path: {movie_path}")
        logger.info(f"Movie relative path: {relative_path}")

        if not instances:
            logger.warning("No Radarr instances provided")
            return {"status": "error", "reason": "No Radarr instances configured"}

        logger.info(f"Found {len(instances)} Radarr instance(s) to process")
        for inst in instances:
            logger.info(f"Instance {inst.name}: URL={inst.url}, enabled_events={inst.enabled_events}")

        # Get sync interval from config
        config = get_config()
        sync_interval = parse_time_string(config.get("sync_interval", "0"))
        logger.info(f"Using sync interval of {sync_interval} seconds between operations")

        results = []
        for i, inst in enumerate(instances):
            try:
                # Apply sync interval between instances (but not before the first one)
                if i > 0 and sync_interval > 0:
                    logger.info(f"Waiting {sync_interval} seconds before processing next instance")
                    await asyncio.sleep(sync_interval)
                
                logger.info(f"Processing Radarr instance: {inst.name}")

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
                    logger.info(f"Movie already exists (id={movie_id}) on {inst.name}")
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
        logger.info(f"Found {len(media_servers)} media server(s) to scan")
        for server in media_servers:
            logger.info(f"Media server config: name={server.get('name')}, type={server.get('type')}, enabled={server.get('enabled')}")
        
        # Apply sync interval before media server scanning
        if sync_interval > 0 and results:
            logger.info(f"Waiting {sync_interval} seconds before scanning media servers")
            await asyncio.sleep(sync_interval)
            
        scanner = MediaServerScanner(media_servers)
        
        # Try to scan using the most specific path available
        scan_path = None
        if folder_path:  # Use folder path for better Plex library scanning
            scan_path = folder_path
            logger.info(f"Using movie folder path for scanning: {scan_path}")
        elif movie_path:  # Fallback to file path if folder path not available
            scan_path = movie_path
            logger.info(f"Using movie file path for scanning: {scan_path}")
        
        scan_results = []
        if scan_path:
            logger.info(f"Initiating scan for path: {scan_path}")
            scan_results = await scanner.scan_path(scan_path, is_series=False)
            logger.info(f"Scan results: {scan_results}")
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
        logger.info(f"Completed processing with response: {response}")
        return response

    except Exception as e:
        logger.error(f"Error processing webhook: {str(e)}", exc_info=True)
        return {"status": "error", "error": str(e)}
