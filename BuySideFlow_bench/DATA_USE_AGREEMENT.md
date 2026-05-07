# BuySideFlow Controlled Data Use Agreement

Version: 1.0  
Last updated: 2026-05-06

This agreement applies to controlled access to the licensed database snapshot used for BuySideFlow evaluation. It does not replace the public license for the BuySideFlow audit package. Public files such as task statements, manifests, benchmark reports, evaluator code, prompt assets, figures, and documentation are governed by `LICENSE.md`. The controlled database rows, database credentials, and unrestricted query access are excluded from that public license.

By requesting, receiving, or using controlled access to the BuySideFlow database snapshot, the user agrees to the terms below.

## 1. Definitions

**Benchmark Maintainers** means the authors or operators responsible for maintaining BuySideFlow and its controlled evaluation environment.

**Public Audit Package** means the publicly released BuySideFlow materials, including task statements, structured inputs, output contracts, dataset card, Croissant metadata, generated manifests, schema summaries where licensing permits, time-audit code, evaluator code, prompt assets, benchmark reports, tool-use logs, figures, and provenance hashes.

**Controlled Database Snapshot** means the fixed read-only financial database snapshot used for BuySideFlow execution. The snapshot contains licensed commercial financial data and is not redistributed as part of the Public Audit Package.

**Official Evaluation Service** means a maintainer-operated service that executes submitted workflows against the Controlled Database Snapshot and returns benchmark scores, diagnostics, tool-use tables, and provenance records.

**Authorized User** means an individual researcher, reviewer, academic user, or licensed data holder approved by the Benchmark Maintainers to use the Controlled Database Snapshot or Official Evaluation Service.

## 2. Permitted Use

Authorized Users may use the Controlled Database Snapshot or Official Evaluation Service only for:

- reproducing BuySideFlow benchmark results;
- evaluating SQL, SQL--Python, and database-agent systems on BuySideFlow tasks;
- conducting error analysis, time-audit analysis, and tool-use analysis for benchmark research;
- preparing academic publications, reviews, replication studies, or benchmark reports based on aggregate results.

Use must remain within the scope of BuySideFlow evaluation. The controlled access is not a general-purpose license to use, copy, mine, redistribute, or commercialize the underlying financial database.

## 3. Prohibited Use

Authorized Users must not:

- export, download, scrape, copy, or reconstruct raw licensed database rows outside the controlled evaluation workflow;
- publish, redistribute, sublicense, sell, or transfer any database dump, table extract, credential, connection string, or unrestricted query output;
- share database credentials or evaluation-service tokens with any person or system not approved by the Benchmark Maintainers;
- use benchmark access to build a substitute database, data product, factor library, commercial analytics product, or training corpus of licensed database rows;
- use query outputs to train, fine-tune, distill, or adapt models in a way that memorizes or reconstructs licensed database rows;
- bypass row limits, query controls, logging, rate limits, sandbox restrictions, or other access controls;
- attempt to infer, reverse engineer, or expose non-public schema assets, proprietary source documents, account-level information, client information, or other restricted materials;
- use the controlled access for production trading, investment advisory, client reporting, or other non-benchmark purposes.

## 4. Query and Output Boundaries

Controlled access is read-only and query-only. Authorized Users may execute benchmark workflows and inspect returned benchmark artifacts only to the extent needed for evaluation and error analysis.

Publications may report aggregate benchmark scores, task-level pass/fail indicators, diagnostic flags, tool-use statistics, and qualitative error examples when those examples do not disclose raw licensed rows or restricted schema details. If a result table or figure would reveal licensed database rows beyond what is necessary for benchmark reporting, it must not be redistributed.

The Benchmark Maintainers may impose query limits, rate limits, timeout limits, result-size limits, or task-scoped execution constraints to prevent bulk extraction or reconstruction of the underlying database.

## 5. Security and Credential Handling

Authorized Users must protect all credentials, tokens, connection strings, endpoint information, and temporary access materials with reasonable security controls.

Authorized Users must promptly notify the Benchmark Maintainers if credentials are lost, exposed, misused, or suspected to be compromised. Access may be suspended or revoked at any time to protect the Controlled Database Snapshot or comply with licensing obligations.

## 6. Logging and Audit

The Benchmark Maintainers may log authentication events, submitted workflows, executed queries, timestamps, task identifiers, output sizes, errors, scores, diagnostic flags, tool-use records, and provenance hashes.

These logs may be used to operate the benchmark, detect misuse, reproduce reported results, debug the evaluation service, and enforce this agreement. Logs will not be used to disclose a user's unpublished model details except as necessary for benchmark operation, misuse investigation, or as separately agreed by the user.

## 7. Publication and Citation

Authorized Users may publish aggregate evaluation results obtained through the controlled environment, provided that they:

- cite BuySideFlow;
- identify the database snapshot identifier and provenance hashes reported by the evaluator when available;
- do not publish controlled database credentials, raw table extracts, unrestricted query outputs, or licensed rows;
- distinguish deterministic tabular scores from judge-assisted mixed or visual scores when reporting results.

If publishing task-level examples, users should use the public task text and output contracts rather than reproducing restricted database rows.

## 8. Access Term and Revocation

Access is granted for the period specified in the approval notice or controlled-access documentation. The Benchmark Maintainers may revoke access if the user violates this agreement, if the underlying data-provider license requires revocation, or if continued access would create legal, security, operational, or cost risks.

Upon expiration or revocation, the Authorized User must stop using the Controlled Database Snapshot and delete any credentials, tokens, temporary connection files, local caches, or restricted outputs obtained through controlled access, except for aggregate benchmark reports that comply with this agreement.

## 9. Third-Party Rights

The Controlled Database Snapshot contains licensed third-party financial data. No ownership or redistribution rights in the underlying database are transferred to Authorized Users. All rights not expressly granted in this agreement are reserved by the relevant rights holders and the Benchmark Maintainers.

If an Authorized User already holds an independent license to the underlying financial data, this agreement does not expand that license. The user remains responsible for complying with their own data-provider agreement.

## 10. No Warranty

The Controlled Database Snapshot, Official Evaluation Service, and benchmark infrastructure are provided for research evaluation as available. The Benchmark Maintainers do not warrant that the data, service, outputs, scores, or reports are error-free, continuously available, or suitable for investment, trading, advisory, regulatory, or commercial use.

BuySideFlow is a research benchmark. It must not be used as a basis for investment decisions or client-facing financial advice.

## 11. Contact

Requests for controlled access, questions about this agreement, and reports of suspected credential exposure should be sent through the contact channel listed in `CONTROLLED_ACCESS.md` or the official BuySideFlow repository page.
