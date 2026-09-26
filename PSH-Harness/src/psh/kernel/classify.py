"""Data classification: assign a DataLabel to any value entering the system.

Composition rather than reimplementation. The predecessor package (``sable``) contains a
PHI detector validated at recall 1.00 on an adversarial corpus and specificity 1.00 on
benign research prose — 20 rules over 16 HIPAA categories, with semantic validators and a
research-identifier allowlist that keeps PMIDs, NCT numbers, rsIDs, p-values and
confidence intervals from being misread as identifiers. Rewriting that here would
duplicate the part of the predecessor that was actually correct and would lose the
validation behind it.

So this module *uses* that detector when the package is installed, and degrades to a
small built-in rule set when it is not. The built-in fallback is deliberately narrower and
says so: it exists so ``psh`` has no hard dependency, not so it can claim equivalence.

What is new here is the part the predecessor lacked: the detector's output becomes a
``DataLabel`` attached to the *value*, so it propagates through derivation. That is the
distinction between scanning a call's arguments and classifying data.
"""

from __future__ import annotations

import re
import math
import codecs
import binascii
import base64
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..labels import (
    DataLabel, Labeled, Sensitivity, combine,
)

__all__ = ["Classifier", "ClassificationResult", "SABLE_AVAILABLE"]


try:  # pragma: no cover - exercised by whichever branch the environment provides
    from sable.governance import PHIDetector as _SablePHIDetector
    SABLE_AVAILABLE = True
except Exception:  # pragma: no cover
    _SablePHIDetector = None  # type: ignore[assignment]
    SABLE_AVAILABLE = False


#: The hundred most common Chinese surnames, as a character class. A name rule anchored
#: only on 患者 flagged "患者出现头痛" as a name; anchoring the name on a surname keeps
#: ordinary clinical prose out while catching the record-shaped 患者张三 and 患者王五 男.
_CJK_SURNAMES = ("[王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾萧田董袁潘于蒋蔡余杜叶"
                 "程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎"
                 "贺顾毛郝龚邵万钱严覃武戴莫孔向汤肖]")

#: Fallback patterns, used only when sable is absent. Narrower than sable's rule set:
#: no semantic validators, no research-identifier allowlist. Kept minimal on purpose —
#: a half-built detector that looks complete is worse than one that admits its scope.
_FALLBACK_RULES: tuple[tuple[str, "re.Pattern[str]", Sensitivity], ...] = (
    ("medical_record_number",
     re.compile(r"\b(?:mrn|medical\s+record(?:\s+number)?)\s*[:#]?\s*(\d{5,12})\b", re.I),
     Sensitivity.PHI),
    ("social_security_number",
     re.compile(r"\b(?:ssn)\s*[:#]?\s*\d{3}-\d{2}-\d{4}\b", re.I), Sensitivity.PHI),
    ("date_of_birth",
     re.compile(r"\b(?:dob|date\s+of\s+birth)\s*[:#]?\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
                re.I), Sensitivity.PHI),
    ("email_address",
     re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b"), Sensitivity.PHI),
    ("name_cued",
     re.compile(r"\b(?i:patient|mr|mrs|ms)\.?\s*[:,]?\s+([A-Z][a-z]{1,20}\s+[A-Z][a-z]{1,20})\b"),
     Sensitivity.PHI),
    # --- Chinese clinical identifiers. A reviewer showed 患者张三，住院号：12345678，
    # 出生日期：1980-01-01 classified INTERNAL while its English counterpart was PHI, and
    # INTERNAL sits below the public-remote ceiling. Every rule is anchored on a cue or a
    # number shape so that research prose about 患者 in general is not flagged.
    ("chinese_patient_name",
     re.compile(r"(?:患者姓名|病人姓名|姓名)\s*[:：]\s*([\u4e00-\u9fff]{2,4})(?![\u4e00-\u9fff])"
                r"|(?:患者|病人|病员)\s*(" + _CJK_SURNAMES + r"[\u4e00-\u9fff]{1,2})"
                r"(?=[，,。；;：:（(）)\s]|$|男|女|\d)"),
     Sensitivity.PHI),
    ("chinese_record_number",
     re.compile(r"(?:住院号|病案号|病历号|门诊号|就诊号|登记号|住院病历号|门诊病历号|病人号)"
                r"\s*[:：]?\s*([A-Za-z]?\d{4,12})"),
     Sensitivity.PHI),
    ("chinese_resident_id",
     re.compile(r"(?:身份证号码|身份证号|身份证|公民身份号码)\s*[:：]?\s*(\d{15}|\d{17}[\dXx])"
                r"|(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])"
                r"\d{3}[\dXx](?![\dXx])"),
     Sensitivity.PHI),
    ("chinese_mobile_number",
     re.compile(r"(?<![\d.-])1[3-9]\d{9}(?![\d.-])"), Sensitivity.PHI),
    ("chinese_date_of_birth",
     re.compile(r"(?:出生日期|出生年月日|出生年月|生日)\s*[:：]?\s*"
                r"\d{4}\s*[-/.年]\s*\d{1,2}(?:\s*[-/.月]\s*\d{1,2}\s*日?)?"),
     Sensitivity.PHI),
    ("chinese_address",
     re.compile(r"(?:家庭住址|现住址|户籍地址|住址|地址)\s*[:：]?\s*"
                r"[\u4e00-\u9fff]{2,}(?:省|市|区|县|镇|乡|村|路|街|道|号|弄|巷)"),
     Sensitivity.PHI),
    ("name_cued_cjk",
     re.compile(r"\b(?i:patient(?:\s+name)?)\s*[:：]?\s*([\u4e00-\u9fff]{2,4})(?![\u4e00-\u9fff])"),
     Sensitivity.PHI),
)

#: Credential shapes. These are SECRET rather than PHI: a leaked key is a different and
#: broader failure than a leaked identifier.
_SECRET_RULES: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    ("api_key", re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}"
                           r"|AKIA[0-9A-Z]{16})\b")),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._-]{20,}\b")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    """The label plus which detector produced it, for auditability."""

    label: DataLabel
    detector: str
    categories: tuple[str, ...] = ()

    @property
    def sensitivity(self) -> Sensitivity:
        return self.label.sensitivity


# ------------------------------------------------------------ encoded-content handling
#
# My own audit showed base64-, hex- and rot13-encoded PHI classified as INTERNAL: the
# detector saw a blob with no identifier shape in it. A trivially reversible encoding must not
# launder a label, so classification tries the common encodings on token runs and rescans.
#
# Second rule: a token run that LOOKS encoded but does not decode to text cannot be PUBLIC.
# Content that cannot be inspected has unknown sensitivity, and unknown is not the same as
# clean — so it floors at SENSITIVE. It is not marked PHI, because that would be a guess.

_ENCODED_RUN = re.compile(r"(?<![A-Za-z0-9+/=])([A-Za-z0-9+/=_-]{24,}|[0-9a-fA-F]{32,})(?![A-Za-z0-9+/=])")
_MIN_PRINTABLE_FRACTION = 0.92


def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts: dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _try_decode(token: str) -> list[str]:
    """Return plausible plaintext decodings of a token, or [] if none decode to text."""
    out: list[str] = []
    candidates: list[bytes] = []
    stripped = token.strip("=")
    # hex
    if re.fullmatch(r"[0-9a-fA-F]+", token) and len(token) % 2 == 0:
        try:
            candidates.append(bytes.fromhex(token))
        except ValueError:
            pass
    # base64 / urlsafe base64 (urlsafe_b64decode takes no validate kwarg)
    padded = token + "=" * (-len(token) % 4)
    for decoder in (lambda t: base64.b64decode(t, validate=False), base64.urlsafe_b64decode):
        try:
            candidates.append(decoder(padded))
        except (binascii.Error, ValueError):
            pass
    for raw in candidates:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        printable = sum(ch.isprintable() or ch.isspace() for ch in text) / max(len(text), 1)
        if printable >= _MIN_PRINTABLE_FRACTION and len(text) >= 8:
            out.append(text)
    return out


def _rot13(text: str) -> str:
    return codecs.encode(text, "rot13")


#: Field-name stems that act as identifier cues. A key like ``mrn_suffix`` tells the reader
#: what the value is even when the value alone is four bare digits.
_FIELD_CUES: tuple[tuple[str, str], ...] = (
    (r"mrn|medical_?record|chart", "MRN"),
    (r"ssn|social", "SSN"),
    (r"dob|birth", "DOB"),
    (r"phone|tel|mobile", "phone"),
    (r"email", "email"),
    (r"first_?name|given|fname|^first$", "Patient first name"),
    (r"last_?name|surname|family|lname|^last$", "Patient last name"),
    (r"^name$|full_?name|patient_?name", "Patient name"),
    (r"address|street", "Address"),
    # Chinese field names. ``_recombine_fields`` writes the cue beside the value, so a
    # structured record whose keys are 姓名 / 住院号 / 身份证 is scanned by the same rules
    # as prose. Keys are lower-cased before matching; CJK is unaffected.
    (r"患者姓名|病人姓名|^姓名$|姓名", "Patient name"),
    (r"住院号|病案号|病历号|门诊号|就诊号|登记号", "住院号"),
    (r"身份证", "身份证号"),
    (r"手机|电话|联系方式|联系电话", "phone"),
    (r"出生日期|出生年月|生日", "出生日期"),
    (r"住址|地址", "住址"),
    (r"邮箱|电子邮件", "email"),
)


def _recombine_fields(payload: Mapping) -> str:
    """Render a structured payload so fragments split across fields recombine.

    Two things happen. Fields whose *names* carry an identifier cue get that cue written
    beside their value ("mrn_prefix: 0485" -> "MRN 0485"), because a detector that needs the
    cue adjacent to the value never sees it in the bare key/value form. And fields sharing a
    stem (``mrn_prefix`` + ``mrn_suffix``, ``ssn_1`` + ``ssn_2``) are concatenated in key order
    so a deliberately split identifier is reassembled before scanning.
    """
    parts: list[str] = []
    by_stem: dict[str, list[str]] = {}
    for key, val in payload.items():
        if isinstance(val, bool) or not isinstance(val, (str, int, float)):
            continue
        text = str(val)
        k = str(key).lower()
        cue = next((c for pat, c in _FIELD_CUES if re.search(pat, k)), None)
        parts.append(f"{cue} {text}" if cue else f"{key}: {text}")
        stem = re.sub(r"[_\-]?(?:prefix|suffix|part|first|second|last|\d+|a|b)$", "", k)
        if stem and stem != k:
            by_stem.setdefault(stem, []).append(text)
    for stem, frags in by_stem.items():
        if len(frags) > 1:
            cue = next((c for pat, c in _FIELD_CUES if re.search(pat, stem)), stem)
            parts.append(f"{cue} {''.join(frags)}")
    # Name fragments: a first and last name in separate fields form one name.
    firsts = [str(v) for k, v in payload.items()
              if isinstance(v, str) and re.search(r"first|given|fname", str(k).lower())]
    lasts = [str(v) for k, v in payload.items()
             if isinstance(v, str) and re.search(r"last|surname|family|lname", str(k).lower())]
    if firsts and lasts:
        parts.append(f"Patient: {firsts[0]} {lasts[0]}")
    return ". ".join(parts)


class Classifier:
    """Assigns a ``DataLabel`` to values entering the system.

    Parameters
    ----------
    default_sensitivity:
        What an unremarkable string is worth. ``INTERNAL`` rather than ``PUBLIC``, because
        the user's own prose is their working material and should not be treated as
        publishable by default. Text explicitly known to be public (a fetched abstract)
        is classified with ``origin="public_source"`` to override this.
    prefer_sable:
        Use the predecessor's validated detector when available.
    """

    #: Origin prefixes that mark a value as coming from a clinical record. The label a
    #: clinical source implies does not depend on whether a scan recognises the language
    #: the record is written in.
    CLINICAL_ORIGIN_PREFIXES: tuple[str, ...] = ("clinical", "ehr", "emr", "his", "patient_record",
                                                 "chart")

    def __init__(self, *, default_sensitivity: Sensitivity = Sensitivity.INTERNAL,
                 prefer_sable: bool = True) -> None:
        self.default_sensitivity = default_sensitivity
        self._phi = _SablePHIDetector() if (prefer_sable and SABLE_AVAILABLE) else None
        self.detector_name = "sable.PHIDetector" if self._phi else "psh.fallback"
        self.classifications = 0

    @classmethod
    def is_clinical_origin(cls, origin: str) -> bool:
        head = (origin or "").lower()
        return any(head == p or head.startswith(p + ":") or head.startswith(p + "_")
                   or head.startswith(p + "/") for p in cls.CLINICAL_ORIGIN_PREFIXES)

    @property
    def validated(self) -> bool:
        """True when the validated detector (sable) is in use rather than the fallback."""
        return self._phi is not None

    # ------------------------------------------------------------------ scanning
    def classify_text(self, text: str, *, origin: str = "") -> ClassificationResult:
        """Classify a string. Never returns a label lower than a detected finding."""
        self.classifications += 1
        if not text or not text.strip():
            return ClassificationResult(DataLabel(Sensitivity.PUBLIC, shareable=True,
                                                  rationale="empty", classifier=self.detector_name),
                                        self.detector_name)

        categories: list[str] = []
        sensitivity = (Sensitivity.PUBLIC if origin == "public_source"
                       else self.default_sensitivity)
        if self.is_clinical_origin(origin):
            # Provenance is a stronger fact than a regex miss. A value the caller says came
            # from a chart, an EHR export or a patient form starts at PHI; the scan below
            # can only raise it further.
            categories.append("clinical_source")
            sensitivity = max(sensitivity, Sensitivity.PHI)

        for name, pattern in _SECRET_RULES:
            if pattern.search(text):
                categories.append(name)
                sensitivity = max(sensitivity, Sensitivity.SECRET)

        if self._phi is not None:
            report = self._phi.scan(text)
            if not report.clean:
                categories.extend(sorted(report.categories()))
                # sable distinguishes DIRECT from INDIRECT findings; a quasi-identifier
                # alone is SENSITIVE rather than PHI, which keeps de-identified research
                # data usable while still restricting it.
                level = getattr(report.level, "name", "DIRECT")
                sensitivity = max(sensitivity,
                                  Sensitivity.PHI if level == "DIRECT" else Sensitivity.SENSITIVE)
        else:
            for name, pattern, level in _FALLBACK_RULES:
                if pattern.search(text):
                    categories.append(name)
                    sensitivity = max(sensitivity, level)

        rationale = (f"{len(categories)} finding(s) via {self.detector_name}"
                     if categories else f"no findings via {self.detector_name}")
        label = DataLabel(
            sensitivity=sensitivity, categories=tuple(dict.fromkeys(categories)),
            rationale=rationale, classifier=self.detector_name,
            shareable=sensitivity <= Sensitivity.RESEARCH_DEIDENTIFIED
                      and sensitivity is not Sensitivity.INTERNAL)
        return ClassificationResult(label, self.detector_name, tuple(categories))

    def classify(self, value: Any, *, origin: str = "") -> Labeled:
        """Return ``value`` wrapped with its label, recursing into containers.

        Already-labelled input is returned unchanged: classification is idempotent, so a
        value cannot be laundered by being re-classified after derivation.
        """
        if isinstance(value, Labeled):
            return value
        label = self._label_any(value, origin=origin)
        return Labeled(value=value, label=label, origin=origin)

    def _label_text_with_decoding(self, text: str, *, origin: str = "") -> DataLabel:
        """Classify text, then classify anything it decodes to, and take the join."""
        label = self.classify_text(text, origin=origin).label
        # rot13 is its own inverse; scanning the rotated text catches rot13-encoded PHI
        # without needing to detect that rot13 was applied.
        rotated = _rot13(text)
        if rotated != text:
            label = label.merged_with(self.classify_text(rotated, origin=origin).label)
        for match in _ENCODED_RUN.finditer(text):
            token = match.group(1)
            decoded = _try_decode(token)
            if decoded:
                for plain in decoded:
                    label = label.merged_with(self.classify_text(plain, origin=origin).label)
            elif len(token) >= 40 and _shannon_entropy(token) > 4.0:
                # Looks encoded or encrypted, does not decode to text: uninspectable.
                label = label.merged_with(DataLabel(
                    Sensitivity.SENSITIVE,
                    rationale="uninspectable high-entropy content; unknown is not clean",
                    classifier=self.detector_name))
        return label

    def _label_any(self, value: Any, *, origin: str = "") -> DataLabel:
        if isinstance(value, str):
            return self._label_text_with_decoding(value, origin=origin)
        if isinstance(value, Mapping):
            labels = [self._label_any(k, origin=origin) for k in value.keys()]
            labels += [self._label_any(v, origin=origin) for v in value.values()]
            # Fields that are clean alone may identify together: {"first": "Alice",
            # "last": "Cheng", "mrn_prefix": "0485", "mrn_suffix": "1923"}. Classify the
            # JOINED text of the payload as well, so co-located fragments recombine. This
            # does not catch fragments split across separate calls — stated in
            # coverage_note().
            labels.append(self.classify_text(_recombine_fields(value), origin=origin).label)
            return combine(*labels) if labels else DataLabel(Sensitivity.PUBLIC,
                                                             shareable=True)
        if isinstance(value, (list, tuple, set, frozenset)):
            labels = [self._label_any(v, origin=origin) for v in value]
            return combine(*labels) if labels else DataLabel(Sensitivity.PUBLIC,
                                                             shareable=True)
        if isinstance(value, (int, float, bool)) or value is None:
            # A bare number carries no identifier on its own. Its sensitivity comes from
            # derivation, which the label lattice handles.
            return DataLabel(Sensitivity.PUBLIC, shareable=True,
                             rationale="non-textual scalar", classifier=self.detector_name)
        return self.classify_text(str(value), origin=origin).label

    def coverage_note(self) -> str:
        """State honestly what this classifier does and does not detect."""
        if self._phi is not None:
            return (
                "Classification uses sable.PHIDetector (20 rules, 16 HIPAA categories, "
                "validated at recall 1.00 / specificity 1.00 on its published corpus) plus "
                "credential-shape rules. It does NOT detect names without a contextual "
                "cue, free-text geography below state level, photographs, or HIPAA's "
                "18th 'any other unique identifying characteristic' category. A PUBLIC or "
                "INTERNAL label means no configured detector matched — it is not a "
                "certification of de-identification. Reversible encodings (base64, hex, "
                "rot13) are decoded and rescanned; uninspectable high-entropy content floors "
                "at SENSITIVE. Identifier fragments split across fields of ONE payload are "
                "recombined; fragments split across SEPARATE calls are not.")
        return (
            "sable is not installed, so classification uses psh's built-in fallback: 5 "
            "PHI patterns and 3 credential shapes, with NO semantic validators and NO "
            "research-identifier allowlist. It will miss identifiers sable would catch "
            "and may flag research text sable would not. Install sable for the validated "
            "detector.")
