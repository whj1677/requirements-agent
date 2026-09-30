# Formal handoff scope v1

Handoff manifest 1.2 adds `delivery_scope.json` and `delivery_scope.schema.json`.
The independent `requirements-delivery-scope-1` contract carries every formally
selected to-be delivery item, including constraints, from the frozen confirmed
baseline. Identity is `(type, namespace, id, version, content_hash)`. Entities
contain canonical item content and exclude candidates, deferred, as-is and
requirements outside the confirmed functional scope. The existing scope linkage
rules determine supporting rule/acceptance inclusion; constraints are retained.

`baseline_id`, `confirmation_id`, `hashes` and `baseline_hash` bind that snapshot.
`content_hash` hashes sorted compact UTF-8 JSON with CRLF normalized to LF using
the application's `digest`; manifest hashes refer to exact file bytes. Document
`content_hash` is the canonical generated content; `file_content_hash` hashes the
reader projection in the referenced file. Legacy items without recorded content
version are explicitly marked `legacy_v1`; compare content hashes as well as
versions. No historical data is migrated or implicitly confirmed.

`requirements-exchange-1` remains unchanged, with its `requirements` array and
tracked requirement/rule/acceptance entities. It retains historical candidates
and deferred entries and does not contain constraints. Consumers of the new
formal scope read `entities` directly and join tracked entries by the complete
identity only when `exchange_file` is present. They must not interpret the legacy
array as the complete formal scope. Old archives remain unchanged. This additive
contract has local schema/export regression coverage; external consumers and
strict manifest-version consumers have not been integration tested.

Confirmation is content acceptance only. Downstream remains `unbound` and test
execution `not_run`. No business implementation, test execution or external
adapter compliance is inferred.
