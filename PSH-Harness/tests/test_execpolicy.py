"""Declarative execution policy: rules that test themselves, in Claude Code's evaluation order."""

from __future__ import annotations

import pytest

from psh.kernel.execpolicy import (
    DEFAULT_RULES, Decision, ExecPolicy, PrefixRule, RuleValidationError,
)


def test_default_policy_loads_and_every_rule_passes_its_own_examples():
    """Every shipped rule declares match/not_match examples; loading validates them."""
    policy = ExecPolicy()
    assert len(policy.rules) == len(DEFAULT_RULES)
    assert all(r.match for r in DEFAULT_RULES), "every default rule must carry examples"


def test_a_rule_that_fails_its_own_example_is_refused_at_load():
    """The Codex property: match/not_match are unit tests, run at load time."""
    with pytest.raises(RuleValidationError, match="claims to match"):
        PrefixRule(("git", "push"), Decision.PROMPT, "x", match=(("git", "status"),))
    with pytest.raises(RuleValidationError, match="claims NOT to match"):
        PrefixRule(("git",), Decision.ALLOW, "x", not_match=(("git", "status"),))


def test_forbidden_requires_a_justification():
    with pytest.raises(RuleValidationError, match="justification"):
        PrefixRule(("rm", "-rf"), Decision.FORBIDDEN)


def test_evaluation_order_is_forbidden_then_prompt_then_allow():
    """Claude Code: first match in deny→ask→allow wins; specificity does not reorder."""
    policy = ExecPolicy([
        PrefixRule(("aws",), Decision.FORBIDDEN, "cloud CLI is out of scope",
                   match=(("aws", "s3", "ls"),)),
        # A NARROWER allow for the same command. It must not win.
        PrefixRule(("aws", "s3", "ls"), Decision.ALLOW, "read-only listing",
                   match=(("aws", "s3", "ls"),)),
    ])
    ev = policy.evaluate("aws s3 ls")
    assert ev.decision is Decision.FORBIDDEN
    assert "out of scope" in ev.justification


def test_a_more_specific_allow_relaxes_a_broader_prompt_but_never_a_forbidden():
    """The recorded departure from Claude Code, and the guarantee that is kept.

    An amendment must be able to turn "ask before git push" into "allow git push origin";
    under strict ask-before-allow it never could. But no specificity relaxes a forbidden.
    """
    policy = ExecPolicy([
        PrefixRule(("git",), Decision.PROMPT, "confirm git ops", match=(("git", "x"),)),
        PrefixRule(("git", "status"), Decision.ALLOW, "read-only", match=(("git", "status"),)),
        PrefixRule(("git", "push", "--force"), Decision.FORBIDDEN, "history rewrite",
                   match=(("git", "push", "--force"),)),
        PrefixRule(("git", "push", "--force", "origin"), Decision.ALLOW, "sneaky",
                   match=(("git", "push", "--force", "origin"),)),
    ])
    assert policy.evaluate("git status").decision is Decision.ALLOW
    assert policy.evaluate("git commit").decision is Decision.PROMPT
    assert policy.evaluate("git push --force origin").decision is Decision.FORBIDDEN, \
        "a narrower allow must never reach a command a forbidden rule refuses"


def test_alternatives_and_wildcards_match_as_documented():
    rule = PrefixRule(("chmod", ("-R", "777"), "*"), Decision.FORBIDDEN, "world-writable",
                      match=(("chmod", "-R", "anything"), ("chmod", "777", ".")),
                      not_match=(("chmod", "600", "key"), ("chmod", "-R")))
    assert rule.matches("chmod -R 777 /")
    assert not rule.matches("chmod 600 id_rsa")


def test_default_rules_refuse_the_v03_denylist_and_allow_inspection():
    policy = ExecPolicy()
    for cmd in ("rm -rf /", "sudo rm x", "dd if=/dev/zero of=/dev/sda", "mkfs /dev/sda1",
                "shutdown -h now", "chmod -R 777 ."):
        ev = policy.evaluate(cmd)
        assert ev.decision is Decision.FORBIDDEN, cmd
        assert ev.justification, f"forbidden without a reason: {cmd}"
    for cmd in ("ls -la", "git status", "grep -rn foo .", "cat README.md"):
        assert policy.evaluate(cmd).decision is Decision.ALLOW, cmd
    assert policy.evaluate("git push --force").decision is Decision.PROMPT
    assert policy.evaluate("some_unknown_binary --flag").decision is Decision.PROMPT, \
        "an unknown command is asked about, not silently run or refused"


def test_policy_round_trips_through_json_and_revalidates(tmp_path):
    policy = ExecPolicy()
    path = tmp_path / "policy.json"
    policy.save(path)
    loaded = ExecPolicy.load(path)
    assert len(loaded.rules) == len(policy.rules)
    assert loaded.evaluate("rm -rf /").decision is Decision.FORBIDDEN

    # Corrupt one rule's example on disk: load must refuse, not silently accept.
    import json
    data = json.loads(path.read_text())
    data["rules"][0]["match"] = [["ls"]]  # rm -rf rule now claims to match "ls"
    path.write_text(json.dumps(data))
    with pytest.raises(RuleValidationError):
        ExecPolicy.load(path)
