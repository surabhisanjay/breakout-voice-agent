# OpenAI Fallback Audit

## Evidence reviewed

- 517 persisted conversation log files.
- Saved API session memory.
- `InboundAgent`, `GPTReasoner`, and `ResponseComposer` exception paths.
- Existing simulated timeout, authentication, connection, and rate-limit tests.

## Failure frequency

No persisted log contains an actual HTTP 429 or `Too Many Requests` message. Apparent `429` search hits were numeric
substrings in session IDs or latency values. The historical 429 rate therefore cannot be calculated from repository
artifacts. This is a telemetry limitation, not evidence that no failures occurred.

## Fallback path

1. OpenAI calls catch timeout, connection, rate-limit, and authentication exceptions.
2. `last_error` becomes `openai_failure: ...`.
3. The approved deterministic draft is returned.
4. The affected OpenAI component is disabled for the rest of that agent instance.
5. Subsequent turns continue through deterministic FAQ, qualification, recommendation, and booking logic.

## Conversation impact

The fallback does not bypass booking safeguards or provider validation. It can reduce conversational naturalness because
the deterministic response is no longer rewritten. The severe saved-session failures were reproduced in deterministic
routing and extraction code, so they cannot be attributed to OpenAI 429 responses.

## Status

- Exception handling: **WORKING**, verified by tests.
- Deterministic functional fallback: **WORKING**, covered by the 321-test suite and transcript replay.
- Historical failure-rate measurement: **BROKEN**, because response source and OpenAI error type are printed only in
  debug paths and are not persisted by the transcript logger.
- Production fallback quality under sustained real 429s: **PARTIALLY WORKING**; semantics are covered, but no persisted
  production 429 sample exists for an end-to-end replay.

## Recommended operational follow-up

Persist structured `response_source`, OpenAI error class, and per-session fallback count in production telemetry. This
was not added during stabilization because it is an observability extension rather than a reproduced workflow fix.
