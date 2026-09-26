# Hidden split

Not published, by design.

The hidden split is what makes a leaderboard mean anything: a system that has
seen the answers is not being evaluated. `scripts/check_leakage.py` asserts that
no hidden case identifier, prompt or gold answer appears in any committed file,
in any published trace, or in any Arena JSON document.

If you are an official evaluator, the split is delivered out of band and its
digest is published, so a reported result can be tied to a specific revision of
the cases without the cases being disclosed.

This directory is ignored by `.gitignore` except for this file.
