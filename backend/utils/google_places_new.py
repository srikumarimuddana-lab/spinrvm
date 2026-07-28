"""Helpers for proxying Google Places API (New) while preserving legacy responses."""

from __future__ import annotations

import math
from typing import Any, Optional

from fastapi import HTTPException

PLACES_NEW_AUTOCOMPLETE_URL = "https://places.googleapis.com/v1/places:autocomplete"
PLACES_NEW_DETAILS_BASE_URL = "https://places.googleapis.com/v1/places"
PLACES_NEW_TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

# Keep autocomplete in the lower-cost request tier by asking only for fields the
# mobile/admin UIs already render, then translate those fields back to the legacy
# response shape expected by the clients.
PLACES_NEW_AUTOCOMPLETE_FIELD_MASK = ",".join(
    [
        "suggestions.placePrediction.placeId",
        "suggestions.placePrediction.text.text",
        "suggestions.placePrediction.structuredFormat.mainText.text",
        "suggestions.placePrediction.structuredFormat.secondaryText.text",
        "suggestions.placePrediction.types",
        "suggestions.placePrediction.distanceMeters",
    ]
)

# Essentials fields only: enough for our pickup/dropoff coordinates and label.
PLACES_NEW_DETAILS_FIELD_MASK = "location,formattedAddress"

# Named-place lookups (e.g. "walmart", "saskatoon airport") only need enough
# to build a candidate list + hand a coordinate to the fare/dispatch path —
# not the richer Essentials/Pro field set.
PLACES_NEW_TEXT_SEARCH_FIELD_MASK = ",".join(
    [
        "places.displayName.text",
        "places.formattedAddress",
        "places.location",
    ]
)


def places_new_headers(api_key: str, field_mask: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": field_mask,
    }


def _parse_location(location: str) -> tuple[float, float]:
    try:
        lat_raw, lng_raw = location.split(",", 1)
        lat = float(lat_raw)
        lng = float(lng_raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail="location must be formatted as 'lat,lng'",
        ) from exc

    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise HTTPException(
            status_code=400,
            detail="location latitude/longitude out of range",
        )
    return lat, lng


def build_autocomplete_payload(
    input_text: str,
    session_token: Optional[str],
    location: Optional[str],
    radius: int,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "input": input_text,
        "languageCode": "en",
        "includedRegionCodes": ["ca"],
    }
    if session_token:
        payload["sessionToken"] = session_token

    if location:
        lat, lng = _parse_location(location)
        center = {"latitude": lat, "longitude": lng}
        # Places API (New) caps circular locationRestriction radius at 50km.
        new_api_radius = min(float(radius), 50000.0)
        payload["locationRestriction"] = {
            "circle": {
                "center": center,
                "radius": new_api_radius,
            }
        }
        payload["origin"] = center

    return payload


def _text_value(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        text = value.get("text")
        return text if isinstance(text, str) and text else None
    return None


def _legacy_prediction(place_prediction: dict[str, Any]) -> Optional[dict[str, Any]]:
    place_id = place_prediction.get("placeId")
    if not place_id:
        return None

    description = _text_value(place_prediction.get("text")) or ""
    structured = place_prediction.get("structuredFormat") or {}
    main_text = _text_value(structured.get("mainText")) or description
    secondary_text = _text_value(structured.get("secondaryText"))

    legacy: dict[str, Any] = {
        "place_id": place_id,
        "description": description or main_text,
        "structured_formatting": {
            "main_text": main_text,
        },
    }
    if secondary_text:
        legacy["structured_formatting"]["secondary_text"] = secondary_text
    if isinstance(place_prediction.get("types"), list):
        legacy["types"] = place_prediction["types"]
    if place_prediction.get("distanceMeters") is not None:
        legacy["distance_meters"] = place_prediction["distanceMeters"]
    return legacy


def legacy_predictions_from_new_response(data: dict[str, Any]) -> list[dict[str, Any]]:
    predictions: list[dict[str, Any]] = []
    for suggestion in data.get("suggestions", []):
        place_prediction = suggestion.get("placePrediction") if isinstance(suggestion, dict) else None
        if not isinstance(place_prediction, dict):
            continue
        legacy = _legacy_prediction(place_prediction)
        if legacy:
            predictions.append(legacy)
    return predictions


def places_new_details_url(place_id: str) -> str:
    normalized = place_id.removeprefix("places/")
    return f"{PLACES_NEW_DETAILS_BASE_URL}/{normalized}"


def legacy_details_from_new_response(data: dict[str, Any]) -> dict[str, Any]:
    loc = data.get("location") or {}
    return {
        "lat": loc.get("latitude"),
        "lng": loc.get("longitude"),
        "formatted_address": data.get("formattedAddress"),
    }


def build_text_search_payload(
    text_query: str,
    near_lat: Optional[float],
    near_lng: Optional[float],
    radius_meters: float,
) -> dict[str, Any]:
    """Build a Places API (New) Text Search (searchText) request body.

    Unlike Autocomplete (New), Text Search (New)'s ``locationRestriction``
    only accepts a rectangle, not a circle — so the bias box below IS the
    hard filter (no candidate outside it can be returned at all), not a soft
    hint like the legacy Geocoding/Text-Search APIs' ``bounds`` parameter.
    """
    payload: dict[str, Any] = {
        "textQuery": text_query,
        "languageCode": "en",
        "regionCode": "CA",
    }
    if near_lat is not None and near_lng is not None:
        dlat = radius_meters / 111_000
        dlng = dlat / max(math.cos(math.radians(near_lat)), 0.2)
        payload["locationRestriction"] = {
            "rectangle": {
                "low": {"latitude": near_lat - dlat, "longitude": near_lng - dlng},
                "high": {"latitude": near_lat + dlat, "longitude": near_lng + dlng},
            }
        }
        # Soft ranking signal on top of the hard restriction above — Text
        # Search (New) sorts by relevance by default; origin nudges ties
        # toward the nearer result, matching the Autocomplete (New) path.
        payload["locationBias"] = {
            "circle": {
                "center": {"latitude": near_lat, "longitude": near_lng},
                "radius": min(radius_meters, 50000.0),
            }
        }
    return payload


def legacy_place_results_from_text_search(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Translate a Text Search (New) response into the legacy Google Places
    ``results[]`` shape (``name`` / ``formatted_address`` /
    ``geometry.location.{lat,lng}``) so it can flow through the same
    candidate-building code as the legacy Geocoding/Text-Search responses.

    Text Search (New) carries no location-precision signal (no
    ``location_type`` / ``partial_match``), same as the legacy Text Search
    API it replaces — callers already treat that combination as UNKNOWN
    precision, never flagged as imprecise.
    """
    results: list[dict[str, Any]] = []
    for place in data.get("places") or []:
        if not isinstance(place, dict):
            continue
        loc = place.get("location") or {}
        lat, lng = loc.get("latitude"), loc.get("longitude")
        if lat is None or lng is None:
            continue
        results.append(
            {
                "name": _text_value(place.get("displayName")),
                "formatted_address": place.get("formattedAddress"),
                "geometry": {"location": {"lat": lat, "lng": lng}},
            }
        )
    return results
