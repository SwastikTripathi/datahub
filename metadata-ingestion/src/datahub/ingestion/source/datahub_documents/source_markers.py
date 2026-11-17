"""Source tokens for the aspects the documents source embeds text from.

The server marks a profile not current once an aspect it watches advances past the
token on the profile's ``refreshMarker``. The documents source reads its text from
stored aspects, so it can name exactly which version of each aspect it read.
"""

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from datahub.metadata.schema_classes import (
    SemanticRefreshMarkerClass,
    SemanticSourceTokenClass,
)

# Watch keys of the shipped `documentText` binding, by the aspect each is read from.
# A deployment that overrides the binding's paths watches different keys, so tokens
# under these keys do not cover it and its profiles read as not current.
DOCUMENT_TEXT_WATCH_KEYS: Mapping[str, str] = {
    "documentInfo": "documentInfo#contents.text",
    "semanticText": "semanticText#text",
}

_AVRO_PRIMITIVE_BRANCHES = frozenset({"string", "long", "int", "boolean"})


def unwrap_avro_union(value: Any) -> Any:
    """MCL events arrive Avro-JSON encoded: a non-null union value is wrapped in a
    single-key object named for its branch. Plain values pass through."""
    if isinstance(value, dict) and len(value) == 1:
        branch, inner = next(iter(value.items()))
        if "." in branch or branch in _AVRO_PRIMITIVE_BRANCHES:
            return inner
    return value


@dataclass(frozen=True)
class _SourceStamp:
    created_time: int
    version: int


def _source_stamp(system_metadata: Any) -> Optional[_SourceStamp]:
    if system_metadata is None:
        return None
    if hasattr(system_metadata, "to_obj"):
        system_metadata = system_metadata.to_obj()
    system_metadata = unwrap_avro_union(system_metadata)
    if not isinstance(system_metadata, dict):
        return None

    created = unwrap_avro_union(system_metadata.get("aspectCreated"))
    created_time = (
        unwrap_avro_union(created.get("time")) if isinstance(created, dict) else None
    )
    version = unwrap_avro_union(system_metadata.get("version"))
    if created_time is None or version is None:
        return None
    try:
        return _SourceStamp(created_time=int(created_time), version=int(version))
    except (TypeError, ValueError):
        return None


def source_token(system_metadata: Any) -> Optional[SemanticSourceTokenClass]:
    """The token of the aspect version that system metadata describes.

    ``system_metadata`` is a ``SystemMetadataClass`` or the dict of an MCL event.
    Returns None when it lacks ``aspectCreated`` or ``version`` (rows written before
    those were recorded), because a token built from guesses could cover an edit it
    never saw.
    """
    stamp = _source_stamp(system_metadata)
    if stamp is None:
        return None
    return SemanticSourceTokenClass(
        createdTime=stamp.created_time, version=stamp.version
    )


def deletion_token(
    previous_system_metadata: Any,
) -> Optional[SemanticSourceTokenClass]:
    """The token a deleted aspect leaves: its creation time and its last version plus
    one, so the delete orders after every version it removed. Matches the server's
    ``SemanticRefreshSideEffect.sourceToken``. None when the previous system metadata
    lacks either value, because a guessed token could cover an edit it never saw.
    """
    stamp = _source_stamp(previous_system_metadata)
    if stamp is None:
        return None
    return SemanticSourceTokenClass(
        createdTime=stamp.created_time, version=stamp.version + 1
    )


def build_refresh_marker(
    tokens: Mapping[str, SemanticSourceTokenClass],
) -> Optional[SemanticRefreshMarkerClass]:
    """A profile marker from tokens keyed by watch key, or None when there are none."""
    if not tokens:
        return None
    return SemanticRefreshMarkerClass(sources=dict(tokens))


def watch_key_tokens(
    aspect_tokens: Mapping[str, Optional[SemanticSourceTokenClass]],
) -> Dict[str, SemanticSourceTokenClass]:
    """Re-key tokens from aspect name to the aspect's watch key, dropping unread ones."""
    return {
        DOCUMENT_TEXT_WATCH_KEYS[aspect]: token
        for aspect, token in aspect_tokens.items()
        if token is not None and aspect in DOCUMENT_TEXT_WATCH_KEYS
    }


def marker_tokens(
    semantic_content: Optional[Mapping[str, Any]],
) -> Dict[str, SemanticSourceTokenClass]:
    """Tokens the entity refresh marker of a stored semanticContent aspect records.

    A deleted semanticText aspect leaves its deletion token here under its watch key.
    The documents source reads the marker before the source aspects, so this read
    can only be older than the source read, never newer.
    """
    if not semantic_content:
        return {}
    sources = (semantic_content.get("refreshMarker") or {}).get("sources") or {}
    tokens: Dict[str, SemanticSourceTokenClass] = {}
    for key, token in sources.items():
        if not isinstance(token, Mapping):
            continue
        created_time = token.get("createdTime")
        version = token.get("version")
        if created_time is None or version is None:
            continue
        tokens[key] = SemanticSourceTokenClass(
            createdTime=int(created_time), version=int(version)
        )
    return tokens
