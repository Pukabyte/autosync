import logging
import time
import asyncio
from utils import http_get, http_post, http_put, get_config, parse_time_string, rewrite_path
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
    """Handle series import by syncing across instances and scanning media servers"""
    series_id = payload.get("series", {}).get("tvdbId")
    path = payload.get("series", {}).get("path")
    
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
            
            # Get the series from the instance
            series = await instance.get_series_by_tvdb_id(series_id)
            if series:
                # Import series to instance
                response = await instance.import_series(series_id, rewritten_path)
                
                # Trigger search if enabled
                if instance.search_on_sync:
                    logger.info(f"Search enabled for series on {instance.name} (search_on_sync=True)")
                    search_series(instance.url, instance.api_key, series_id)
                    logger.info(f"Triggered search for seriesId={series_id} on {instance.name}")
                
                results["imports"].append({
                    "instance": instance.name,
                    "status": "success"
                })
            else:
                logger.warning(f"Series not found in {instance.name}")
                results["imports"].append({
                    "instance": instance.name,
                    "status": "skipped",
                    "reason": "Series not found"
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
        results["scanResults"] = await scanner.scan_path(path, is_series=True)
    
    return results
