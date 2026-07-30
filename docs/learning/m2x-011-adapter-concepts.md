# Concepts Behind the M2X Day-1 Adapter — Primer

The six engineering concepts the M2X-010/011 work exercises. Each section: what it is,
why it matters here.

## 1. Adapter pattern for LLM providers (provider-neutral abstraction)

One interface (`complete()`, `transcribe()`) in front of many backends. Application
code names a model by its Hugging Face repo id only; a data-driven routing table maps
repo id → provider endpoint + credential. Because Groq, NVIDIA NIM and Ollama all speak
the OpenAI-compatible wire format, one HTTP implementation covers all three by varying
base URL and credential. Benefit: switching provider is a config flag, not a refactor;
no `if provider == "groq"` branches ever leak into feature code.

## 2. Content-addressed response caching

Cache key = cryptographic hash (sha256) of everything that determines the response:
call kind, model, provider, full payload, sampling parameters. The key IS the identity
of the request. Classic pitfall: hashing too little. If provider is omitted, a
three-provider comparison returns one provider's cached answer three times. If
temperature is omitted, a `temperature=0.7` request silently gets a cached
`temperature=0.0` answer. Rule: a cache key must include every input that can change
the output — cache-key completeness is a correctness property, not a performance detail.
Large binary inputs (audio) are hashed to a digest rather than embedded.

## 3. Retry with exponential backoff and Retry-After

Transient failures (429 rate limits, 5xx) are retried with a doubling delay. A
provider's `Retry-After` header overrides the local guess — the server knows when quota
resets. Non-transient 4xx (malformed request) are never retried: retrying only burns
quota to fail identically. When retries are exhausted, the error names a concrete
escape hatch (switch provider via env var) instead of just reporting a status code.
Retries never multiply log records: the log counts logical calls, not HTTP attempts.

## 4. Run logging and cost observability (JSONL)

Every model call — cache hits included — appends exactly one schema-validated record to
an append-only JSONL file: model, provider, latency, tokens in/out, cost, cached flag.
Cost attribution is computed by replaying the log against a tracked price table, which
is why prices must live in version control, not a git-ignored `.env`. Failure policy is
asymmetric by design: cache writes are best-effort (an accelerator may degrade), log
writes raise on failure (a measurement tool must never silently lose a record). Cache
hits log `cost_usd=0` but real token counts, so "what did the cache save us" stays
computable.

## 5. Secrets handling with Pydantic Settings and SecretStr

Credentials enter through one typed Settings object and nowhere else. `SecretStr` masks
values in `repr`, exception traces and `model_dump()` — a stray print emits
`**********`. Keys are fetched per request, never stored on instances, never logged.
The split rule for configuration: if reproducing a number requires it, it is tracked
config; if leaking it compromises an account, it is `.env`.

## 6. Hermetic testing: mock transports and injected clocks

129 tests run with zero network and zero real time. `httpx.MockTransport` intercepts at
the transport layer so tests assert on the exact outgoing request per provider; sleep
and clock functions are injected so retry/backoff tests complete instantly. Cost math
is tested with fake non-zero prices, because free-tier $0.00 would make every cost
assertion vacuously pass. The shipped routing config itself is validated by the suite,
so a typo in the real registry fails in CI, not on a live call.
