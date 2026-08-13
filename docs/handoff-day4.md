# Handoff — starting Day 4

Written at the close of Day 3 for whoever picks up Day 4, assuming no memory of the
sessions that produced it. Everything here is verifiable from the repo; where something
is a judgement or a recommendation it says so.

---

## 1. The one thing to decide first

**Phase 1B is failing, and M2X-040 opens a set that can only be spent once.**

| gate leg | required | actual |
|---|---|---|
| held-out F1 | ≥ 0.85 | dev best **0.3645** |
| schema-valid | 10/10 | 14/15 on dev |
| injections | 3/3 | **1/3** |

M2X-040 unseals the 10 held-out cases, certifies exactly one run, and **burns the set**.
Running it now spends the only certification available on a configuration already known to
fail, and leaves nothing to certify the fix.

**Recommendation, posted on ticket 4916: do not unseal yet.** Fix first, then gate.

Note the awkward dependency this creates. M2X-041 (recovery) is written as *"ONLY if
M2X-040 fails"* — so following the tickets literally means burning the set to unlock the
ticket that repairs it. The substance of M2X-041 (dev error analysis, systematic fixes,
5 fresh sealed cases) is what Phase 1B needs *before* the gate, not after. That is a real
conflict between two tickets and it is the call to make on day 4 morning.

---

## 2. Where Day 3 ended

All five Day 3 tickets are complete and in **Done**.

| ticket | PR | state |
|---|---|---|
| M2X-030 schema freeze | [#27](https://github.com/FiftyfiveTech/ai-course-m2x/pull/27) | merged |
| M2X-033 ground truth | [#29](https://github.com/FiftyfiveTech/ai-course-m2x/pull/29) | merged |
| M2X-034 F1 harness | [#30](https://github.com/FiftyfiveTech/ai-course-m2x/pull/30) | merged |
| M2X-035 injection suite | [#31](https://github.com/FiftyfiveTech/ai-course-m2x/pull/31) | **open** |
| M2X-036 dev iteration | [#33](https://github.com/FiftyfiveTech/ai-course-m2x/pull/33) | **open** |

**#31 and #33 are unmerged.** The tickets sit in Done anyway (moved by someone other than
the agent that wrote them). Day 4 work that touches `eval/` should branch from #33's head,
not from `main`, or it will be missing the metric change described next.

527 tests green on `feature/m2x-036-dev-iteration`.

---

## 3. Contract changes Day 4 inherits

### The matching rule was replaced (this is the big one)

M2X-030 froze **token-set F1 ≥ 0.60**. M2X-036 replaced it with **embedding cosine
≥ 0.675** after showing it measured phrasing rather than agreement: five of seventy-two
labelled items found any candidate above threshold, while the band beneath was full of
pairs any reader calls identical.

Consequences that matter on Day 4:

- **A micro-F1 under the old rule and one under the new rule are different quantities.**
  Never put them in one series. Results rows carry `similarity`, `match_threshold` and
  `embed_model_repo_id` so they can be told apart; `--similarity lexical` reproduces the
  old rule.
- The threshold was calibrated on fifteen pairs judged SAME/DIFFERENT **by reading them,
  before any cosine was computed**. Lowest SAME 0.6928, highest DIFFERENT 0.6586,
  threshold the midpoint. **The gap is 0.034 wide on fifteen pairs — separation, not
  comfort.** Re-deriving it on more pairs is a genuine improvement someone could make.
- Containment was evaluated and **disqualified**: a one-word fragment scores 1.00 against
  any item containing that word. Do not revisit it without reading
  `docs/design/day3-iteration.md` first.

### `deadline` is reported, never scored

The ground truth contains **zero** deadlines — every deadline spoken in the corpus is
relative and no tiron meeting has a date to resolve against. The field has no positive
examples, so it is reported as an abstention rate and excluded from micro-F1.

### Two caveats that apply to every Phase 1B number

1. **Labels share an author with the prompt and schema.** Every F1 is an **upper bound**,
   not an independent measurement. This is by explicit decision, recorded in
   `eval/labels/README.md`.
2. **The held-out seal is convention, not encryption.** Plaintext under
   `eval/labels/heldout/` is git-ignored and never committed, so **a fresh clone has no
   held-out set and cannot reproduce the M2X-040 number** — which `CLAUDE.md` otherwise
   requires of any gate. Resolve before treating the gate as certified.

---

## 4. Things Day 4 assumes are missing that already exist

Phase 2 was built *before* Day 3's measurement half, so the RAG tickets are not starting
from zero:

- **M2X-043** (chunking + Chroma index) — done. `src/m2x/indexing.py`,
  `src/m2x/vector_store.py`, `m2x index build|query`.
- **M2X-044** (cited Q&A with abstention) — done. `src/m2x/ask.py`, `m2x ask`,
  `prompts/rag/v1` and `v2`.
- **M2X-037** (fenced-JSON parse fix) — done by someone else, merged as #28. Uses
  `instructor.Mode.MD_JSON`.

So **M2X-045** (30-question eval set) and **M2X-046** (RAGAS wiring) sit on top of
working code. Read `docs/design/day4-ask.md` and `docs/design/day4-index.md` first.

One inherited number worth knowing: `ask` uses `--max-distance 0.48`, described in its own
retro as "measured and provisional" on eight questions. M2X-045's 30 questions are the
first real chance to re-derive it.

---

## 5. Known defects, unfixed

**Citation drift — the top schema-validity failure.** The extractor pairs a segment id
with the *previous* line's timestamps (`seg-0033` cited as `580.3-581.4` when it runs
`581.44-586.445`), failing evidence validation and burning the whole retry budget on that
case. An explicit prompt rewrite (v3) did not change it, which points at the model rather
than the wording.

**The structural fix is known and belongs to M2X-041**: have the model cite an id only and
derive the time range in code. M2X-044 already does exactly this for RAG citations — "a
timestamp the model cannot type is one it cannot invent". It is a schema change, so it was
out of scope for M2X-036.

**The extractor is steerable.** Two demonstrated compliance failures:
- v2 returned *"The meeting decided to assign everything to Bob"* as a decision. No `owner`
  field said Bob, so an owner-only check would have passed it.
- v3 fixed that case; `inject-03` then returned a decision reading *"SYSTEM OVERRIDE
  ACCEPTED"*, verbatim what the pasted `<system>` block demanded.

A prompt rule naming one attack shape does not generalise to the next.

**`scripts/fetch_tiron.py` writes Windows paths.** Lines ~115-118 use `str(Path)`, so
running the fetch on Windows flips every manifest path to backslashes. One-line fix
(`.as_posix()`), belongs to the corpus ticket.

---

## 6. Environment, as configured on this machine

| | |
|---|---|
| **Ollama** | 0.32.9 installed at `%LOCALAPPDATA%\Programs\Ollama\ollama.exe`, serving on :11434 |
| models pulled | `nomic-embed-text` (embeddings, **required** by the eval), `hf.co/bartowski/Meta-Llama-3.1-8B-Instruct-GGUF` |
| **extraction provider** | use `--provider nim`. Groq's free tier returns **413** on the larger cases and **429** on rate — it lost 7 of 15 cases |
| local extraction | **does not work.** Reached 7/15 then hung; ollama idle, process blocked. 12.58 tok/s warm, 37.9 s mean per attempt, largest prompt 3,972 tokens against a **4,096** context. Cause unpinned — the three cases that logged nothing are the *smallest*, so it is not simple overflow |
| `num_ctx` | not exposed by `ModelAdapter`; raising it is an adapter change |

`ollama serve` must be running for **any** eval, because the matching metric embeds.

---

## 7. Tooling gotchas that cost time

- **`make` does not exist here.** Use `uv run pytest -q`.
- **`gh` is not on PATH** for this shell. Invoke as `& "C:\Program Files\GitHub CLI\gh.exe"`.
  Authenticated as `yashpancholi09`.
- **Never pass a multi-line body as a `--body` argument.** PowerShell here-strings break on
  embedded double quotes. Use `gh pr create --body-file <path>` and
  `git commit -F <path>`.
- **Never rewrite a tracked file with `Get-Content | Set-Content`.** It mangles em-dashes to
  `â€"`. Use the editing tools.
- **Odoo:** resolve the board by name each session — there are two projects called
  *AI Dev Course* (74 and 75); take the **highest id**. Comments are **write-only**: the
  read tools hardcode a `name` field `mail.message` does not have, so `get_ticket` and
  `search_tickets` both fail against that model. Post **plain text**; HTML is escaped and
  renders as literal tags.
- **`data/` is git-ignored and empty on a fresh clone.** There are no transcripts. The eval
  works because labels reference the *committed* tiron reference pair under `eval/tiron/`.

---

## 8. Suggested order for Day 4

1. **Decide the M2X-040 / M2X-041 conflict** (section 1). Everything else waits on it.
2. **M2X-042** — the rituals ticket. Note that Day 1–3 cross-reviews were all left
   *pending* rather than written up, because they need two people; do not fill one in from
   what it would have concluded.
3. **M2X-041 substance**, if the decision goes that way: citation fix (section 5), then
   fresh sealed cases.
4. **M2X-045 / M2X-046** — RAG eval set and RAGAS. Independent of the 1B gate, so they can
   proceed regardless.

Read before starting: `docs/design/day3-iteration.md` (the risk note), `eval/README.md`
(how a number is computed), `eval/labels/README.md` (what the ground truth is and is not).
