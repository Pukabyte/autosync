import logging
import time
import asyncio
from utils import http_get, http_post, http_put, get_config, parse_time_string
from typing import Dict, Any, List
from models import (
    SonarrSeries,
    SonarrEpisode,
    SonarrAddSeriesOptions,
    WebhookPayload,
    SonarrMonitorTypes,
    SonarrInstance,
)
from media_server_service import MediaServerScanner
from pathlib import Path

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------------
# Sonarr-Specific Functions
# ------------------------------------------------------------------------------
def get_series_by_tvdbid(base_url, api_key, tvdb_id) -> List[Dict[str, Any]]:
    """
    GET /api/v3/series?tvdbId=<tvdb_id>
    Returns an array of series (possibly empty).
    """
    url = f"{base_url}/api/v3/series?tvdbId={tvdb_id}"
    return http_get(url, api_key)


def add_series(base_url: str, api_key: str, series: SonarrSeries) -> Dict[str, Any]:
    """Add a new series to Sonarr using validated model."""
    # Convert model to dict for API call
    payload = series.model_dump(exclude_none=True)

    url = f"{base_url}/api/v3/series"
    return http_post(url, api_key, payload)


def update_episodes(
    base_url: str, api_key: str, episodes: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Update episodes in Sonarr using individual PUT requests."""
    if not episodes:
        return []

    updated_episodes = []
    for episode in episodes:
        try:
            # First get the current episode data
            url = f"{base_url}/api/v3/episode/{episode['id']}"
            current_episode = http_get(url, api_key)

            # Update only the monitored status while preserving other fields
            current_episode["monitored"] = True

            # PUT to the individual episode endpoint
            updated = http_put(url, api_key, current_episode)
            updated_episodes.append(updated)

            logger.info(f"Successfully updated episode {episode['id']}")

        except Exception as e:
            logger.error(f"Failed to update episode {episode['id']}: {str(e)}")
            continue

    return updated_episodes


def search_episodes(
    base_url: str, api_key: str, episode_ids: List[int]
) -> Dict[str, Any]:
    """
    Trigger a search for specific episodes in Sonarr.
    """
    url = f"{base_url}/api/v3/command"
    payload = {"name": "EpisodeSearch", "episodeIds": episode_ids}
    try:
        response = http_post(url, api_key, payload)
        logger.info(f"Triggered search for episodeIds={episode_ids} in Sonarr.")
        return response
    except Exception as e:
        logger.error(f"Failed to trigger search for episodes: {str(e)}")
        raise


def search_series(base_url: str, api_key: str, series_id: int) -> Dict[str, Any]:
    """
    Trigger a search for a specific series in Sonarr.
    """
    url = f"{base_url}/api/v3/command"
    payload = {"name": "SeriesSearch", "seriesId": series_id}
    try:
        response = http_post(url, api_key, payload)
        logger.info(f"Triggered search for seriesId={series_id} in Sonarr.")
        return response
    except Exception as e:
        logger.error(f"Failed to trigger search for seriesId={series_id}: {str(e)}")
        raise


def refresh_series(base_url: str, api_key: str, series_id: int) -> Dict[str, Any]:
    """
    Trigger a refresh for a specific series in Sonarr.
    """
    url = f"{base_url}/api/v3/command"
    payload = {"name": "RefreshSeries", "seriesId": series_id}
    try:
        response = http_post(url, api_key, payload)
        logger.info(f"Triggered refresh for seriesId={series_id} in Sonarr.")
        return response
    except Exception as e:
        logger.error(f"Failed to trigger refresh for seriesId={series_id}: {str(e)}")
        raise


def rescan_series(base_url: str, api_key: str, series_id: int) -> Dict[str, Any]:
    """
    Trigger a rescan for a specific series in Sonarr.
    """
    url = f"{base_url}/api/v3/command"
    payload = {"name": "RescanSeries", "seriesId": series_id}
    try:
        response = http_post(url, api_key, payload)
        logger.info(f"Triggered rescan for seriesId={series_id} in Sonarr.")
        return response
    except Exception as e:
        logger.error(f"Failed to trigger rescan for seriesId={series_id}: {str(e)}")
        raise


async def handle_sonarr_grab(
    payload: Dict[str, Any], instances: List[SonarrInstance]
) -> Dict[str, Any]:
    """Handle Sonarr grab webhook with validated data."""
    try:
        # Validate incoming webhook payload
        webhook_data = WebhookPayload(**payload)

        if webhook_data.eventType != "Grab":
            logger.debug(f"Ignoring non-Grab event: {webhook_data.eventType}")
            return {"status": "ignored", "reason": f"Event is {webhook_data.eventType}"}

        target_season = (
            webhook_data.episodes[0].seasonNumber if webhook_data.episodes else None
        )
        episode_numbers = ", ".join(
            str(ep.episodeNumber) for ep in webhook_data.episodes
        )

        logger.info(
            "Processing Sonarr Grab: Title=%s, TVDB=%s, Season=%s, Episodes=[%s]",
            webhook_data.series.title,
            webhook_data.series.tvdbId,
            target_season,
            episode_numbers,
        )

        if not instances:
            logger.warning("No Sonarr instances provided")
            return {"status": "error", "reason": "No Sonarr instances configured"}

        logger.debug(f"Processing {len(instances)} Sonarr instances")

        results = []
        for inst in instances:
            try:
                logger.debug(f"Processing Sonarr instance: {inst.name}")

                # Check if series exists
                existing = get_series_by_tvdbid(
                    inst.url, inst.api_key, webhook_data.series.tvdbId
                )

                if not existing:
                    logger.info(f"Series not found in {inst.name}, adding new series")
                    # Create series model for addition
                    series = SonarrSeries(
                        tvdbId=webhook_data.series.tvdbId,
                        title=webhook_data.series.title,
                        qualityProfileId=inst.quality_profile_id,
                        seasonFolder=inst.season_folder,
                        rootFolderPath=inst.root_folder_path,
                        monitored=True,
                        # seasons=[Season(seasonNumber=target_season, monitored=True)],
                        seasons=[],
                        addOptions=SonarrAddSeriesOptions(
                            ignoreEpisodesWithFiles=True,
                            monitor=SonarrMonitorTypes.future,
                            searchForMissingEpisodes=inst.search_on_sync,
                            searchForCutoffUnmetEpisodes=inst.search_on_sync,
                        ),
                    )

                    # Add the series
                    added = add_series(inst.url, inst.api_key, series)
                    series_id = added["id"]
                    logger.info(f"Added new series (id={series_id}) to {inst.name}")
                    
                    # Log if search was enabled
                    if inst.search_on_sync:
                        logger.info(f"Search enabled for new series on {inst.name} (search_on_sync=True)")
                    
                    logger.debug(
                        "Pausing for 2 seconds after adding series so episodes can be added"
                    )
                    time.sleep(2)
                else:
                    series_id = existing[0]["id"]
                    logger.debug(
                        f"Found existing series (id={series_id}) on {inst.name}"
                    )

                # Handle episode monitoring
                if webhook_data.episodes and target_season is not None:
                    url = f"{inst.url}/api/v3/episode?seriesId={series_id}&seasonNumber={target_season}"
                    season_eps = http_get(url, inst.api_key)
                    logger.debug(
                        f"Retrieved {len(season_eps)} episodes for season {target_season}"
                    )

                    to_update = []
                    for grabbed_ep in webhook_data.episodes:
                        matching_eps = [
                            ep
                            for ep in season_eps
                            if ep["episodeNumber"] == grabbed_ep.episodeNumber
                        ]

                        if matching_eps:
                            target_ep = matching_eps[0]
                            if not target_ep.get("monitored", False):
                                episode = SonarrEpisode(
                                    id=target_ep["id"],
                                    seriesId=series_id,
                                    seasonNumber=grabbed_ep.seasonNumber,
                                    episodeNumber=grabbed_ep.episodeNumber,
                                    title=grabbed_ep.title,
                                    monitored=True,
                                    hasFile=target_ep.get("hasFile", False),
                                    episodeFileId=target_ep.get("episodeFileId", 0),
                                )
                                to_update.append(episode)
                                logger.debug(
                                    f"Will monitor episode {grabbed_ep.episodeNumber}"
                                )

                    if to_update:
                        episodes_to_update = [
                            ep.model_dump(exclude_none=True) for ep in to_update
                        ]
                        updated = update_episodes(
                            inst.url, inst.api_key, episodes_to_update
                        )
                        logger.info(f"Updated {len(updated)} episodes in {inst.name}")
                        # Search episodes
                        # time.sleep(0.5)
                        episode_ids = [ep["id"] for ep in updated]
                        search_episodes(inst.url, inst.api_key, episode_ids)
                        logger.info(f"Triggered search for episodes in {inst.name}")
                        results.append(
                            {
                                "instance": inst.name,
                                "episodesMonitored": len(updated),
                                "season": target_season,
                            }
                        )
                    else:
                        logger.debug(f"No episodes need updating in {inst.name}")
                        results.append(
                            {
                                "instance": inst.name,
                                "episodesMonitored": 0,
                                "season": target_season,
                            }
                        )

            except Exception as e:
                logger.error(f"Error processing instance {inst.name}: {str(e)}")
                results.append(
                    {"instance": inst.name, "error": str(e), "season": target_season}
                )

        response = {
            "status": "ok",
            "sonarrTitle": webhook_data.series.title,
            "tvdbId": webhook_data.series.tvdbId,
            "targetSeason": target_season,
            "results": results,
        }
        logger.debug(f"Completed processing with response: {response}")
        return response

    except Exception as e:
        logger.error(f"Error processing webhook: {str(e)}")
        return {"status": "error", "error": str(e)}


async def handle_sonarr_import(payload: Dict[str, Any], instances: List[SonarrInstance]) -> Dict[str, Any]:
    """Handle Sonarr import webhook with validated instances."""
    try:
        # Log the full payload for debugging
        logger.debug(f"Received Sonarr webhook payload: {payload}")
        
        # Check event type
        event_type = payload.get("eventType", "")
        # Accept both "Import" and "Download" events
        if event_type not in ["Import", "Download"]:
            logger.debug(f"Ignoring non-Import event: {event_type}")
            return {"status": "ignored", "reason": f"Sonarr event is {event_type}"}

        webhook_data = WebhookPayload(**payload)

        logger.info(
            "Processing Sonarr Import: Title=%s, TVDB=%s, Episodes=%s",
            webhook_data.series.title,
            webhook_data.series.tvdbId,
            [f"S{ep.seasonNumber}E{ep.episodeNumber}" for ep in webhook_data.episodes]
        )

        # Get all possible paths from the payload
        series_path = webhook_data.series.path
        episode_path = payload.get("episodeFile", {}).get("path")
        
        logger.debug(f"Path details:")
        logger.debug(f"  ├─ Series path: \033[1m{series_path}\033[0m")
        logger.debug(f"  └─ Episode file path: \033[1m{episode_path}\033[0m")

        if not instances:
            logger.warning("No Sonarr instances provided")
            return {"status": "error", "reason": "No Sonarr instances configured"}

        logger.debug(f"Found {len(instances)} Sonarr instance(s) to process")

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
                
                logger.debug(f"Processing instance {inst.name}:")
                logger.debug(f"  ├─ URL: {inst.url}")
                logger.debug(f"  └─ Events: {', '.join(inst.enabled_events)}")

                # Check if series exists
                existing = get_series_by_tvdbid(inst.url, inst.api_key, webhook_data.series.tvdbId)

                if not existing:
                    logger.info(f"Series not found in {inst.name}, adding new series")
                    # Create series model for addition
                    series = SonarrSeries(
                        tvdbId=webhook_data.series.tvdbId,
                        title=webhook_data.series.title,
                        qualityProfileId=inst.quality_profile_id,
                        seasonFolder=inst.season_folder,
                        rootFolderPath=inst.root_folder_path,
                        monitored=True,
                        seasons=[],
                        addOptions=SonarrAddSeriesOptions(
                            ignoreEpisodesWithFiles=True,
                            monitor=SonarrMonitorTypes.future,
                            searchForMissingEpisodes=inst.search_on_sync,
                            searchForCutoffUnmetEpisodes=inst.search_on_sync,
                        ),
                    )

                    added = add_series(inst.url, inst.api_key, series)
                    series_id = added["id"]
                    logger.info(f"Added new series (id={series_id}) to {inst.name}")
                    
                    # Log if search was enabled
                    if inst.search_on_sync:
                        logger.info(f"Search enabled for new series on {inst.name} (search_on_sync=True)")

                    results.append({
                        "instance": inst.name,
                        "action": "added_series",
                        "seriesId": series_id
                    })
                else:
                    series_id = existing[0]["id"]
                    logger.debug(f"Series already exists (id={series_id}) on {inst.name}")
                    
                    # Refresh and rescan the series
                    refresh_result = refresh_series(inst.url, inst.api_key, series_id)
                    rescan_result = rescan_series(inst.url, inst.api_key, series_id)
                    
                    results.append({
                        "instance": inst.name,
                        "action": "series_refreshed_and_rescanned",
                        "seriesId": series_id
                    })

            except Exception as e:
                logger.error(f"Error processing instance {inst.name}: {str(e)}", exc_info=True)
                results.append({"instance": inst.name, "error": str(e)})

        # Initialize scanner with media servers from config
        media_servers = config.get("media_servers", [])
        logger.debug(f"Media server scan details:")
        logger.debug(f"  ├─ Total servers: \033[1m{len(media_servers)}\033[0m")
        logger.debug(f"  └─ Active servers: \033[1m{len([s for s in media_servers if s.get('enabled')])}\033[0m")
        
        # Apply sync interval before media server scanning
        if sync_interval > 0 and results:
            logger.debug(f"Waiting {sync_interval} seconds before scanning media servers")
            await asyncio.sleep(sync_interval)
            
        scanner = MediaServerScanner(media_servers)
        
        # Try to scan using the most specific path available
        scan_path = None
        if series_path:  # Use series path for better Plex library scanning
            scan_path = series_path
            logger.debug(f"Using series path for scanning: {scan_path}")
        elif episode_path:  # Fallback to episode path if series path not available
            # For TV shows, scan the season folder (one up from episode)
            scan_path = str(Path(episode_path).parent)
            logger.debug(f"Using episode parent path for scanning: {scan_path}")
        
        scan_results = []
        if scan_path:
            logger.info(f"Initiating scan for path: {scan_path}")
            scan_results = await scanner.scan_path(scan_path, is_series=True)
            logger.debug(f"Scan results: {scan_results}")
        else:
            logger.warning("No valid path available to scan")

        response = {
            "status": "ok",
            "sonarrTitle": webhook_data.series.title,
            "tvdbId": webhook_data.series.tvdbId,
            "results": results,
            "scanResults": scan_results,
            "scannedPath": scan_path
        }
        logger.info(f"Completed processing with response: {response}")
        return response

    except Exception as e:
        logger.error(f"Error processing webhook: {str(e)}", exc_info=True)
        return {"status": "error", "error": str(e)}
