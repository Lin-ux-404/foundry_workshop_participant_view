# Synthetic application data

This directory contains the versioned, synthetic lookup data used by the DRAAD
workshop application:

- [`crew.json`](./crew.json): available crew records;
- [`raamopdrachten.json`](./raamopdrachten.json): authorization scopes;
- [`incidents.json`](./incidents.json): repeatable exercise inputs; and
- [`vwi_catalog.json`](./vwi_catalog.json): canonical synthetic procedure IDs
  used by deterministic safety and coverage checks.

The supplied Python layer reads these files locally. Participants implement the
AI components that retrieve and select candidate procedures; the deterministic
application layer filters authorization records by date, postcode and available
crew, then applies the final safety rules.

The prepared Azure AI Search index contains the procedure corpus. Source PDFs
and indexing utilities are facilitator-owned and are intentionally absent from
this repository.

Work-mode suffixes are safety-significant. For example, `E-22-onder-sp` and
`E-22-sp-loos` are distinct identifiers. An ambiguous bare code must not count
as procedure coverage.

Use the supplied incidents as test inputs, inspect the pipeline trace, and
explain each final rule verdict. The expected case-by-case answers are not
included; teams must establish them from retrieved evidence and deterministic
rule output.

All records are teaching fixtures. They are not operational authorizations.
