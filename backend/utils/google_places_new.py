"""Helpers for proxying Google Places API (New) while preserving legacy responses."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException

PLACES_NEW_AUTOCOMPLETE_URL = "https://places.googleapis.com/v1/places:autocomplete"
PLACES_NEW_DETAILS_BASE_URL = "https://places.googleapis.com/v1/places"

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
        payload["locationBias"] = {
            "circle": {
                "center": center,
                "radius": float(radius),
            }
        }
        # Keep origin separate so Places API (New) can populate distanceMeters;
        # the proxy uses it to sort nearest-first without excluding farther
        # destinations that fall outside the bias circle.
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
