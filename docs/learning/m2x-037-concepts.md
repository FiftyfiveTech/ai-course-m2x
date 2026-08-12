# M2X-037 concepts — structured output, and where the parsing actually happens

The ticket is one word of code. The concepts are worth more than the fix, because the
same shape of mistake is available every time a library is asked to do something it
almost does.

## 1. Structured output is a negotiation, not a guarantee

Asking a model for JSON gets you JSON *usually*. Chat models are trained to be helpful
to a reader, and a reader likes a fenced code block with a sentence in front of it:

````
Here is the corrected JSON response:
```json
{"decisions": [...]}
```
````

That is a perfectly cooperative answer. It is also not parseable by
`json.loads`. Nothing about it is a model failure — the failure is on the consuming side,
which asked for JSON and then assumed the reply would be *only* JSON.

Three ways a stack can close that gap, in descending order of reliability:

| approach | how | cost |
|---|---|---|
| provider-enforced (`response_format`, grammar/constrained decoding) | the server refuses to emit non-conforming tokens | needs provider support; ties you to a flag |
| tool/function calling | the schema rides the tool definition, the reply is an arguments object | not every model does it well |
| **prompt + tolerant parse** | ask for JSON, then extract it from whatever prose arrives | portable, and what this repo uses |

M2X is provider-neutral by design — the adapter deliberately does not expose
`response_format` (see `docs/design/day3-schema.md`), so JSON-only output is carried by
the schema instructions Instructor injects, not by a provider flag. That choice is
correct, and it *obliges* the third row: a tolerant parse.

## 2. Instructor's `Mode` is the parsing strategy, not a cosmetic label

`instructor.Mode` selects which handler reads the reply. Two look interchangeable and
are not:

- **`Mode.JSON`** — the content is expected to be a JSON document. It goes to Pydantic's
  JSON validator as-is.
- **`Mode.MD_JSON`** — the content is expected to be *markdown that contains* JSON. It
  routes through `extract_json_from_codeblock()` (`instructor/v2/core/json.py:9`), which
  scans for the first balanced `{`/`[` span and json-parses it — so a fence, a preamble,
  or both survive.

The name reads like a formatting detail. It is the whole parsing contract.

## 3. Retries only help against variance

The extraction loop retries with the validation error appended, and that genuinely fixes
a *stochastic* error: a fabricated `segment_id`, a relative deadline like "next friday".
Ask again, and a different sample comes back correct.

A fenced reply is not variance. It is what the model reliably does. Retrying re-requests
the same shape, gets it, and burns the attempt budget:

```
Invalid JSON: expected value at line 1 column 1 [type=json_invalid, ...]
instructor.v2.core.errors.InstructorRetryException
```

**The pitfall:** a retry loop makes a systematic defect look like a flaky one. Three
identical failures is the tell — variance would have produced three *different* errors.
When every attempt fails the same way, stop retrying and go read the parse path.

## 4. You can reproduce a "live provider" bug without a provider

This defect was recorded as needing a live call to observe, and carried that way in a
handoff note for days. It does not. `tests/test_extraction.py` already scripts the model
over `httpx.MockTransport` — so the reply body is yours to choose, including the exact
malformed shape the provider sends.

The one subtlety: **feed the same reply on every attempt.** A test that queues a fenced
reply followed by a clean one proves the retry works; it says nothing about the bug. The
live failure is the same shape three times.

**The general lesson:** "needs a live provider to reproduce" is usually a claim about the
test harness, not about the bug. If the failure is in *parsing* a response, the response
is just a string, and a string is cheap to fake.

## Pitfall summary

- Assuming "the model returned JSON" when it returned *prose containing* JSON.
- Reading a library mode as a label rather than as the behaviour it selects.
- Letting a retry loop disguise a systematic error as a flaky one.
- Believing a defect needs the network when only its input came from the network.
