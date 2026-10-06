# tests/fixtures/ollama/

Hand-crafted (not captured) fixture responses for
`puriyudha.clients.ollama.OllamaClient`, matching Ollama's documented
`POST /api/generate` (`stream: false`) response shape.

Unlike `tests/fixtures/contract/` (captured for real against
`services.mock_bhashini`), there is no bundled fake Ollama server here:
Ollama's HTTP API shape is a stable, independently documented contract
(unlike the Bhashini service, which CLAUDE.md explicitly flags as
unverified), so these were written by hand from that documentation rather
than by running a live `ollama serve` with `ministral-3:3B` / `qwen3-vl:2b`
pulled.

- `generate_text_valid.json` - a successful `ministral-3:3B` call whose
  `response` field is valid JSON matching a simple schema.
- `generate_vision_valid.json` - a successful `qwen3-vl:2b` call (an
  `images` request), same idea.
- `generate_invalid_json.json` - `response` is not valid JSON at all (not
  even after balanced-object extraction), for testing
  `OllamaClient.generate_json`'s one retry.
- `generate_schema_mismatch.json` - `response` is valid JSON but missing a
  required field, for testing the same retry path via schema validation
  instead of a parse error.

The five below capture real 3B-model failure modes (verification-pass
finding H1, part 2.4): a small local model asked for JSON-constrained
output still, in practice, does not always emit *only* JSON.
`OllamaClient.generate_json` repairs the first three by extracting the
first balanced `{...}` substring (a string-literal-aware brace-depth scan,
not a regex -- see `puriyudha/clients/ollama.py`'s
`_extract_first_balanced_json_object`); the last two remain genuinely
malformed even after that extraction is attempted, and still go through the
existing retry-once-then-raise path.

Repaired (extraction succeeds and the log records a WARNING -- a rate worth
tracking later as a signal about prompt quality):

- `generate_fenced_json_block.json` - `response` is a ```` ```json ... ``` ````
  fenced code block wrapping the object.
- `generate_json_with_prose_preamble.json` - `response` has prose before the
  opening `{` ("Sure! Here is the extracted record: {...}").
- `generate_concatenated_json_objects.json` - `response` is two JSON objects
  concatenated with no separator; the balanced scan stops after the first
  one and the second is ignored (as if the model kept generating past its
  first complete answer).

Still genuinely malformed (extraction is attempted, still fails to parse,
same retry-once-then-raise `OllamaInvalidJSONError` path as
`generate_invalid_json.json`):

- `generate_json_trailing_comma.json` - `response` has a trailing comma
  before the closing brace, which extraction finds a balanced substring for
  but which still is not valid JSON.
- `generate_json_truncated_mid_string.json` - `response` cuts off mid
  string literal (as if the model hit its token limit), so the object never
  balances at all and extraction returns nothing to parse.
