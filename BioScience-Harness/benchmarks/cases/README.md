# Benchmark cases

The case files themselves are **not published in this repository**.

Each case is a JSON document conforming to `BenchmarkCase`. What lives here at
release time:

- `dev/` — 60 public development cases, published with the benchmark release so
  a new system can iterate against them.
- `../hidden/` — 40 held-out cases, delivered only to official evaluators.
- `adversarial/` — 20 safety and overclaim cases, scored on their own axes.

The schema, the loader, the corpus builder, the scorers and the data card are
all in this repository, and they are what make the benchmark reproducible. The
cases and their gold labels are withheld so that a clone cannot be prompted or
trained against them.

This directory is ignored by `.gitignore` except for this file. See
`docs/adr/0001` for why a Season, once cut, cannot be modified.
