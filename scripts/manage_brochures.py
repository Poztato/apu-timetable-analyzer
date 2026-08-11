"""Download, verify, and extract text from registered curriculum brochures."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


CONFIG_RELATIVE_PATH = Path("config/elective_rules.json")
TEXT_DIRECTORY_RELATIVE_PATH = Path(
    "OneDrive_2026-08-10/Brochures/_text"
)
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


class BrochureError(RuntimeError):
    """Raised when a brochure cannot be handled safely."""


@dataclass(frozen=True)
class BrochureSource:
    """A brochure registered in the elective-rules configuration."""

    source_id: str
    title: str
    edition: str
    local_path: Path
    url: str
    sha256: str


def resolve_repository_path(
    repository_root: Path,
    relative_path: Path | str,
    label: str,
) -> Path:
    """Resolve a relative path and require it to remain inside the repository."""

    root = repository_root.resolve()
    supplied_path = Path(relative_path)
    if supplied_path.is_absolute():
        raise BrochureError(f"{label} must be relative to the repository root.")

    resolved = (root / supplied_path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise BrochureError(f"{label} points outside the repository root.") from exc
    return resolved


def _required_string(
    source: dict[str, Any], field: str, position: int
) -> str:
    value = source.get(field)
    if not isinstance(value, str) or not value.strip():
        raise BrochureError(
            f"Brochure source {position} has no valid {field}."
        )
    return value.strip()


def load_brochure_sources(
    repository_root: Path,
    config_relative_path: Path = CONFIG_RELATIVE_PATH,
) -> list[BrochureSource]:
    """Load and validate the brochure registry from elective_rules.json."""

    config_path = resolve_repository_path(
        repository_root, config_relative_path, "Configuration path"
    )
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise BrochureError(f"Cannot read brochure configuration: {config_path}.") from exc
    except json.JSONDecodeError as exc:
        raise BrochureError(
            f"Brochure configuration is not valid JSON: {config_path}."
        ) from exc

    raw_sources = payload.get("sources") if isinstance(payload, dict) else None
    if not isinstance(raw_sources, list) or not raw_sources:
        raise BrochureError(
            "Brochure configuration must contain a non-empty sources array."
        )

    sources: list[BrochureSource] = []
    seen_ids: set[str] = set()
    seen_paths: set[Path] = set()
    for position, raw_source in enumerate(raw_sources, start=1):
        if not isinstance(raw_source, dict):
            raise BrochureError(f"Brochure source {position} must be an object.")

        source_id = _required_string(raw_source, "id", position)
        if source_id in seen_ids:
            raise BrochureError(f"Duplicate brochure source id: {source_id}.")

        local_path_text = _required_string(raw_source, "local_path", position)
        local_path = Path(local_path_text)
        resolved_local_path = resolve_repository_path(
            repository_root,
            local_path,
            f"Local path for source {source_id}",
        )
        if resolved_local_path in seen_paths:
            raise BrochureError(
                f"Multiple brochure sources use the same local path: {local_path_text}."
            )

        url = _required_string(raw_source, "url", position)
        parsed_url = urlparse(url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise BrochureError(f"Source {source_id} has no valid HTTP(S) URL.")

        expected_hash = _required_string(raw_source, "sha256", position).lower()
        if not SHA256_PATTERN.fullmatch(expected_hash):
            raise BrochureError(
                f"Source {source_id} must have a 64-character SHA-256 hash."
            )

        sources.append(
            BrochureSource(
                source_id=source_id,
                title=_required_string(raw_source, "title", position),
                edition=_required_string(raw_source, "edition", position),
                local_path=local_path,
                url=url,
                sha256=expected_hash,
            )
        )
        seen_ids.add(source_id)
        seen_paths.add(resolved_local_path)

    return sources


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of a file without loading it all into memory."""

    digest = hashlib.sha256()
    try:
        with path.open("rb") as input_file:
            for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise BrochureError(f"Cannot read brochure file: {path}.") from exc
    return digest.hexdigest()


def verify_pdf(path: Path, expected_hash: str) -> dict[str, Any]:
    """Verify that a path is a PDF with the exact registered content hash."""

    if not path.is_file():
        raise BrochureError(f"Brochure PDF is missing: {path}.")

    try:
        with path.open("rb") as brochure_file:
            header = brochure_file.read(5)
    except OSError as exc:
        raise BrochureError(f"Cannot read brochure PDF: {path}.") from exc

    if header != b"%PDF-":
        raise BrochureError(f"Downloaded content is not a PDF: {path}.")

    actual_hash = sha256_file(path)
    if actual_hash != expected_hash.lower():
        raise BrochureError(
            "Brochure SHA-256 mismatch for "
            f"{path}. Expected {expected_hash.lower()}, received {actual_hash}."
        )

    return {
        "sha256": actual_hash,
        "size_bytes": path.stat().st_size,
    }


def download_brochure(
    source: BrochureSource,
    repository_root: Path,
    timeout_seconds: float,
    opener: Callable[..., Any] = urlopen,
) -> tuple[Path, dict[str, Any]]:
    """Download to a temporary file, verify it, then replace the target."""

    target = resolve_repository_path(
        repository_root,
        source.local_path,
        f"Local path for source {source.source_id}",
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    request = Request(
        source.url,
        headers={
            "Accept": "application/pdf",
            "User-Agent": "apu-timetable-analyzer/0.1",
        },
        method="GET",
    )

    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".part",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            with opener(request, timeout=timeout_seconds) as response:
                status = getattr(response, "status", 200)
                if status not in (None, 200):
                    raise BrochureError(
                        f"Source {source.source_id} returned HTTP {status}."
                    )
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    temporary_file.write(chunk)

        metadata = verify_pdf(temporary_path, source.sha256)
        temporary_path.replace(target)
        temporary_path = None
        return target, metadata
    except HTTPError as exc:
        raise BrochureError(
            f"Source {source.source_id} returned HTTP {exc.code}."
        ) from exc
    except URLError as exc:
        raise BrochureError(
            f"Source {source.source_id} download failed: {exc.reason}."
        ) from exc
    except TimeoutError as exc:
        raise BrochureError(
            f"Source {source.source_id} download timed out."
        ) from exc
    except OSError as exc:
        raise BrochureError(
            f"Cannot save source {source.source_id} to {target}."
        ) from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def ensure_brochure_pdf(
    source: BrochureSource,
    repository_root: Path,
    timeout_seconds: float,
    force: bool = False,
    opener: Callable[..., Any] = urlopen,
) -> tuple[str, Path, dict[str, Any]]:
    """Reuse a valid local PDF or download a verified replacement."""

    target = resolve_repository_path(
        repository_root,
        source.local_path,
        f"Local path for source {source.source_id}",
    )
    if target.exists() and not force:
        try:
            metadata = verify_pdf(target, source.sha256)
        except BrochureError as exc:
            raise BrochureError(
                f"{exc} Use --force to fetch a verified replacement."
            ) from exc
        return "verified", target, metadata

    target, metadata = download_brochure(
        source,
        repository_root,
        timeout_seconds,
        opener=opener,
    )
    return "downloaded", target, metadata


def _default_pdf_reader(pdf_path: Path) -> Any:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise BrochureError(
            "Text extraction requires pypdf. Run: "
            "python -m pip install -r requirements.txt"
        ) from exc

    try:
        return PdfReader(str(pdf_path))
    except Exception as exc:
        raise BrochureError(f"Cannot parse PDF structure: {pdf_path}.") from exc


def _write_text_atomically(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".part",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(content)
        temporary_path.replace(target)
        temporary_path = None
    except OSError as exc:
        raise BrochureError(f"Cannot write extracted brochure text: {target}.") from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def extract_pdf_text(
    pdf_path: Path,
    text_path: Path,
    reader_factory: Callable[[Path], Any] = _default_pdf_reader,
) -> dict[str, int]:
    """Extract embedded text with explicit page boundaries and no OCR."""

    reader = reader_factory(pdf_path)
    sections: list[str] = []
    pages_with_text = 0
    try:
        pages = list(reader.pages)
        for page_number, page in enumerate(pages, start=1):
            extracted = page.extract_text() or ""
            normalized = extracted.strip()
            if normalized:
                pages_with_text += 1
            sections.append(f"===== PAGE {page_number} =====\n{normalized}")
    except Exception as exc:
        raise BrochureError(f"Cannot extract embedded text from {pdf_path}.") from exc

    if not pages:
        raise BrochureError(f"Brochure contains no pages: {pdf_path}.")
    if pages_with_text == 0:
        raise BrochureError(
            f"Brochure contains no extractable embedded text: {pdf_path}."
        )

    _write_text_atomically(text_path, "\n\n".join(sections) + "\n")
    return {
        "page_count": len(pages),
        "pages_with_text": pages_with_text,
    }


def _select_sources(
    sources: list[BrochureSource], source_ids: list[str] | None
) -> list[BrochureSource]:
    if not source_ids:
        return sources

    requested = set(source_ids)
    available = {source.source_id for source in sources}
    unknown = sorted(requested.difference(available))
    if unknown:
        raise BrochureError(
            "Unknown brochure source id(s): " + ", ".join(unknown) + "."
        )
    return [source for source in sources if source.source_id in requested]


def manage_brochures(
    repository_root: Path,
    config_relative_path: Path = CONFIG_RELATIVE_PATH,
    text_directory_relative_path: Path = TEXT_DIRECTORY_RELATIVE_PATH,
    source_ids: list[str] | None = None,
    timeout_seconds: float = 60.0,
    force: bool = False,
    verify_only: bool = False,
    extract_text: bool = True,
    opener: Callable[..., Any] = urlopen,
    reader_factory: Callable[[Path], Any] = _default_pdf_reader,
) -> dict[str, Any]:
    """Process selected sources and return a machine-readable summary."""

    root = repository_root.resolve()
    sources = _select_sources(
        load_brochure_sources(root, config_relative_path), source_ids
    )
    text_directory = resolve_repository_path(
        root, text_directory_relative_path, "Extracted-text directory"
    )

    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for source in sources:
        try:
            if verify_only:
                pdf_path = resolve_repository_path(
                    root,
                    source.local_path,
                    f"Local path for source {source.source_id}",
                )
                pdf_metadata = verify_pdf(pdf_path, source.sha256)
                pdf_status = "verified"
            else:
                pdf_status, pdf_path, pdf_metadata = ensure_brochure_pdf(
                    source,
                    root,
                    timeout_seconds,
                    force=force,
                    opener=opener,
                )

            source_result: dict[str, Any] = {
                "source_id": source.source_id,
                "title": source.title,
                "edition": source.edition,
                "pdf_path": pdf_path.relative_to(root).as_posix(),
                "pdf_status": pdf_status,
                **pdf_metadata,
            }
            if extract_text and not verify_only:
                text_path = text_directory / f"{source.source_id}.txt"
                extraction_metadata = extract_pdf_text(
                    pdf_path,
                    text_path,
                    reader_factory=reader_factory,
                )
                source_result.update(
                    {
                        "text_path": text_path.relative_to(root).as_posix(),
                        **extraction_metadata,
                    }
                )
            results.append(source_result)
        except BrochureError as exc:
            failures.append(
                {"source_id": source.source_id, "error": str(exc)}
            )

    return {
        "status": "ok" if not failures else "failed",
        "source_count": len(sources),
        "verified_count": len(results),
        "downloaded_count": sum(
            result["pdf_status"] == "downloaded" for result in results
        ),
        "extracted_count": sum("text_path" in result for result in results),
        "failure_count": len(failures),
        "sources": results,
        "failures": failures,
    }


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Download, verify, and extract embedded text from registered "
            "APU brochures."
        )
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root containing config/elective_rules.json.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_RELATIVE_PATH,
        help="Configuration path relative to the repository root.",
    )
    parser.add_argument(
        "--text-directory",
        type=Path,
        default=TEXT_DIRECTORY_RELATIVE_PATH,
        help="Text output directory relative to the repository root.",
    )
    parser.add_argument(
        "--source-id",
        action="append",
        help="Process only this source id. Repeat to select multiple sources.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="HTTP timeout in seconds.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Redownload selected PDFs, keeping existing files until replacements verify.",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Verify existing PDFs without network access or text extraction.",
    )
    parser.add_argument(
        "--no-extract",
        action="store_true",
        help="Download and verify PDFs without extracting text.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_argument_parser()
    arguments = parser.parse_args(argv)
    if arguments.timeout <= 0:
        parser.error("--timeout must be greater than zero.")
    if arguments.verify_only and arguments.force:
        parser.error("--verify-only cannot be combined with --force.")

    try:
        summary = manage_brochures(
            repository_root=arguments.repository_root,
            config_relative_path=arguments.config,
            text_directory_relative_path=arguments.text_directory,
            source_ids=arguments.source_id,
            timeout_seconds=arguments.timeout,
            force=arguments.force,
            verify_only=arguments.verify_only,
            extract_text=not arguments.no_extract,
        )
    except BrochureError as exc:
        print(f"Brochure processing failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
