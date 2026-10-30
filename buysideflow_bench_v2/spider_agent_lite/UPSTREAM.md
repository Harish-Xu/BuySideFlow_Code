# Upstream provenance

- Repository: <https://github.com/xlang-ai/Spider2.git>
- Source directory: `methods/spider-agent-lite`
- Commit: `cafb867313aab4e674652054198f383cf4018943`
- Retrieved: 2026-09-25
- License: MIT; the upstream repository license is copied to `LICENSE`.

`UPSTREAM_MANIFEST.json` records the SHA-256 digest of every mirrored file.
The snapshot excludes `bigquery_credential.json`,
`snowflake_credential.json`, `logs/`, and `output/`. These are credentials or
generated artifacts and are not implementation source. The upstream README
refers to `requirements.txt`, but that file is absent from the pinned source
directory. `requirements-runtime.txt` is therefore an adapter-maintained list
of packages imported by this snapshot, not a reconstructed upstream lockfile;
the resolved environment must be recorded with each formal experiment.
