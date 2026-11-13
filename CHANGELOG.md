# Change log

## Unreleased — portable Full profile (planned)

- Full feature parity with the development retrieval engine is the next release goal; Full will be the default and Lite optional.
- Semantic/hybrid retrieval and optional neural reranking are not yet available in this distribution. Current installation instructions and runtime remain keyword-only.
- A matching precomputed semantic index and reproducible inference environment are planned; corpus acquisition and maintenance will remain outside the portable runtime.


## 2026-11-12

- Added an optional research wrapper that uses collection-filtered discovery,
  bounded searches within shortlisted papers, and a check for missing
  deliverable sections. The public connector continues to use keyword search.
- Verified basic evidence handoff, including physical page references and
  quality warnings. The workflow remains experimental while research
  completeness and citation quality receive further review.
