"""Frozen extraction outcomes, so a gate number is a property of the repository.

`docs/gates.md` says a gate counts only when the supervisor re-runs the command on a
fresh clone and sees the same output. Phase 1B could not meet that. The same prompt, the
same commit, the same matcher and the same fifteen cases scored **0.3086** here and
**0.4279** on the supervisor's run at `3cc59a8` — the only difference being which sampled
model outputs each checkout's `data/cache/` happened to hold. `temperature` is already
0.0; no seed is sent; `data/` is git-ignored, so a fresh clone starts empty and samples
afresh. The number was reproducible only by accident.

A fixture is one case's extraction, recorded once and committed. Replaying scores the
*same* model outputs on every machine, so the reported F1 becomes a fact about tracked
files rather than about a cache directory that no two people share.

## What replay does and does not reproduce

It reproduces **scoring**: matching, thresholds, per-field and micro F1, schema validity,
the case set behind the denominator. Given the fixtures and the labels, two clones cannot
disagree.

It does not reproduce **sampling**. A fixture freezes one draw from the model, chosen at
record time. That is the point rather than a limitation — a gate needs one auditable
number, not a fresh sample per reviewer — but it means the gate certifies *that draw*.
Re-recording is an explicit act (`--fixtures record`) that shows up as a diff, which is
the property the cache never had.

## Three integrity rules

1. **Provider failures are never recorded.** A 429 is a fact about a network, not an
   answer from a model, and freezing one into the gate would let a bad afternoon become a
   permanent score. A case whose recording hit the provider simply has no fixture, and
   replay then refuses to run rather than quietly shrinking the denominator.
2. **Schema failures *are* recorded.** 100% schema validity is a gate leg, so a fixture
   set holding only successes would report it green by construction. A case the model
   could not produce a valid record for is recorded as exactly that.
3. **Every fixture carries the digest of the transcript it was produced from.** Labels
   store bounds and re-derive their segments from `eval/tiron/`, so a reference edit moves
   the words under a fixture recorded from the old ones. The digest turns that into a loud
   failure instead of a score nobody can explain.
"""

from __future__ import annotations

import hashlib
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from m2x.errors import ConfigError
from m2x.extraction import ExtractionOutcome, TranscriptSource, render_transcript

DEFAULT_FIXTURES_DIR = Path("eval/fixtures/extraction")
"""Tracked, deliberately outside git-ignored ``data/`` — the whole point is that a fresh
clone has them."""

FIXTURE_SCHEMA = 1
"""Bumped when the layout changes, so an old fixture fails loudly instead of scoring."""

STATUS_OK = "ok"
"""The model produced a valid record."""

STATUS_SCHEMA_FAILED = "schema"
"""The model never produced a valid record within the attempt budget."""


class FixtureMode(str, Enum):
    """How an eval run relates to the fixture set.

    Three modes rather than a boolean because they are three different claims: ``LIVE``
    measures the model now, ``RECORD`` decides what the gate will certify, and ``REPLAY``
    certifies it. Conflating record and replay is how a "reproducible" number ends up
    re-sampling on the reviewer's machine.
    """

    LIVE = "off"
    """Call the provider, write nothing. The default, and what iteration wants."""

    RECORD = "record"
    """Call the provider and freeze each outcome. An explicit, reviewable act."""

    REPLAY = "replay"
    """Score the committed outcomes. No provider is contacted; none needs to exist."""


def transcript_digest(source: TranscriptSource, *, char_limit: int | None = None) -> str:
    """Hash the exact text an extraction saw.

    The *rendered* block, not the raw segments: rendering is what fixes segment ids,
    timestamps and truncation, and two segment lists that render identically are the same
    input as far as the model is concerned.

    Args:
        source: Transcript, or segments, the case is cut from.
        char_limit: Rendering budget. ``None`` uses :func:`~m2x.extraction.render_transcript`'s
            default, which is what an unpinned extraction uses.

    Returns:
        Lowercase hex SHA-256 of the rendered block.
    """
    block, _ = render_transcript(source) if char_limit is None else render_transcript(source, char_limit=char_limit)
    return hashlib.sha256(block.encode("utf-8")).hexdigest()


class ExtractionFixture(BaseModel):
    """One case's frozen extraction.

    Attributes:
        schema_version: Layout version, checked on load.
        case_id: Case this fixture answers.
        status: :data:`STATUS_OK` or :data:`STATUS_SCHEMA_FAILED`.
        transcript_sha256: Digest of the rendered transcript it was produced from.
        outcome: The extraction, present only when ``status`` is ok.
        failure: Why the model failed, present only when ``status`` is a schema failure.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: int = FIXTURE_SCHEMA
    case_id: str = Field(min_length=1)
    status: str
    transcript_sha256: str = Field(min_length=64, max_length=64)
    outcome: ExtractionOutcome | None = None
    failure: str | None = None

    @property
    def succeeded(self) -> bool:
        """Whether this fixture holds a scoreable record.

        Returns:
            True when the model produced a valid record.
        """
        return self.status == STATUS_OK


def model_slug(model_repo_id: str) -> str:
    """Turn a Hugging Face repo id into one path component.

    Args:
        model_repo_id: e.g. ``meta-llama/Llama-3.1-8B-Instruct``.

    Returns:
        e.g. ``meta-llama--Llama-3.1-8B-Instruct``.
    """
    return model_repo_id.replace("/", "--")


def fixture_path(
    case_id: str,
    *,
    prompt_version: str,
    model_repo_id: str,
    fixtures_dir: Path = DEFAULT_FIXTURES_DIR,
) -> Path:
    """Locate one fixture.

    The prompt version and the model are directory components rather than fields inside
    the file, so two prompts cannot overwrite each other's recordings and a reviewer can
    see at a glance what was recorded against what.

    Args:
        case_id: Case id.
        prompt_version: Prompt version the recording used, e.g. ``v3``.
        model_repo_id: Extracting model.
        fixtures_dir: Root of the fixture set.

    Returns:
        The path, which may not exist.
    """
    return fixtures_dir / prompt_version / model_slug(model_repo_id) / f"{case_id}.json"


def save_fixture(fixture: ExtractionFixture, path: Path) -> Path:
    """Write one fixture, creating its directories.

    Args:
        fixture: Fixture to persist.
        path: Destination, from :func:`fixture_path`.

    Returns:
        The path written.

    Raises:
        ValueError: The fixture holds neither a record nor a failure, which would replay
            as a case that silently contributes nothing.
        OSError: The file could not be written.
    """
    if fixture.succeeded and fixture.outcome is None:
        raise ValueError(f"{fixture.case_id}: status ok with no outcome to score")
    if not fixture.succeeded and not fixture.failure:
        raise ValueError(f"{fixture.case_id}: a failure fixture must say what failed")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fixture.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def load_fixture(path: Path, *, expected_digest: str | None = None) -> ExtractionFixture:
    """Read one fixture and check it still answers the same question.

    Args:
        path: Fixture file.
        expected_digest: Digest of the transcript the caller is about to score against.
            ``None`` skips the check, which only a tool inspecting fixtures should do.

    Returns:
        The validated fixture.

    Raises:
        ConfigError: The fixture is missing, was written by another schema version, or was
            recorded from different transcript text. All three are loud on purpose: a
            missing fixture would shrink the gate's denominator without saying so, and a
            stale one would score a model's answer against words it never read.
        pydantic.ValidationError: The file is not a valid fixture.
        OSError: The file could not be read.
    """
    if not path.exists():
        raise ConfigError(
            f"no fixture at {path}. Replay refuses to skip a case — a missing fixture "
            "would move the micro-F1 denominator invisibly. Re-record with "
            "`m2x eval extraction --fixtures record`."
        )
    fixture = ExtractionFixture.model_validate_json(path.read_text(encoding="utf-8"))
    if fixture.schema_version != FIXTURE_SCHEMA:
        raise ConfigError(
            f"{path} has schema {fixture.schema_version}, this build reads {FIXTURE_SCHEMA} "
            "— re-record rather than scoring a layout nobody checked"
        )
    if expected_digest is not None and fixture.transcript_sha256 != expected_digest:
        raise ConfigError(
            f"{path} was recorded from different transcript text "
            f"({fixture.transcript_sha256[:12]}… vs {expected_digest[:12]}…). The "
            "reference under eval/tiron/ changed after this fixture was recorded, so the "
            "model's answer no longer corresponds to the words being scored."
        )
    return fixture
