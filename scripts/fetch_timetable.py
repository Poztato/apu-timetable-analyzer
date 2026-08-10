"""Collect and retain changed snapshots of APU's timetable feed."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


TIMETABLE_URL = (
    "https://s3-ap-southeast-1.amazonaws.com/open-ws/weektimetable"
)
EXPECTED_FIELDS = frozenset(
    {
        "INTAKE",
        "MODID",
        "MODULE_NAME",
        "DAY",
        "LOCATION",
        "ROOM",
        "LECTID",
        "NAME",
        "SAMACCOUNTNAME",
        "DATESTAMP",
        "DATESTAMP_ISO",
        "TIME_FROM",
        "TIME_TO",
        "TIME_FROM_ISO",
        "TIME_TO_ISO",
        "GROUPING",
        "CLASS_CODE",
        "COLOR",
    }
)
INDEX_RELATIVE_PATH = Path("data/snapshots/index.json")
SNAPSHOT_DIRECTORY_RELATIVE_PATH = Path("data/snapshots/raw")


class SnapshotError(RuntimeError):
    """Raised when collection or snapshot validation cannot safely continue."""


def _parse_iso_datetime(value: Any, row_number: int, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise SnapshotError(f"Row {row_number}: {field} must be a non-empty string.")

    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise SnapshotError(
            f"Row {row_number}: {field} is not a valid ISO timestamp: {value!r}."
        ) from exc

    if parsed.utcoffset() is None:
        raise SnapshotError(
            f"Row {row_number}: {field} must include a timezone offset."
        )
    return parsed


def _parse_iso_date(value: Any, row_number: int) -> date:
    if not isinstance(value, str) or not value.strip():
        raise SnapshotError(
            f"Row {row_number}: DATESTAMP_ISO must be a non-empty string."
        )
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise SnapshotError(
            f"Row {row_number}: DATESTAMP_ISO is not a valid ISO date: {value!r}."
        ) from exc


def validate_feed(feed_bytes: bytes) -> dict[str, Any]:
    """Validate source bytes and return metadata needed by the snapshot index."""

    if not feed_bytes:
        raise SnapshotError("The timetable response is empty.")

    try:
        payload = json.loads(feed_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SnapshotError("The timetable response is not valid UTF-8 JSON.") from exc

    if not isinstance(payload, list):
        raise SnapshotError("The timetable response must be a JSON array.")
    if not payload:
        raise SnapshotError("The timetable response contains no event records.")

    distinct_intakes: set[str] = set()
    event_dates: list[date] = []

    for row_number, record in enumerate(payload, start=1):
        if not isinstance(record, dict):
            raise SnapshotError(f"Row {row_number}: each event must be a JSON object.")

        missing_fields = sorted(EXPECTED_FIELDS.difference(record))
        if missing_fields:
            missing = ", ".join(missing_fields)
            raise SnapshotError(f"Row {row_number}: missing required fields: {missing}.")

        start_at = _parse_iso_datetime(
            record["TIME_FROM_ISO"], row_number, "TIME_FROM_ISO"
        )
        end_at = _parse_iso_datetime(
            record["TIME_TO_ISO"], row_number, "TIME_TO_ISO"
        )
        if end_at <= start_at:
            raise SnapshotError(
                f"Row {row_number}: TIME_TO_ISO must be later than TIME_FROM_ISO."
            )

        event_date = _parse_iso_date(record["DATESTAMP_ISO"], row_number)
        if start_at.date() != event_date:
            raise SnapshotError(
                f"Row {row_number}: DATESTAMP_ISO does not match TIME_FROM_ISO."
            )
        event_dates.append(event_date)

        intake = record["INTAKE"]
        if isinstance(intake, str) and intake.strip():
            distinct_intakes.add(intake.strip())

    if not distinct_intakes:
        raise SnapshotError("The timetable response contains no non-empty intake codes.")

    return {
        "row_count": len(payload),
        "distinct_intake_count": len(distinct_intakes),
        "minimum_event_date": min(event_dates).isoformat(),
        "maximum_event_date": max(event_dates).isoformat(),
    }


def load_snapshot_index(index_path: Path) -> list[dict[str, Any]]:
    """Load and minimally validate the retained snapshot index."""

    if not index_path.exists():
        return []

    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SnapshotError(f"Cannot read snapshot index: {index_path}.") from exc

    if not isinstance(index, list):
        raise SnapshotError("The snapshot index must contain a JSON array.")

    for position, entry in enumerate(index, start=1):
        if not isinstance(entry, dict):
            raise SnapshotError(f"Snapshot index entry {position} must be an object.")
        for field in ("snapshot_id", "sha256", "path"):
            if not isinstance(entry.get(field), str) or not entry[field]:
                raise SnapshotError(
                    f"Snapshot index entry {position} has no valid {field}."
                )
    return index


def _write_bytes_atomically(target: Path, content: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=target.parent, prefix=f".{target.name}.", delete=False
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, target)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _write_json_atomically(target: Path, value: Any) -> None:
    serialized = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    _write_bytes_atomically(target, serialized.encode("utf-8"))


def _deterministic_gzip(content: bytes) -> bytes:
    """Return gzip bytes without a timestamp or source filename in the header."""

    with tempfile.SpooledTemporaryFile() as buffer:
        with gzip.GzipFile(
            filename="", mode="wb", fileobj=buffer, compresslevel=9, mtime=0
        ) as compressed:
            compressed.write(content)
        buffer.seek(0)
        return buffer.read()


def decode_response_body(body: bytes, response_headers: Mapping[str, str]) -> bytes:
    """Decode supported HTTP content encodings before JSON validation."""

    normalized_headers = {key.lower(): value for key, value in response_headers.items()}
    content_encoding = normalized_headers.get("content-encoding", "").strip().lower()
    if content_encoding in ("", "identity"):
        return body
    if content_encoding == "gzip":
        try:
            return gzip.decompress(body)
        except (EOFError, OSError) as exc:
            raise SnapshotError("The timetable response contains invalid gzip data.") from exc
    raise SnapshotError(
        f"Unsupported timetable response content encoding: {content_encoding}."
    )


def collect_snapshot(
    feed_bytes: bytes,
    response_headers: Mapping[str, str],
    repository_root: Path,
    collected_at: datetime | None = None,
    source_url: str = TIMETABLE_URL,
) -> dict[str, Any]:
    """Validate bytes, retain a changed snapshot, and update the index."""

    repository_root = repository_root.resolve()
    index_path = repository_root / INDEX_RELATIVE_PATH
    snapshot_directory = repository_root / SNAPSHOT_DIRECTORY_RELATIVE_PATH
    index = load_snapshot_index(index_path)
    metadata = validate_feed(feed_bytes)
    content_hash = hashlib.sha256(feed_bytes).hexdigest()

    if index and index[-1]["sha256"] == content_hash:
        retained_path = repository_root / Path(index[-1]["path"])
        if not retained_path.is_file():
            raise SnapshotError(
                "The latest index entry matches the feed, but its snapshot file is missing."
            )
        return {
            "status": "unchanged",
            "changed": False,
            "snapshot_id": index[-1]["snapshot_id"],
            "sha256": content_hash,
            **metadata,
        }

    timestamp = collected_at or datetime.now(timezone.utc)
    if timestamp.utcoffset() is None:
        raise SnapshotError("collected_at must include timezone information.")
    timestamp = timestamp.astimezone(timezone.utc).replace(microsecond=0)
    timestamp_for_id = timestamp.strftime("%Y-%m-%dT%H-%M-%SZ")
    snapshot_id = f"{timestamp_for_id}_{content_hash[:12]}"
    snapshot_relative_path = (
        SNAPSHOT_DIRECTORY_RELATIVE_PATH / f"{snapshot_id}.json.gz"
    )
    snapshot_path = repository_root / snapshot_relative_path
    if snapshot_path.exists():
        raise SnapshotError(f"Snapshot path already exists: {snapshot_path}.")

    compressed = _deterministic_gzip(feed_bytes)
    _write_bytes_atomically(snapshot_path, compressed)

    normalized_headers = {key.lower(): value for key, value in response_headers.items()}
    entry = {
        "snapshot_id": snapshot_id,
        "collected_at": timestamp.isoformat().replace("+00:00", "Z"),
        "source_url": source_url,
        "sha256": content_hash,
        "path": snapshot_relative_path.as_posix(),
        "content_length": len(feed_bytes),
        **metadata,
        "etag": normalized_headers.get("etag"),
        "last_modified": normalized_headers.get("last-modified"),
        "s3_version_id": normalized_headers.get("x-amz-version-id"),
    }

    try:
        _write_json_atomically(index_path, [*index, entry])
    except Exception:
        snapshot_path.unlink(missing_ok=True)
        raise

    return {
        "status": "created",
        "changed": True,
        **entry,
    }


def fetch_feed(url: str, timeout_seconds: float) -> tuple[bytes, dict[str, str]]:
    """Fetch the source feed once and return its bytes and response headers."""

    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "apu-timetable-analyzer/0.1",
            "x-refresh": "",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            status = getattr(response, "status", 200)
            if status != 200:
                raise SnapshotError(f"Timetable request returned HTTP {status}.")
            response_headers = dict(response.headers.items())
            response_body = decode_response_body(response.read(), response_headers)
            return response_body, response_headers
    except HTTPError as exc:
        raise SnapshotError(f"Timetable request returned HTTP {exc.code}.") from exc
    except URLError as exc:
        raise SnapshotError(f"Timetable request failed: {exc.reason}.") from exc
    except TimeoutError as exc:
        raise SnapshotError("Timetable request timed out.") from exc


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect a changed snapshot of APU's timetable feed."
    )
    parser.add_argument("--url", default=TIMETABLE_URL, help="Timetable JSON URL.")
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root containing data/snapshots.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="HTTP timeout in seconds.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_argument_parser().parse_args(argv)
    try:
        feed_bytes, response_headers = fetch_feed(arguments.url, arguments.timeout)
        result = collect_snapshot(
            feed_bytes=feed_bytes,
            response_headers=response_headers,
            repository_root=arguments.repository_root,
            source_url=arguments.url,
        )
    except SnapshotError as exc:
        print(f"Snapshot collection failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
