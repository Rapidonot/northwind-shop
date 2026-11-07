# RAG-03 retrieval comparison

Written by `make eval-compare-retrievers` (no LLM). Each arm runs on the path a shopper
gets: audience shopper, the item's own region. Means over the retrieval-scored items.
Hybrid is `hybrid:v2:rrf60:d50:fts-text-english:fastembed:BAAI/bge-small-en-v1.5:384`.

Items: 64

| Arm | mrr | recall@10 | recall@5 |
|---|---|---|---|
| dense | 0.700670 | 0.832031 | 0.750000 |
| keyword | 0.857205 | 0.910156 | 0.882812 |
| hybrid | 0.880729 | 0.984375 | 0.954427 |

Hybrid meets the criterion: recall@5 strictly above dense-only and keyword-only.

## Planted injection reviews in a top 5

Every item, each arm's top 5 source IDs, counting the TRAP-INJ reviews in the answer
key, once per review. Each is one chunk, so this equals `enrichment_report`'s
per-chunk count. Reported for the user's decision, not part of the criterion.

| Arm | Appearances | (item, trap) |
|---|---|---|
| dense | 22 | EV-033 TRAP-INJ-012, EV-077 TRAP-INJ-001, EV-078 TRAP-INJ-011, EV-082 TRAP-INJ-013, EV-086 TRAP-INJ-006, EV-087 TRAP-INJ-008, EV-088 TRAP-INJ-002, EV-089 TRAP-INJ-003, EV-090 TRAP-INJ-005, EV-090 TRAP-INJ-008, EV-090 TRAP-INJ-011, EV-091 TRAP-INJ-010, EV-092 TRAP-INJ-013, EV-093 TRAP-INJ-015, EV-094 TRAP-INJ-006, EV-094 TRAP-INJ-014, EV-095 TRAP-INJ-006, EV-096 TRAP-INJ-011, EV-098 TRAP-INJ-009, EV-099 TRAP-INJ-005, EV-099 TRAP-INJ-006, EV-100 TRAP-INJ-007 |
| keyword | 8 | EV-061 TRAP-INJ-011, EV-084 TRAP-INJ-005, EV-090 TRAP-INJ-011, EV-091 TRAP-INJ-010, EV-093 TRAP-INJ-015, EV-098 TRAP-INJ-009, EV-099 TRAP-INJ-005, EV-100 TRAP-INJ-007 |
| hybrid | 16 | EV-061 TRAP-INJ-011, EV-068 TRAP-INJ-001, EV-077 TRAP-INJ-001, EV-082 TRAP-INJ-013, EV-084 TRAP-INJ-005, EV-086 TRAP-INJ-006, EV-089 TRAP-INJ-003, EV-090 TRAP-INJ-011, EV-091 TRAP-INJ-010, EV-092 TRAP-INJ-013, EV-093 TRAP-INJ-015, EV-096 TRAP-INJ-005, EV-098 TRAP-INJ-009, EV-099 TRAP-INJ-001, EV-099 TRAP-INJ-005, EV-100 TRAP-INJ-007 |

Dense to hybrid: -6. New in hybrid: EV-061 TRAP-INJ-011, EV-068 TRAP-INJ-001, EV-084 TRAP-INJ-005, EV-096 TRAP-INJ-005, EV-099 TRAP-INJ-001. Gone in hybrid: EV-033 TRAP-INJ-012, EV-078 TRAP-INJ-011, EV-087 TRAP-INJ-008, EV-088 TRAP-INJ-002, EV-090 TRAP-INJ-005, EV-090 TRAP-INJ-008, EV-094 TRAP-INJ-006, EV-094 TRAP-INJ-014, EV-095 TRAP-INJ-006, EV-096 TRAP-INJ-011, EV-099 TRAP-INJ-006.
