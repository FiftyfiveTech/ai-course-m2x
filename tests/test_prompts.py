"""Prompt-library tests: parsing, version resolution, and the integrity rule.

Two kinds of test live here. Most build a throwaway library under ``tmp_path``, because
the parser's behaviour should not depend on what the repo currently ships. The last two
deliberately do the opposite and read the *tracked* ``prompts/`` tree: they are what
enforces "a cited version is never edited in place", and a copy of the tree would
enforce nothing.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from m2x.errors import ConfigError
from m2x.prompts import (
    available_versions,
    latest_version,
    load_prompt,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
TRACKED_PROMPTS = REPO_ROOT / "prompts"
CHANGELOG = TRACKED_PROMPTS / "CHANGELOG.md"


def write_prompt(root: Path, name: str, version: str, *, system: str = "be terse", user: str = "{{transcript}}") -> Path:
    """Write one prompt version into a throwaway library."""
    path = root / name / f"{version}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"prose the loader ignores\n\n## system\n\n{system}\n\n## user\n\n{user}\n", encoding="utf-8")
    return path


def test_sections_parse_and_the_preamble_is_dropped(tmp_path: Path) -> None:
    write_prompt(tmp_path, "extraction", "v1", system="you extract", user="<t>\n{{transcript}}\n</t>")

    prompt = load_prompt("extraction", "v1", prompts_dir=tmp_path)

    assert prompt.system == "you extract"
    assert prompt.user_template == "<t>\n{{transcript}}\n</t>"
    assert "prose the loader ignores" not in prompt.system


def test_versions_order_numerically_not_lexically(tmp_path: Path) -> None:
    """``v10`` is newer than ``v9``; string sorting says otherwise and would pin the old one."""
    for version in ("v1", "v9", "v10"):
        write_prompt(tmp_path, "extraction", version)

    assert available_versions("extraction", prompts_dir=tmp_path) == ["v1", "v9", "v10"]
    assert latest_version("extraction", prompts_dir=tmp_path) == "v10"


def test_no_version_given_loads_the_latest(tmp_path: Path) -> None:
    """Shipping a new version file is the whole switching mechanism — no code changes."""
    write_prompt(tmp_path, "extraction", "v1", system="first")
    assert load_prompt("extraction", prompts_dir=tmp_path).system == "first"

    write_prompt(tmp_path, "extraction", "v2", system="second")
    loaded = load_prompt("extraction", prompts_dir=tmp_path)

    assert loaded.version == "v2"
    assert loaded.system == "second"


def test_an_old_version_stays_loadable_after_a_newer_one_lands(tmp_path: Path) -> None:
    """Re-running a reported number means loading the prompt it was reported with."""
    write_prompt(tmp_path, "extraction", "v1", system="first")
    write_prompt(tmp_path, "extraction", "v2", system="second")

    assert load_prompt("extraction", "v1", prompts_dir=tmp_path).system == "first"


def test_unknown_prompt_names_the_directory(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="No prompt named 'nope'"):
        load_prompt("nope", prompts_dir=tmp_path)


def test_unknown_version_lists_the_known_ones(tmp_path: Path) -> None:
    write_prompt(tmp_path, "extraction", "v1")

    with pytest.raises(ConfigError, match="Known: v1"):
        load_prompt("extraction", "v7", prompts_dir=tmp_path)


def test_a_malformed_version_id_is_rejected(tmp_path: Path) -> None:
    write_prompt(tmp_path, "extraction", "v1")

    with pytest.raises(ConfigError, match="not a version id"):
        load_prompt("extraction", "latest", prompts_dir=tmp_path)


def test_a_missing_section_is_an_error_not_an_empty_prompt(tmp_path: Path) -> None:
    """Half a prompt would still get sent, and the answer would still be scored."""
    path = tmp_path / "extraction" / "v1.md"
    path.parent.mkdir(parents=True)
    path.write_text("## system\n\nyou extract\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="## user"):
        load_prompt("extraction", "v1", prompts_dir=tmp_path)


def test_render_substitutes_the_placeholder(tmp_path: Path) -> None:
    write_prompt(tmp_path, "extraction", "v1", user="<t>\n{{transcript}}\n</t>")
    prompt = load_prompt("extraction", "v1", prompts_dir=tmp_path)

    assert prompt.render_user(transcript="seg-0001 we ship") == "<t>\nseg-0001 we ship\n</t>"


def test_a_placeholder_with_no_value_is_an_error(tmp_path: Path) -> None:
    write_prompt(tmp_path, "extraction", "v1", user="{{transcript}} {{meeting_date}}")
    prompt = load_prompt("extraction", "v1", prompts_dir=tmp_path)

    with pytest.raises(ConfigError, match="meeting_date"):
        prompt.render_user(transcript="x")


def test_a_value_with_no_placeholder_is_an_error(tmp_path: Path) -> None:
    """The silent drop: a renamed placeholder would otherwise send an empty transcript.

    The model answers an empty prompt with an empty record, which validates, and the
    eval reports 0.0 against the model rather than against the rename.
    """
    write_prompt(tmp_path, "extraction", "v1", user="<t>\n{{meeting}}\n</t>")
    prompt = load_prompt("extraction", "v1", prompts_dir=tmp_path)

    with pytest.raises(ConfigError, match="no such placeholder"):
        prompt.render_user(transcript="x")


def changelog_digests() -> dict[tuple[str, str], str]:
    """Parse ``prompts/CHANGELOG.md`` into ``{(prompt_name, version): digest}``.

    Sections are ``## <name>``; rows are ``| v1 | date | `digest` | … |``.
    """
    digests: dict[tuple[str, str], str] = {}
    name = ""
    for line in CHANGELOG.read_text(encoding="utf-8").splitlines():
        heading = re.match(r"^##[ \t]+(\S+)[ \t]*$", line)
        if heading:
            name = heading.group(1)
            continue
        row = re.match(r"^\|\s*(v\d+)\s*\|[^|]*\|\s*`([0-9a-f]+)`\s*\|", line)
        if row and name:
            digests[(name, row.group(1))] = row.group(2)
    return digests


@pytest.mark.parametrize(
    "name,version",
    sorted(
        (directory.name, path.stem)
        for directory in TRACKED_PROMPTS.iterdir()
        if directory.is_dir()
        for path in directory.glob("v*.md")
    ),
)
def test_every_tracked_version_matches_its_changelog_digest(name: str, version: str) -> None:
    """A cited version is never edited in place — this is the check that says so.

    Fails in two directions: a version with no changelog row (undocumented), and a
    version whose text has moved since its row was written (edited in place). Either way
    the fix is a new version file plus a new row, never a quiet edit.
    """
    prompt = load_prompt(name, version, prompts_dir=TRACKED_PROMPTS)
    recorded = changelog_digests().get((name, version))

    assert recorded is not None, (
        f"prompts/{name}/{version}.md has no row in prompts/CHANGELOG.md. "
        f"Add one with digest `{prompt.content_digest[:12]}`."
    )
    assert prompt.content_digest.startswith(recorded), (
        f"prompts/{name}/{version}.md no longer matches its CHANGELOG digest "
        f"({recorded}). A cited version is append-only: add the next version instead. "
        f"If this version was never cited, its digest is now `{prompt.content_digest[:12]}`."
    )


def test_every_changelog_row_has_a_version_file() -> None:
    """A row with no file is a version somebody can cite and nobody can load."""
    for (name, version) in changelog_digests():
        assert (TRACKED_PROMPTS / name / f"{version}.md").is_file(), (
            f"prompts/CHANGELOG.md lists {name} {version}, which is not on disk"
        )


def test_the_digest_covers_the_prompt_text_and_not_the_prose(tmp_path: Path) -> None:
    """Correcting the human-facing preamble must not read as a prompt change."""
    path = write_prompt(tmp_path, "extraction", "v1", system="you extract")
    before = load_prompt("extraction", "v1", prompts_dir=tmp_path).content_digest

    path.write_text(path.read_text(encoding="utf-8").replace("prose the loader ignores", "clearer prose"), encoding="utf-8")
    after = load_prompt("extraction", "v1", prompts_dir=tmp_path)

    assert after.content_digest == before
    assert (
        after.content_digest
        == hashlib.sha256(b"you extract\x00{{transcript}}").hexdigest()
    )
