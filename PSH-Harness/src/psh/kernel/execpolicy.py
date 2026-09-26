"""Declarative execution policy with self-testing rules.

Borrowed from Codex (`codex-rs/execpolicy/README.md`, `src/policy.rs`), with one property
from Claude Code (`docs/en/permissions`). Attribution and the reasons for each choice are in
`PATTERN_ATTRIBUTION.md`; the mechanics are here.

Three properties transfer from Codex's ``prefix_rule``:

* **Three decisions, not two.** ``allow`` / ``prompt`` / ``forbidden``. psh's tool gateway had
  a hardcoded destructive-command denylist — binary, and editable only by changing code.
* **Justification is a field.** A rule says why it exists, and a ``forbidden`` rule is asked to
  name an alternative. The justification is surfaced in the refusal.
* **Rules carry their own tests.** ``match`` and ``not_match`` are example invocations
  validated when the policy loads. A rule that does not match its own examples, or matches
  one it says it should not, is rejected before it can be applied. Codex calls these unit
  tests; that is what they are.

One property transfers from Claude Code's permission rules, with a recorded departure:

* **``forbidden`` is absolute: first match wins and specificity does not reorder.** A broad
  ``forbidden`` cannot be punched through by a narrower ``allow``. This is what makes the
  policy safe to extend: an amendment (see ``approvals.py``) can only ever add ``allow``
  rules, and every ``forbidden`` still overrides them.
* **Between ``prompt`` and ``allow``, the most specific match wins.** This departs from Claude
  Code, where ``ask`` also beats a narrower ``allow``. The departure is deliberate: the point
  of an approval amendment is that "ask before ``git push``" plus a user saying "allow
  ``git push origin`` from now on" must actually stop asking. Under strict ask-before-allow it
  never would — an amendment could only fill an *unruled* gap, which is not the case it exists
  for. Codex's ``ApprovedExecpolicyAmendment`` presupposes that a prompted command becomes
  allowed, so where the two sources disagree, this follows Codex. The safety property that
  matters — nothing relaxes a ``forbidden`` — is unchanged.

Not borrowed: Starlark. Rules are dataclasses and load from JSON. A second language inside a
small kernel is cost without benefit at this scale.

Matching model
--------------
A command is a token list (``shlex``-split when given as a string). A pattern is an ordered
list of tokens; each element is either a literal string or a list of alternatives, and ``*``
matches any single token. A pattern matches a command when the command *begins with* the
pattern — a prefix rule, exactly as in Codex.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..contracts import PolicyDenied

__all__ = ["Decision", "PrefixRule", "ExecPolicy", "PolicyEvaluation", "DEFAULT_RULES",
           "RuleValidationError"]


class Decision(str, Enum):
    ALLOW = "allow"
    PROMPT = "prompt"
    FORBIDDEN = "forbidden"

    @property
    def rank(self) -> int:
        # Evaluation order: forbidden first, then prompt, then allow.
        return {"forbidden": 0, "prompt": 1, "allow": 2}[self.value]


class RuleValidationError(PolicyDenied):
    """A rule failed its own examples at load time."""


Token = str | Sequence[str]


def _tokenize(command: str | Sequence[str]) -> tuple[str, ...]:
    if isinstance(command, str):
        return tuple(shlex.split(command))
    return tuple(str(t) for t in command)


@dataclass(frozen=True, slots=True)
class PrefixRule:
    """One rule: a token-prefix pattern and what to do when a command matches it."""

    pattern: tuple[Token, ...]
    decision: Decision = Decision.ALLOW
    justification: str = ""
    match: tuple[tuple[str, ...], ...] = ()
    not_match: tuple[tuple[str, ...], ...] = ()
    #: Where the rule came from: "default", "policy_file", "amendment:<session>" ...
    origin: str = "default"

    def __post_init__(self) -> None:
        if not self.pattern:
            raise RuleValidationError("a rule needs at least one pattern token")
        if self.decision is Decision.FORBIDDEN and not self.justification:
            raise RuleValidationError(
                "a forbidden rule must carry a justification, ideally naming an alternative")
        # Self-test. A rule that fails its own examples is a rule nobody understands.
        for example in self.match:
            if not self.matches(example):
                raise RuleValidationError(
                    f"rule {self.render()} claims to match {list(example)!r} and does not")
        for example in self.not_match:
            if self.matches(example):
                raise RuleValidationError(
                    f"rule {self.render()} claims NOT to match {list(example)!r} and does")

    def matches(self, command: str | Sequence[str]) -> bool:
        tokens = _tokenize(command)
        if len(tokens) < len(self.pattern):
            return False
        for want, got in zip(self.pattern, tokens):
            if want == "*":
                continue
            if isinstance(want, str):
                if want != got:
                    return False
            elif got not in want:
                return False
        return True

    def render(self) -> str:
        return " ".join(f"({'|'.join(t)})" if not isinstance(t, str) else t
                        for t in self.pattern)

    def to_dict(self) -> dict[str, Any]:
        return {"pattern": [list(t) if not isinstance(t, str) else t for t in self.pattern],
                "decision": self.decision.value, "justification": self.justification,
                "match": [list(m) for m in self.match],
                "not_match": [list(m) for m in self.not_match], "origin": self.origin}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "PrefixRule":
        pattern = tuple(tuple(t) if isinstance(t, list) else str(t) for t in d["pattern"])
        return cls(pattern=pattern, decision=Decision(d.get("decision", "allow")),
                   justification=str(d.get("justification", "")),
                   match=tuple(_tokenize(m) for m in d.get("match", ())),
                   not_match=tuple(_tokenize(m) for m in d.get("not_match", ())),
                   origin=str(d.get("origin", "policy_file")))


@dataclass(frozen=True, slots=True)
class PolicyEvaluation:
    decision: Decision
    rule: PrefixRule | None
    command: tuple[str, ...]

    @property
    def justification(self) -> str:
        return self.rule.justification if self.rule else ""

    def describe(self) -> str:
        if self.rule is None:
            return f"{self.decision.value}: no rule matched; default applies"
        why = f" — {self.rule.justification}" if self.rule.justification else ""
        return f"{self.decision.value} by rule [{self.rule.render()}]{why}"


#: The shipped default policy: psh v0.3's hardcoded denylist, now as rules with reasons.
DEFAULT_RULES: tuple[PrefixRule, ...] = (
    PrefixRule(("rm", ("-rf", "-fr", "-r", "-Rf", "-fR")), Decision.FORBIDDEN,
               "recursive delete is unrecoverable; move to a trash directory or delete "
               "specific files by name",
               match=(("rm", "-rf", "/"), ("rm", "-r", "build")), not_match=(("rm", "file.txt"),)),
    PrefixRule(("sudo",), Decision.FORBIDDEN,
               "privilege escalation is outside every profile; run the command without sudo "
               "or perform it manually",
               match=(("sudo", "apt", "install", "x"),), not_match=(("ls",),)),
    PrefixRule(("dd",), Decision.FORBIDDEN, "raw device writes; use a file-level tool",
               match=(("dd", "if=/dev/zero", "of=/dev/sda"),)),
    PrefixRule(("mkfs",), Decision.FORBIDDEN, "filesystem creation destroys the target",
               match=(("mkfs", "/dev/sda1"),)),
    PrefixRule((("shutdown", "reboot", "halt", "poweroff"),), Decision.FORBIDDEN,
               "system power state is not a research operation",
               match=(("shutdown", "-h", "now"), ("reboot",))),
    PrefixRule(("chmod", ("-R", "777")), Decision.FORBIDDEN,
               "world-writable recursive permissions; set permissions on the specific path",
               match=(("chmod", "-R", "777", "."), ("chmod", "777", "x")),
               not_match=(("chmod", "600", "key"),)),
    PrefixRule(("git", ("push", "reset", "checkout", "clean", "rebase")), Decision.PROMPT,
               "rewrites history or discards work; confirm before proceeding",
               match=(("git", "push", "--force"), ("git", "reset", "--hard")),
               not_match=(("git", "status"), ("git", "log"))),
    PrefixRule((("curl", "wget"),), Decision.PROMPT,
               "fetches remote content; the egress proxy governs the host, but confirm intent",
               match=(("curl", "https://x"),), not_match=(("cat", "f"),)),
    PrefixRule(("pip", ("install", "uninstall")), Decision.PROMPT,
               "changes the environment; confirm the package",
               match=(("pip", "install", "x"),), not_match=(("pip", "list"),)),
    PrefixRule((("ls", "cat", "head", "tail", "grep", "wc", "find", "echo", "pwd", "which"),),
               Decision.ALLOW, "read-only inspection",
               match=(("ls", "-la"), ("grep", "-rn", "x", ".")), not_match=(("rm", "x"),)),
    PrefixRule(("git", ("status", "log", "diff", "show", "branch")), Decision.ALLOW,
               "read-only repository inspection", match=(("git", "status"),)),
    PrefixRule(("python", ("-c", "-m")), Decision.PROMPT,
               "arbitrary code; confirm what it does", match=(("python", "-c", "print(1)"),),
               not_match=(("python", "--version"),)),
)


class ExecPolicy:
    """An ordered rule set with forbidden → prompt → allow evaluation."""

    def __init__(self, rules: Iterable[PrefixRule] = DEFAULT_RULES, *,
                 default: Decision = Decision.PROMPT) -> None:
        self.rules: list[PrefixRule] = list(rules)
        #: What happens when no rule matches. PROMPT by default: an unknown command is asked
        #: about, not silently run and not silently refused.
        self.default = default
        self.evaluations = 0
        self.amendments = 0

    def evaluate(self, command: str | Sequence[str]) -> PolicyEvaluation:
        """Return the decision for a command. Forbidden rules are consulted first, always."""
        self.evaluations += 1
        tokens = _tokenize(command)
        # Forbidden first, absolutely: a forbidden rule is checked against every command
        # before any prompt or allow rule gets a say, and specificity is irrelevant.
        for rule in self.rules:
            if rule.decision is Decision.FORBIDDEN and rule.matches(tokens):
                return PolicyEvaluation(Decision.FORBIDDEN, rule, tokens)
        # Between prompt and allow, the most specific (longest) matching pattern wins, so an
        # amendment "allow git push origin" relaxes "prompt on git push" — but nothing here
        # can ever reach a command a forbidden rule already refused.
        best: PrefixRule | None = None
        for rule in self.rules:
            if rule.decision is not Decision.FORBIDDEN and rule.matches(tokens):
                # >= : on equal specificity the LATER rule wins. Amendments are appended, so
                # a fresh "allow git push" beats the shipped "prompt git push" — the more
                # recent decision is the operative one.
                if best is None or len(rule.pattern) >= len(best.pattern):
                    best = rule
        if best is not None:
            return PolicyEvaluation(best.decision, best, tokens)
        return PolicyEvaluation(self.default, None, tokens)

    def add_rule(self, rule: PrefixRule) -> None:
        """Add a rule. Rule self-tests ran at construction; nothing further to validate."""
        self.rules.append(rule)
        if rule.origin.startswith("amendment"):
            self.amendments += 1

    # ------------------------------------------------------------- persistence
    def to_json(self) -> str:
        return json.dumps({"default": self.default.value,
                           "rules": [r.to_dict() for r in self.rules]}, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "ExecPolicy":
        """Load a policy. Every rule's examples are validated here; a bad rule refuses load."""
        data = json.loads(text)
        rules = [PrefixRule.from_dict(r) for r in data.get("rules", [])]
        return cls(rules, default=Decision(data.get("default", "prompt")))

    @classmethod
    def load(cls, path: Path) -> "ExecPolicy":
        return cls.from_json(Path(path).read_text(encoding="utf-8"))

    def save(self, path: Path) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8")

    def stats(self) -> dict[str, Any]:
        by = {d.value: sum(1 for r in self.rules if r.decision is d) for d in Decision}
        return {"rules": len(self.rules), "by_decision": by, "evaluations": self.evaluations,
                "amendments": self.amendments, "default": self.default.value}
