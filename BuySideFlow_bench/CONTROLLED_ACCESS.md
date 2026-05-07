# BuySideFlow Controlled Access

Version: 1.0  
Last updated: 2026-05-07

BuySideFlow is released as a public audit package with controlled execution over a licensed financial database snapshot. The public files can be inspected without database credentials. Full benchmark execution requires either authorized read-only database access or use of the official evaluation service.

This document explains what is public, what is controlled, who may request access, and what access provides.

## 1. Public Materials

The public audit package may include:

- task statements and structured inputs;
- output contracts;
- dataset card and Croissant metadata;
- generated manifests and manifest overrides;
- public schema summaries and DDL fragments where licensing permits;
- table-usage summaries;
- evaluator, time-audit, manifest, and provenance code;
- prompt assets;
- benchmark reports, figures, and per-task tool-use logs;
- provenance hashes for benchmark sources, schema assets, prompt assets, reference artifacts, sidecars, evaluator code, judge caches, and database snapshot identifiers.

These materials are governed by `LICENSE.md`.

## 2. Controlled Materials

The following materials are not part of the public release:

- licensed database rows;
- database dumps or backups;
- unrestricted query outputs;
- database credentials, endpoints, or connection strings;
- proprietary source documents used during task construction;
- restricted schema assets or reference artifacts when they would expose licensed data or non-public database details.

Access to these materials, when provided, is governed by `DATA_USE_AGREEMENT.md` and the relevant third-party data-provider license.

## 3. Access Modes

BuySideFlow supports two controlled execution modes.

### 3.1 Read-Only Database Access

Authorized users may receive query-only credentials to a fixed read-only managed database snapshot. This mode is intended for users who need to rerun the evaluator, inspect execution behavior, or reproduce benchmark reports under the same database snapshot and provenance hashes.

Read-only access does not permit data export, bulk extraction, credential sharing, database replication, or use of the database for non-benchmark purposes.

### 3.2 Official Evaluation Service

When direct database access is not feasible, users may submit model outputs, trajectories, or benchmark workflows to an official evaluation service. The service executes the submitted materials against the same controlled database snapshot and returns evaluation reports, scores, diagnostic flags, tool-use tables, and provenance records.

This mode is intended for reviewers, replication studies, and external model evaluations where controlled execution is sufficient and direct database credentials are not necessary.

## 4. Who May Request Access

Controlled access may be requested by:

- academic researchers conducting benchmark replication or model evaluation;
- conference reviewers or artifact evaluators;
- licensed holders of the underlying financial data;
- research teams conducting non-commercial benchmark comparisons;
- other users approved by the benchmark maintainers for reproducibility or auditing purposes.

Access is not intended for production trading, investment advisory, client reporting, commercial data extraction, or construction of substitute financial databases.

## 5. Request Requirements

An access request should include:

- requester name and affiliation;
- institutional email address;
- intended access mode: read-only database access or official evaluation service;
- purpose of the request, such as paper review, replication, benchmark comparison, or model evaluation;
- expected duration of access;
- confirmation that the requester accepts `DATA_USE_AGREEMENT.md`;
- confirmation that the requester will not export, redistribute, or reconstruct licensed database rows;
- whether the requester already holds an independent license to the underlying financial data, if applicable.

For double-blind review settings, reviewers should follow the conference's official artifact-access process when available. The maintainers may provide anonymized access instructions or an official evaluation-service channel to avoid unnecessary identity disclosure during review.

## 6. Approval and Provisioning

The benchmark maintainers review requests for consistency with the benchmark purpose, data-provider licensing constraints, security requirements, and operational capacity.

Approved users may receive one of the following:

- temporary read-only database credentials;
- a task-scoped evaluation-service token;
- instructions for submitting model outputs or trajectories for official evaluation;
- a provenance report identifying the database snapshot, schema assets, prompt assets, reference artifacts, sidecars, evaluator version, and judge-cache state used for the run.

Access may include query limits, timeout limits, result-size limits, rate limits, task-scoped execution, IP allowlisting, or other controls needed to protect the licensed database.

## 7. Service Period

The controlled access service is maintained for research reproducibility and benchmark review. The maintainers intend to keep a fixed read-only snapshot and official evaluation path available during the active benchmark release period.

Service continuation may depend on data-provider licensing, hosting cost, access volume, and security requirements. If the access policy or service period changes, the public repository will be updated with the current terms.

## 8. What Users May Publish

Authorized users may publish:

- aggregate benchmark scores;
- task-level pass/fail results when they do not reveal restricted rows;
- point-in-time diagnostic summaries;
- tool-use statistics;
- qualitative error analysis based on public task text and non-sensitive outputs;
- provenance identifiers and hashes reported by the evaluator.

Authorized users must not publish:

- database credentials or endpoints;
- raw licensed table rows;
- unrestricted query outputs;
- database dumps or reconstructed tables;
- restricted schema assets;
- proprietary source documents;
- any artifact that would enable redistribution or reconstruction of the controlled database.

## 9. Revocation

Access may be revoked if a user violates `DATA_USE_AGREEMENT.md`, attempts to bypass access controls, exports restricted data, shares credentials, uses the database outside the benchmark purpose, or creates legal, security, operational, or licensing risk.

Upon revocation or expiration, users must stop using controlled access and delete credentials, tokens, local caches, temporary connection files, and restricted outputs obtained through controlled access, except for aggregate benchmark reports that comply with the agreement.

## 10. Contact

For controlled access requests, use the contact channel listed on the official BuySideFlow repository page.

If this file is included in an anonymized conference submission, the public repository may temporarily use a conference-approved anonymous contact mechanism. After de-anonymization, the maintainers may replace it with a named institutional contact.
