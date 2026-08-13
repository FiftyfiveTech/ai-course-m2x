"""Tests for replayable extraction fixtures.

The claim under test is narrow and load-bearing: **replay must score exactly what live
scored, or refuse.** Anything softer reintroduces the defect fixtures exist to remove — a
gate number that silently depends on which sampled outputs a machine happens to hold.

So the tests come in two halves. One half checks that a recorded run and a replayed run
agree. The other half checks the refusals, because a replay that quietly skips a case
reports a number over a smaller set while looking identical to one that did not.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from conftest import chat_response
from m2x.adapter import ModelAdapter
from m2x.errors import ConfigError
from m2x.eval_extraction import run_extraction_eval
from m2x.eval_fixtures import (
    FIXTURE_SCHEMA,
    STATUS_OK,
    STATUS_SCHEMA_FAILED,
    ExtractionFixture,
    FixtureMode,
    fixture_path,
    load_fixture,
    model_slug,
    save_fixture,
    transcript_digest,
)
from m2x.extraction import DEFAULT_EXTRACTION_PROMPT_VERSION, DEFAULT_EXTRACT_MODEL
from m2x.labels import LabelledCase, save_labelled_case
from m2x.schema import Decision, Evidence, MeetingRecord
from m2x.types import TranscriptSegment

CASE_ID = "ref-001-c01"


def _write_reference(directory: Path, meeting_id: str = "ref-001", count: int = 6) -> None:
    """Write a reference pair the cases can be sliced from.

    Args:
        directory: Reference directory, created if absent.
        meeting_id: Meeting the pair describes.
        count: How many turns.
    """
    directory.mkdir(parents=True, exist_ok=True)
    turns = [
        {"t_start": float(i), "t_end": float(i) + 0.5, "speaker": f"spk-{i % 2}"}
        for i in range(count)
    ]
    (directory / f"{meeting_id}.speakers.json").write_text(
        json.dumps({"meeting_id": meeting_id, "segments": turns}), encoding="utf-8"
    )
    (directory / f"{meeting_id}.txt").write_text(
        "\n".join(f"we agreed to ship release {i}" for i in range(count)) + "\n",
        encoding="utf-8",
    )


def _write_case(labels_dir: Path, description: str = "ship release one") -> None:
    """Write one labelled dev case.

    Args:
        labels_dir: Labels root.
        description: The single decision's description.
    """
    save_labelled_case(
        LabelledCase(
            case_id=CASE_ID,
            meeting_id="ref-001",
            first_turn=0,
            last_turn=2,
            label=MeetingRecord(
                decisions=[
                    Decision(
                        description=description,
                        evidence=Evidence(segment_id="seg-0001", t_start=0.0, t_end=0.5),
                    )
                ]
            ),
        ),
        labels_dir / "dev",
    )


def _reply(description: str = "ship release one") -> str:
    """A model reply carrying one decision.

    Args:
        description: The decision's description.

    Returns:
        The JSON body.
    """
    return json.dumps(
        {
            "decisions": [
                {
                    "description": description,
                    "evidence": {"segment_id": "seg-0001", "t_start": 0.0, "t_end": 0.5},
                }
            ],
            "actions": [],
            "risks": [],
            "open_questions": [],
        }
    )


def _handler(reply: str) -> Callable[[httpx.Request], httpx.Response]:
    """Mock transport returning one fixed record for every call.

    Args:
        reply: Body to return.

    Returns:
        The handler.
    """

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=chat_response(reply))

    return handle


def _corpus(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Build a reference, a label set and a fixtures root.

    Args:
        tmp_path: Test directory.

    Returns:
        ``(reference_dir, labels_dir, fixtures_dir)``.
    """
    _write_reference(tmp_path / "tiron")
    _write_case(tmp_path / "labels")
    return tmp_path / "tiron", tmp_path / "labels", tmp_path / "fixtures"


def _run(
    adapter: ModelAdapter,
    paths: tuple[Path, Path, Path],
    mode: FixtureMode,
) -> tuple[object, str]:
    """Run the eval over the temporary corpus in one fixture mode.

    Args:
        adapter: Adapter to extract with.
        paths: ``(reference_dir, labels_dir, fixtures_dir)``.
        mode: Fixture mode.

    Returns:
        ``(report, prompt_version)``.
    """
    reference_dir, labels_dir, fixtures_dir = paths
    return run_extraction_eval(
        "dev",
        adapter=adapter,
        labels_dir=labels_dir,
        reference_dir=reference_dir,
        fixtures=mode,
        fixtures_dir=fixtures_dir,
    )


def test_record_then_replay_gives_the_same_number(
    tmp_path: Path, make_adapter: Callable[..., ModelAdapter]
) -> None:
    """The whole point: a replayed run reproduces the recorded run's score."""
    paths = _corpus(tmp_path)

    recorded, _ = _run(make_adapter(_handler(_reply())), paths, FixtureMode.RECORD)
    replayed, _ = _run(make_adapter(_handler(_reply())), paths, FixtureMode.REPLAY)

    assert replayed.micro_f1 == pytest.approx(recorded.micro_f1)  # type: ignore[attr-defined]
    assert replayed.scored_case_ids == recorded.scored_case_ids  # type: ignore[attr-defined]


def test_replay_ignores_what_the_provider_would_say_now(
    tmp_path: Path, make_adapter: Callable[..., ModelAdapter]
) -> None:
    """Replay must not sample.

    The strongest available check that no provider call happens: record a good answer,
    then replay against a transport that would return a *wrong* one. If the score moves,
    replay reached the model.
    """
    paths = _corpus(tmp_path)
    _run(make_adapter(_handler(_reply("ship release one"))), paths, FixtureMode.RECORD)

    replayed, _ = _run(
        make_adapter(_handler(_reply("something else entirely"))), paths, FixtureMode.REPLAY
    )

    assert replayed.micro_f1 == pytest.approx(1.0)  # type: ignore[attr-defined]


def test_replay_refuses_a_missing_fixture(
    tmp_path: Path, make_adapter: Callable[..., ModelAdapter]
) -> None:
    """A case with no fixture must abort the run, not vanish from the denominator.

    Skipping would report a micro-F1 over a smaller set while printing the same shape of
    table — indistinguishable from a complete run, which is exactly the confusion the
    fixture set was built to end.
    """
    paths = _corpus(tmp_path)

    with pytest.raises(ConfigError, match="no fixture at"):
        _run(make_adapter(_handler(_reply())), paths, FixtureMode.REPLAY)


def test_replay_refuses_a_fixture_recorded_from_other_text(
    tmp_path: Path, make_adapter: Callable[..., ModelAdapter]
) -> None:
    """A reference edit must not silently rescore old answers against new words.

    Labels store bounds and re-derive their segments, so editing eval/tiron/ moves the
    transcript under a fixture without touching the fixture. The digest catches it.
    """
    reference_dir, labels_dir, fixtures_dir = _corpus(tmp_path)
    _run(make_adapter(_handler(_reply())), (reference_dir, labels_dir, fixtures_dir), FixtureMode.RECORD)

    (reference_dir / "ref-001.txt").write_text(
        "\n".join(f"we now say something different {i}" for i in range(6)) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="different transcript text"):
        _run(
            make_adapter(_handler(_reply())),
            (reference_dir, labels_dir, fixtures_dir),
            FixtureMode.REPLAY,
        )


def test_a_schema_failure_is_recorded_and_replays_as_a_failure(
    tmp_path: Path, make_adapter: Callable[..., ModelAdapter]
) -> None:
    """Schema validity is a gate leg, so failures have to survive the round trip.

    A fixture set holding only successes would replay 100% schema-valid by construction —
    a green gate leg manufactured by the recording process.
    """
    paths = _corpus(tmp_path)

    recorded, _ = _run(make_adapter(_handler("not JSON at all")), paths, FixtureMode.RECORD)
    replayed, _ = _run(make_adapter(_handler(_reply())), paths, FixtureMode.REPLAY)

    assert recorded.cases_schema_failed == 1  # type: ignore[attr-defined]
    assert replayed.cases_schema_failed == 1  # type: ignore[attr-defined]
    assert replayed.schema_failed_case_ids == [CASE_ID]  # type: ignore[attr-defined]
    assert replayed.schema_validity == pytest.approx(0.0)  # type: ignore[attr-defined]


def test_a_provider_failure_is_never_recorded(
    tmp_path: Path, make_adapter: Callable[..., ModelAdapter]
) -> None:
    """A 429 is a fact about a network, not an answer from a model.

    Freezing one would make a bad afternoon a permanent gate number. The case is left
    without a fixture instead, which replay then refuses to run past — loudly.
    """
    reference_dir, labels_dir, fixtures_dir = _corpus(tmp_path)

    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limited"})

    report, _ = _run(
        make_adapter(refuse), (reference_dir, labels_dir, fixtures_dir), FixtureMode.RECORD
    )

    assert report.cases_provider_failed == 1  # type: ignore[attr-defined]
    assert not list(fixtures_dir.rglob("*.json"))


def test_live_mode_writes_nothing(
    tmp_path: Path, make_adapter: Callable[..., ModelAdapter]
) -> None:
    """Iteration must not accumulate fixtures nobody asked for.

    Recording is a claim about what the gate will certify, so it stays an explicit act.
    """
    reference_dir, labels_dir, fixtures_dir = _corpus(tmp_path)

    _run(make_adapter(_handler(_reply())), (reference_dir, labels_dir, fixtures_dir), FixtureMode.LIVE)

    assert not fixtures_dir.exists()


def test_fixture_path_separates_prompt_versions_and_models() -> None:
    """Two prompts must not overwrite each other's recordings.

    Prompt version and model are the two things a Phase 1B number is *about*; sharing a
    filename between them would let a v3 recording be scored as a v6 result.
    """
    v3 = fixture_path(CASE_ID, prompt_version="v3", model_repo_id=DEFAULT_EXTRACT_MODEL)
    v6 = fixture_path(CASE_ID, prompt_version="v6", model_repo_id=DEFAULT_EXTRACT_MODEL)
    other = fixture_path(CASE_ID, prompt_version="v3", model_repo_id="qwen/Qwen3-8B")

    assert v3 != v6 != other
    assert v3.parts[-2] == model_slug(DEFAULT_EXTRACT_MODEL)
    assert "/" not in v3.parts[-2]


def test_replay_reads_the_pinned_prompt_version_by_default(
    tmp_path: Path, make_adapter: Callable[..., ModelAdapter]
) -> None:
    """An unpinned run must record and replay under the same directory.

    If record resolved the version one way and replay another, every unpinned replay
    would report "no fixture" against files sitting right there.
    """
    reference_dir, labels_dir, fixtures_dir = _corpus(tmp_path)

    _run(make_adapter(_handler(_reply())), (reference_dir, labels_dir, fixtures_dir), FixtureMode.RECORD)

    assert (fixtures_dir / DEFAULT_EXTRACTION_PROMPT_VERSION).is_dir()


def test_save_refuses_an_ok_fixture_with_no_outcome(tmp_path: Path) -> None:
    """A fixture that scores nothing and fails nothing would vanish from both counts."""
    with pytest.raises(ValueError, match="no outcome"):
        save_fixture(
            ExtractionFixture(
                case_id=CASE_ID, status=STATUS_OK, transcript_sha256="0" * 64
            ),
            tmp_path / "x.json",
        )


def test_save_refuses_a_failure_fixture_with_no_reason(tmp_path: Path) -> None:
    """A recorded failure that cannot say what failed is not evidence of anything."""
    with pytest.raises(ValueError, match="must say what failed"):
        save_fixture(
            ExtractionFixture(
                case_id=CASE_ID, status=STATUS_SCHEMA_FAILED, transcript_sha256="0" * 64
            ),
            tmp_path / "x.json",
        )


def test_load_refuses_a_stale_schema_version(tmp_path: Path) -> None:
    """A fixture from another layout is not evidence about this one."""
    path = tmp_path / "x.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": FIXTURE_SCHEMA + 1,
                "case_id": CASE_ID,
                "status": STATUS_SCHEMA_FAILED,
                "transcript_sha256": "0" * 64,
                "failure": "whatever",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="re-record"):
        load_fixture(path)


def test_transcript_digest_tracks_the_rendered_text() -> None:
    """The digest is over what the model saw, not over the objects behind it.

    Rendering fixes ids, timestamps and truncation; two segment lists that render the same
    are the same input as far as the model is concerned, and must hash the same.
    """
    one = [TranscriptSegment(text="we agreed", t_start=0.0, t_end=1.0, speaker="a")]
    same = [TranscriptSegment(text="we agreed", t_start=0.0, t_end=1.0, speaker="a")]
    different = [TranscriptSegment(text="we disagreed", t_start=0.0, t_end=1.0, speaker="a")]

    assert transcript_digest(one) == transcript_digest(same)
    assert transcript_digest(one) != transcript_digest(different)
