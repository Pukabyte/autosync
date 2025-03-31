# ------------------------------------------------------------------------------
# Shared Helpers - utils.py
# ------------------------------------------------------------------------------
import yaml
import logging
import requests
import re
from typing import Dict, Any, Optional, List
import json

# Add at the top of the file
logger = logging.getLogger(__name__)

# Initialize CONFIG as a module-level dictionary
CONFIG: Dict[str, Any] = {"instances": []}


def load_config() -> Dict[str, Any]:
    """Load configuration from YAML file and return it."""
    global CONFIG
    try:
        with open("config.yaml", "r") as f:
            CONFIG = yaml.safe_load(f)
            if not CONFIG or "instances" not in CONFIG:
                raise ValueError("Invalid config: 'instances' key is missing")
            logger.info("Loaded config with %d instance(s).", len(CONFIG["instances"]))
            return CONFIG
    except Exception as e:
        logger.error(f"Error loading config: {str(e)}")
        # Ensure CONFIG has at least an empty instances list
        CONFIG = {"instances": []}
        raise


def get_config() -> Dict[str, Any]:
    """Get the current configuration."""
    global CONFIG
    if not CONFIG or "instances" not in CONFIG:
        return load_config()
    return CONFIG


def save_config(config: Dict[str, Any]) -> bool:
    """Save configuration to YAML file and update global CONFIG."""
    global CONFIG
    try:
        # Ensure required keys exist
        if "instances" not in config:
            config["instances"] = []
        if "media_servers" not in config:
            config["media_servers"] = []
        if "webhook_events" not in config:
            # Add default webhook events if not present
            config["webhook_events"] = {
                "sonarr": ["Grab", "Download", "Rename", "SeriesDelete", "EpisodeFileDelete", "Import", "SeriesAdd"],
                "radarr": ["Grab", "Download", "Rename", "MovieDelete", "MovieFileDelete", "Import"]
            }
        else:
            # Ensure SeriesAdd is in sonarr events if webhook_events exists
            if "sonarr" in config["webhook_events"] and "SeriesAdd" not in config["webhook_events"]["sonarr"]:
                config["webhook_events"]["sonarr"].append("SeriesAdd")
                
        if "sync_delay" not in config:
            config["sync_delay"] = "0"
        if "sync_interval" not in config:
            config["sync_interval"] = "0"
        
        # Write to file
        with open("config.yaml", "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)
        
        # Update global config
        CONFIG = config
        logger.info(f"Saved config with {len(config.get('instances', []))} instance(s) and {len(config.get('media_servers', []))} media server(s)")
        return True
    except Exception as e:
        logger.error(f"Error saving config: {str(e)}")
        return False


def http_get(url: str, api_key: str) -> Dict[str, Any]:
    """Make a GET request with API key authentication."""
    # Ensure URL has protocol
    if not url.startswith(('http://', 'https://')):
        url = f"http://{url}"
        logger.debug(f"Added http:// protocol to URL: {url}")
        
    headers = {"X-Api-Key": api_key}
    
    # Log request (masking api key)
    masked_url = url.replace(api_key, "********") if api_key in url else url
    logger.debug(f"GET {masked_url}")
    
    try:
        response = requests.get(url, headers=headers)
        
        # Log response details for debugging
        logger.debug(f"Response status: {response.status_code}")
        if response.status_code == 401:
            logger.error(f"Unauthorized error for {masked_url}. Please check API key and URL.")
            logger.debug(f"Response headers: {dict(response.headers)}")
        
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"HTTP request failed: {str(e)}")
        raise


def http_post(url: str, api_key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Make a POST request with API key authentication and JSON payload."""
    # Ensure URL has protocol
    if not url.startswith(('http://', 'https://')):
        url = f"http://{url}"
        logger.debug(f"Added http:// protocol to URL: {url}")
        
    headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}
    response = requests.post(url, headers=headers, json=payload)
    response.raise_for_status()
    return response.json()


def http_put(url: str, api_key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Make a PUT request with API key authentication."""
    # Ensure URL has protocol
    if not url.startswith(('http://', 'https://')):
        url = f"http://{url}"
        logger.debug(f"Added http:// protocol to URL: {url}")
        
    headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}

    # Log request (masking api key)
    masked_payload = {**payload}
    if "apiKey" in masked_payload:
        masked_payload["apiKey"] = "********"
    logger.debug(f"PUT {url} with payload: {json.dumps(masked_payload, indent=2)}")

    response = requests.put(url, headers=headers, json=payload)

    # Log response
    logger.info(f"Response status: {response.status_code}")
    if response.content:
        try:
            response_data = response.json()
            logger.debug(f"Response data: {json.dumps(response_data, indent=2)}")
        except:
            logger.debug(f"Raw response: {response.text}")

    response.raise_for_status()
    return response.json() if response.content else {}


def parse_time_string(time_str: str) -> float:
    """
    Parse a time string like '5s', '1m', '500ms' into seconds (float).
    Supports: ms (milliseconds), s (seconds), m (minutes)
    """
    if not time_str:
        return 0
        
    # Default to seconds if no unit specified
    if isinstance(time_str, (int, float)):
        return float(time_str)
    
    if isinstance(time_str, str) and time_str.isdigit():
        return float(time_str)
        
    # Parse with regex
    if isinstance(time_str, str):
        match = re.match(r'^(\d+(?:\.\d+)?)(ms|s|m)$', time_str)
        if match:
            value, unit = match.groups()
            value = float(value)
            
            if unit == 'ms':
                return value / 1000
            elif unit == 's':
                return value
            elif unit == 'm':
                return value * 60
    
    logger.warning(f"Invalid time string format: {time_str}, defaulting to 0")
    return 0


def rewrite_path(path: str, rewrite_rules: Optional[List[Dict[str, str]]] = None) -> str:
    """
    Rewrite a path using the provided rewrite rules.
    
    Args:
        path: The path to rewrite
        rewrite_rules: List of PathRewrite objects containing from_path and to_path attributes
        
    Returns:
        The rewritten path if a matching rule is found, otherwise the original path
    """
    if not rewrite_rules:
        return path
        
    for rule in rewrite_rules:
        from_path = rule.from_path if hasattr(rule, 'from_path') else rule.get('from_path', '')
        to_path = rule.to_path if hasattr(rule, 'to_path') else rule.get('to_path', '')
        
        if from_path and to_path and path.startswith(from_path):
            return path.replace(from_path, to_path, 1)
            
    return path
