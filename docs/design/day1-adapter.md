# Adapter design

Design record for the provider-neutral model adapter (M2X-010 pairing output,
implemented by M2X-011). Records *what* was decided and *why*, so the rationale
survives past the week.

## The problem

Provider choice will change mid-project: Groq throttles and NIM has to take over,
and hosted must be compared against local. If provider choice lives in feature code
as `if provider == "groq"` branches, every one of those switches is a refactor.

## The shape

```
ModelAdapter
  complete(messages, model_repo_id, *, provider, temperature, max_tokens, seed) -> Response
  transcribe(audio, model_repo_id, *, provider, language)                       -> Transcript
```

`model_repo_id` is **always** a Hugging Face repo id. It is the only model identifier
in application code. Provider is resolved from data, never from a branch:

```
config/models.toml  ──  repo id ─> provider ─> {base_url, credential, provider's own alias}
```

All three backends speak the OpenAI-compatible wire format, which is why one
implementation covers them by varying a base URL and a credential.

| Module | Responsibility |
|--------|----------------|
| [model_registry.py](../../src/m2x/model_registry.py) | Routing table, banned-model rule, config validation |
| [adapter.py](../../src/m2x/adapter.py) | HTTP, retries, response parsing — the only code that talks to a provider |
| [cache.py](../../src/m2x/cache.py) | Content-addressed response cache |
| [run_log.py](../../src/m2x/run_log.py) | The eleven-field JSONL record |
| [pricing.py](../../src/m2x/pricing.py) | Cost arithmetic |
| [settings.py](../../src/m2x/settings.py) | Credentials — the security boundary |

## Decisions

### 1. Prices live in tracked config, not `.env`

`config/models.toml` is committed and holds provider endpoints, model routing, and
prices. `.env` holds credentials and nothing else.

**Why this deviates from M2X-011.** That ticket says Pydantic Settings loads the price
table from `.env`. But `.env` is git-ignored, and the cost-attribution report is built
by replaying the run log against the price table — so a supervisor on a fresh clone
would compute $0.00 for everything and the report would be unverifiable. The split
rule: *if reproducing a number requires it, it is tracked config; if leaking it
compromises an account, it is `.env`.*

The one exception is `OLLAMA_HOST`, which stays an environment variable because the
port a laptop serves on is machine-local and does not belong in version control.

### 2. Provider is part of the cache key

Key = `sha256(kind + repo_id + provider + payload + params)`.

Including `provider` is not cosmetic. Groq and a quantised local Ollama build of the
same repo id give measurably different output, and measuring that gap is a
deliverable. Without provider in the key, running one prompt across three providers
would hit the cache on runs two and three and return run one's answer — three
identical results presented as a comparison. That directly contradicts M2X-011's own
acceptance criterion, so the key must be provider-sensitive.
Test: `test_switching_provider_does_not_hit_the_cache`.

### 3. Sampling params are part of the cache key

Ask for `temperature=0.7`, and a key built only from `(model, messages)` hands back a
cached `temperature=0.0` answer with nothing to indicate it happened. That is a silent
correctness bug that would quietly corrupt any model comparison.
Unset params are dropped rather than hashed as `null`, so adding a new optional
parameter later does not invalidate every existing entry.

### 4. `transcribe()` lives on the same adapter, and caches by audio digest

One class, both modalities. The Day-1 pitfall is *letting any call bypass the adapter*
— such a call is unlogged, and an unlogged call makes the cost report a lie. Audio is
hashed rather than embedded in the key, since a ten-minute clip is megabytes and the
digest is a perfectly good content address.

`verbose_json` is always requested. Plain text is smaller, but timestamps are the
product's spine — every extracted decision and every citation resolves to a segment's
time range — and discarding them here would be unrecoverable.

### 5. Cache hits log zero cost but real token counts

`cost_usd` is money actually spent, so a hit records `0.0`. `tokens_in`/`tokens_out`
describe the payload and keep their real values. The asymmetry is deliberate: replaying
the price table over `cached=true` records yields exactly what the cache saved. Zeroing
tokens too would destroy that.

`latency_ms` on a hit is the cache read, not the original network call — the original
number is already in the log from when the call was first made.

### 6. Failures: cache is best-effort, the log is not

A cache write that fails is swallowed — the cache is an accelerator, and a full disk
should slow a run down, not break it. A run-log write that fails raises. A run that
cannot be measured has failed at its actual purpose, and a silently missing record is
the worst outcome for an auditing tool.

Failed calls write neither a cache entry nor a log record, so a transient error is
never memoised and never counted as work.

### 7. Retries

429 and 5xx are retried with exponential backoff; a provider's `Retry-After` header
always wins over the local guess, since it is authoritative about when quota resets.
Other 4xx are raised immediately — retrying a malformed request only burns quota to
fail again. Exhausted 429 raises an error naming the concrete escape hatch
(`M2X_PROVIDER_OVERRIDE=nim`) rather than just reporting the status.

Retries do not multiply log records: the log counts calls, not HTTP attempts.

### 8. Security boundary

Credentials enter through `Settings` and nowhere else. They are typed `SecretStr`, so
a stray `print`, an exception `repr`, or a `model_dump()` emits `**********`. The
adapter fetches a key per request and never stores one on the instance. Local Ollama
sends no `Authorization` header at all rather than an empty one.

The run log records `model_repo_id` and `provider`. It never records a key or prompt
content. Asserted by `test_secrets_are_not_exposed_by_settings_repr`.

**Rollback:** this design is additive — the adapter, registry, cache, logger, and their
tests are new files. Reverting the commits that add `src/m2x/` and `config/` returns
the repo to the skeleton with no other code affected.

### 9. Guardrails

Banned models (`gemini*`, `groq/compound*`) are rejected **before** the registry
lookup, so adding one to `config/models.toml` is not enough to use it. Calling
`complete()` with a transcription model — or the reverse — is caught locally, because
the provider-side symptom is an opaque 400. A provider that cannot serve a model is an
error, never a silent downgrade to the default: a comparison claiming three providers
must fail loudly if one of them did not actually run.

## Verification

`make test` — 129 tests, no network and no real clocks. HTTP is
`httpx.MockTransport`, so tests assert on the exact outgoing request; sleep and both
clocks are injected, so retry tests finish instantly.

`test_repository_config_loads_and_routes` validates the committed
`config/models.toml` itself, so a typo in the real registry fails in CI rather than on
a live call.

## Open, not yet built

- **`make demo`** — the three-provider demo command M2X-011 is verified by. Needs the
  `m2x` CLI entry point, which does not exist yet; `make run` still exits 1.
- **`m2x runs summary`** — totals and p50/p95 by provider/model/phase. M2X-014. The
  record shape and `RunLogger.read_all()` it builds on are in place.
- **Diarisation** — `TranscriptSegment.speaker` is carried through the adapter but
  nothing populates it until Phase 1.
