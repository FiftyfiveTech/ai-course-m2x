"""Prompts as versioned artefacts, read from disk by name and version.

A prompt is an *input* to a run, exactly like the model repo id and the transcript, and
the last of the three that was still a Python string literal. Moving it into
``prompts/<name>/v<N>.md`` buys one thing: every number the project reports can name the
prompt that produced it, because the version is stamped into the record's metadata and
onto every run-log line (see :mod:`m2x.run_log`) and described in
``prompts/CHANGELOG.md``. Two of those three agreeing is the dangerous state — it looks
audited and is not — so all three are written from the single version resolved here.

**Versions are append-only.** Once a score has been reported against ``v1``, editing
``v1`` falsifies every record and log line that names it. A changed prompt is ``v2``.
The rule is convention plus review, with one mechanical assist: ``tests/test_prompts.py``
fails when a version file's checksum does not match its ``CHANGELOG.md`` row, so an
in-place edit cannot pass silently.

**Rendering is strict in both directions**, which is the non-obvious part. An
unsubstituted placeholder is an obvious bug; the dangerous one is the *silent drop* — a
new version renames ``{{transcript}}``, the caller still passes ``transcript=``, and the
model is handed an empty transcript block. It answers with an empty record, which is a
perfectly valid :class:`~m2x.schema.MeetingRecord`, and the eval blames the model for an
F1 of zero. So a placeholder with no value and a value with no placeholder are both
errors.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from m2x.errors import ConfigError

DEFAULT_PROMPTS_DIR = Path("prompts")
"""Root of the prompt library, resolved from the working directory.

Relative for the same reason ``config/models.toml`` is: both are tracked files a
supervisor needs to reproduce a number, and both are found by running from the
repository root.
"""

VERSION_PATTERN = re.compile(r"^v(\d+)$")
"""A version is ``v`` followed by an integer. Ordering is numeric, so ``v10 > v9``."""

SECTION_PATTERN = re.compile(r"^##[ \t]+(\S+)[ \t]*$", re.MULTILINE)
"""A section heading. Text before the first one is prose for humans and is ignored."""

PLACEHOLDER_PATTERN = re.compile(r"\{\{(\w+)\}\}")
"""``{{name}}``. Double braces so JSON and schema examples in a prompt stay literal."""

SYSTEM_SECTION = "system"
USER_SECTION = "user"


class Prompt(BaseModel):
    """One version of one prompt, parsed and ready to render."""

    model_config = ConfigDict(frozen=True)

    name: str
    """Library name, e.g. ``"extraction"`` — the directory under the prompts root."""

    version: str
    """Version id as written on disk, e.g. ``"v1"``. This is what gets stamped."""

    system: str
    """The system message, verbatim."""

    user_template: str
    """The user message, with ``{{placeholder}}`` slots still in place."""

    path: Path
    """File this was read from, so an error can name it."""

    @property
    def content_digest(self) -> str:
        """sha256 over the text the model actually sees.

        Over ``system`` and ``user_template`` rather than the file bytes, so the prose
        above the first heading can be corrected without pretending the prompt changed —
        while any edit to a word the model reads, whitespace included, moves the digest.
        ``prompts/CHANGELOG.md`` records this per version and a test compares them, which
        is what turns "never edit a cited version in place" into a failing test rather
        than a hope.
        """
        return hashlib.sha256(f"{self.system}\0{self.user_template}".encode("utf-8")).hexdigest()

    def render_user(self, **values: str) -> str:
        """Substitute the template's placeholders.

        Args:
            **values: One keyword per placeholder in the template.

        Returns:
            The user message with every placeholder replaced.

        Raises:
            ConfigError: A placeholder had no value, or a value named no placeholder.
                Both directions are errors: the second is what catches a renamed
                placeholder quietly emptying the prompt.
        """
        wanted = set(PLACEHOLDER_PATTERN.findall(self.user_template))
        supplied = set(values)
        missing = sorted(wanted - supplied)
        unused = sorted(supplied - wanted)

        if missing or unused:
            # A rename trips both halves at once, and both are worth printing: one says
            # what the template is waiting for, the other says what was thrown away.
            faults = []
            if missing:
                faults.append(f"no value supplied for placeholder(s) {missing}")
            if unused:
                faults.append(f"{unused} passed but the template has no such placeholder")
            raise ConfigError(
                f"{self.path}: {'; '.join(faults)}; it expects {sorted(wanted)}"
            )

        return PLACEHOLDER_PATTERN.sub(lambda match: values[match.group(1)], self.user_template)


def _sections(text: str) -> dict[str, str]:
    """Split a prompt file into its ``## <name>`` sections.

    Args:
        text: Whole file contents.

    Returns:
        ``{section_name: body}``, bodies stripped of surrounding blank lines. Text
        before the first heading is dropped — that is where the file explains itself.
    """
    matches = list(SECTION_PATTERN.finditer(text))
    bodies: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        bodies[match.group(1).lower()] = text[match.end() : end].strip()
    return bodies


def available_versions(name: str, *, prompts_dir: Path = DEFAULT_PROMPTS_DIR) -> list[str]:
    """List the versions of a prompt, oldest first.

    Args:
        name: Prompt library name.
        prompts_dir: Root of the library.

    Returns:
        Version ids ordered numerically, e.g. ``["v1", "v2", "v10"]``. Empty when the
        directory exists but holds no version files.

    Raises:
        ConfigError: The prompt directory does not exist.
    """
    directory = prompts_dir / name
    if not directory.is_dir():
        raise ConfigError(
            f"No prompt named {name!r} under {prompts_dir}. "
            "Run from the repository root, or pass a prompts directory."
        )

    numbered: list[tuple[int, str]] = []
    for path in directory.glob("*.md"):
        match = VERSION_PATTERN.match(path.stem)
        if match:
            numbered.append((int(match.group(1)), path.stem))
    return [version for _, version in sorted(numbered)]


def latest_version(name: str, *, prompts_dir: Path = DEFAULT_PROMPTS_DIR) -> str:
    """Return the highest version present.

    This is what makes a new version a zero-code-change switch: shipping ``v2.md`` moves
    every unpinned caller onto it. It stays reproducible because prompts are tracked —
    a fresh clone at a given commit sees exactly one highest version. It is the commit
    that pins a run, never the word "latest".

    Args:
        name: Prompt library name.
        prompts_dir: Root of the library.

    Returns:
        The highest version id.

    Raises:
        ConfigError: The prompt has no versions at all.
    """
    versions = available_versions(name, prompts_dir=prompts_dir)
    if not versions:
        raise ConfigError(f"Prompt {name!r} has no versions in {prompts_dir / name}")
    return versions[-1]


def load_prompt(
    name: str,
    version: str | None = None,
    *,
    prompts_dir: Path = DEFAULT_PROMPTS_DIR,
) -> Prompt:
    """Read one prompt version off disk.

    Args:
        name: Prompt library name, e.g. ``"extraction"``.
        version: Version id, e.g. ``"v1"``. ``None`` takes the latest.
        prompts_dir: Root of the library.

    Returns:
        The parsed prompt.

    Raises:
        ConfigError: The prompt, the version, or a required section is missing, or the
            file cannot be read. A prompt that half-loads would send the model a
            truncated instruction and score it, so nothing here is best-effort.
    """
    resolved = version or latest_version(name, prompts_dir=prompts_dir)
    if not VERSION_PATTERN.match(resolved):
        raise ConfigError(f"{resolved!r} is not a version id; expected the form 'v1'")

    path = prompts_dir / name / f"{resolved}.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        known = ", ".join(available_versions(name, prompts_dir=prompts_dir)) or "none"
        raise ConfigError(f"Cannot read prompt {name}/{resolved}: {exc}. Known: {known}") from exc

    bodies = _sections(text)
    missing = [section for section in (SYSTEM_SECTION, USER_SECTION) if not bodies.get(section)]
    if missing:
        raise ConfigError(f"{path} is missing a non-empty '## {missing[0]}' section")

    return Prompt(
        name=name,
        version=resolved,
        system=bodies[SYSTEM_SECTION],
        user_template=bodies[USER_SECTION],
        path=path,
    )
