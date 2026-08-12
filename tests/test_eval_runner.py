"""Tests for the eval runner and its results record.

Separate from ``test_eval_extraction.py`` on purpose: that file tests the arithmetic and
needs no adapter, this one tests the parts that touch a model, the filesystem and the
clock. Keeping them apart is what lets the scoring tests stay fast and mock-free.

No network and no real clock here either — the adapter is backed by a mock transport and
``now`` is injected, per the project's testing rules.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from conftest import chat_response
from m2x.adapter import ModelAdapter
from m2x.eval_extraction import (
    append_result,
    aggregate,
    run_extraction_eval,
    score_case,
)
from m2x.labels import LabelledCase, save_labelled_case
from m2x.schema import Decision, Evidence, MeetingRecord

FIXED_TIME = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)


def _write_reference(directory: Path, meeting_id: str = "ref-001", count: int = 6) -> None:
    """Write a reference pair the cases can be sliced from."""
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


def _write_case(labels_dir: Path, set_name: str, description: str) -> None:
    """Write one labelled case into a set."""
    save_labelled_case(
        LabelledCase(
            case_id="ref-001-c01",
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
        labels_dir / set_name,
    )


def _record_reply(description: str) -> str:
    """A model reply carrying one decision."""
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
    """Mock transport returning one fixed record for every call."""

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=chat_response(reply))

    return handle


def test_a_matching_extraction_scores_perfectly(
    tmp_path: Path, make_adapter: Callable[..., ModelAdapter]
) -> None:
    """End to end: label in, extraction out, score computed."""
    _write_reference(tmp_path / "tiron")
    _write_case(tmp_path / "labels", "dev", "ship release one")
    adapter = make_adapter(_handler(_record_reply("ship release one")))

    report, _ = run_extraction_eval(
        "dev",
        adapter=adapter,
        labels_dir=tmp_path / "labels",
        reference_dir=tmp_path / "tiron",
    )

    assert report.cases_scored == 1
    assert report.cases_failed == 0
    assert report.micro_f1 == pytest.approx(1.0)


def test_a_case_that_never_validates_is_counted_not_raised(
    tmp_path: Path, make_adapter: Callable[..., ModelAdapter]
) -> None:
    """One unparseable case must not cost the rest of the set.

    The gate needs the failure *count*; aborting the run would leave it unknown and
    throw away the cases that did work.
    """
    _write_reference(tmp_path / "tiron")
    _write_case(tmp_path / "labels", "dev", "ship release one")
    adapter = make_adapter(_handler("this is not JSON at all"))

    report, _ = run_extraction_eval(
        "dev",
        adapter=adapter,
        labels_dir=tmp_path / "labels",
        reference_dir=tmp_path / "tiron",
    )

    assert report.cases_scored == 0
    assert report.cases_failed == 1
    assert report.schema_validity == pytest.approx(0.0)


def test_a_missing_set_names_the_directory(
    tmp_path: Path, make_adapter: Callable[..., ModelAdapter]
) -> None:
    """On a fresh clone `heldout` is absent by design, so the error explains itself."""
    adapter = make_adapter(_handler(_record_reply("anything")))

    with pytest.raises(FileNotFoundError, match="heldout"):
        run_extraction_eval(
            "heldout",
            adapter=adapter,
            labels_dir=tmp_path / "labels",
            reference_dir=tmp_path / "tiron",
        )


def test_extraction_runs_against_the_case_not_the_whole_meeting(
    tmp_path: Path, make_adapter: Callable[..., ModelAdapter]
) -> None:
    """The prompt must contain only the case's turns, or citations resolve wrongly."""
    _write_reference(tmp_path / "tiron", count=6)
    _write_case(tmp_path / "labels", "dev", "ship release one")
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.content.decode("utf-8"))
        return httpx.Response(200, json=chat_response(_record_reply("ship release one")))

    adapter = make_adapter(handle)
    run_extraction_eval(
        "dev",
        adapter=adapter,
        labels_dir=tmp_path / "labels",
        reference_dir=tmp_path / "tiron",
    )

    # The case covers turns 0-2 inclusive, so it renders exactly seg-0001..seg-0003.
    # Turn 3 and everything after it must never reach the model: the ids are positional
    # within the case, so a leaked extra turn would shift what every citation means.
    assert "seg-0003" in seen[0]
    assert "seg-0004" not in seen[0]
    assert "release 3" not in seen[0]


def test_result_records_the_prompt_version_and_sha(tmp_path: Path) -> None:
    """A number that cannot name what produced it is a rumour."""
    labelled = MeetingRecord(
        decisions=[
            Decision(
                description="ship it",
                evidence=Evidence(segment_id="seg-0001", t_start=0.0, t_end=1.0),
            )
        ]
    )
    report = aggregate("dev", [score_case("c01", labelled, labelled)])
    path = tmp_path / "results" / "extraction.jsonl"

    append_result(
        report,
        prompt_version="v3",
        model_repo_id="meta-llama/Llama-3.1-8B-Instruct",
        path=path,
        git_sha="abc1234",
        now=lambda: FIXED_TIME,
    )

    record = json.loads(path.read_text(encoding="utf-8").strip())
    assert record["prompt_version"] == "v3"
    assert record["git_sha"] == "abc1234"
    assert record["set"] == "dev"
    assert record["micro_f1"] == 1.0
    assert "deadline excluded from micro_f1" in record["note"]


def test_results_are_append_only(tmp_path: Path) -> None:
    """History is the point: a later run must not overwrite an earlier number."""
    labelled = MeetingRecord()
    report = aggregate("dev", [score_case("c01", labelled, labelled)])
    path = tmp_path / "results" / "extraction.jsonl"

    for version in ("v1", "v2"):
        append_result(
            report,
            prompt_version=version,
            model_repo_id="m",
            path=path,
            git_sha="abc1234",
            now=lambda: FIXED_TIME,
        )

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [row["prompt_version"] for row in rows] == ["v1", "v2"]
