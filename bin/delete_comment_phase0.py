#!/usr/bin/env python3
"""Phase-0 measurement for plan-coder issue #142 (``/delete-comment``).

Phase 0 is *measurement only*.  Nothing in this script deletes, edits or
refactors a comment, and nothing in this script is wired into the live
harness: no change to ``bin/agent.py``, no prompt change, no config change,
no new dependency, no lint rule, no issue filing.

What it does
------------
1. **Census** -- extract every comment *block* from ``bin/*.py`` with a
   deterministic extractor (``tokenize``-based, no heuristics on raw text
   scanning) and report the counts.
2. **Classification** -- assign every block to exactly one of the five
   categories defined in issue #142:

   * ``Tautology``          -- the comment restates what the code does.
   * ``Excuse``             -- the comment excuses a hack (the real find).
   * ``Stale``              -- the comment describes code that no longer exists.
   * ``DecisionRecord``     -- the comment records a non-obvious decision.
   * ``KeepProvenanceInvariant`` -- the comment carries provenance or an
     invariant (equation/spec source, external reference, units, licence
     header, "do not edit" marker).  Keep blocks are never deletion
     candidates.

3. **Excuse clustering** -- group ``Excuse`` blocks that share the same
   underlying hack/excuse into one cluster and report the raw excuse count
   versus the cluster count (the "flood ratio").

4. **Ledger** -- write one JSON record per comment block (file, start line,
   category, verbatim evidence quote, rationale note, cluster) to
   ``docs/spikes/delete-comment-phase0-runs.json``.

Classification policy (issue #142 sections 1-3)
---------------------------------------------
* The five categories above are mutually exclusive; a block gets exactly one.
* ``Keep`` wins ties.  When a block could be ``Keep`` *or* something else it
  is classified ``Keep`` and the reason is written into the record's note.
  False-Keeps are safe (the block survives); false-deletes are the hard-stop
  error class, so the classifier is deliberately biased towards ``Keep``.
* Every record carries a verbatim evidence quote taken from the block itself.
  A record without an evidence quote does not count.
* This script is the *classifier*, not the *adjudicator*.  The agreement and
  false-delete PASS bars of issue #142 section 4 are adjudicated
  independently by the main agent / Alex; this tool only presents the
  classification.

Usage
-----
    python3 bin/delete_comment_phase0.py --json
    python3 bin/delete_comment_phase0.py --out docs/spikes/delete-comment-phase0-runs.json
    python3 bin/delete_comment_phase0.py --summary

Options:
    --repo-root DIR    Repository root (default: parent of this file).
    --glob GLOB        File glob to census (default: ``bin/*.py``).
    --out PATH         Write the JSON ledger to PATH.
    --json             Print the full census/ledger as JSON on stdout.
    --summary          Print a short human-readable summary on stdout.
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import re
import sys
import tokenize
from dataclasses import dataclass, field

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_GLOB = "bin/*.py"
DEFAULT_LEDGER = os.path.join("docs", "spikes", "delete-comment-phase0-runs.json")

# Rough English/code token ratio used to turn characters into tokens.  The
# repo's other Phase-0 spikes (bin/headroom_phase0.py) use the same constant.
CHARS_PER_TOKEN = 4.0

CATEGORIES = (
    "Tautology",
    "Excuse",
    "Stale",
    "DecisionRecord",
    "KeepProvenanceInvariant",
)

# --------------------------------------------------------------------------
# extractor
# --------------------------------------------------------------------------


@dataclass
class CommentBlock:
    """A maximal run of consecutive comment-only lines."""

    file: str
    start_line: int
    end_line: int
    lines: list = field(default_factory=list)

    @property
    def text(self) -> str:
        """The block's comment text, without the leading ``#`` markers."""
        out = []
        for raw in self.lines:
            stripped = raw.strip()
            if stripped.startswith("#"):
                stripped = stripped[1:]
            out.append(stripped.strip())
        return "\n".join(out)

    @property
    def evidence(self) -> str:
        """The decisive evidence quote: the first non-empty comment line."""
        for raw in self.lines:
            stripped = raw.strip()
            if stripped.startswith("#"):
                stripped = stripped[1:]
            stripped = stripped.strip()
            if stripped:
                return stripped
        return ""

    @property
    def key(self) -> str:
        """Stable identity of a block: file plus start line."""
        return f"{self.file}:{self.start_line}"


def _is_shebang(line: str) -> bool:
    return line.startswith("#!")


def _is_encoding_declaration(line: str) -> bool:
    """PEP 263 encoding declaration (also matched by the ``# -*- coding:`` form)."""
    lowered = line.lower()
    if "coding" not in lowered:
        return False
    return "coding:" in lowered or "coding=" in lowered


def comment_only_lines(source: str) -> tuple[dict, dict]:
    """Return ``(comment_rows, special_rows)`` for one Python source string.

    ``comment_rows`` maps a 1-based line number to the number of ``#`` comment
    tokens that start on that line *and* that are the only tokens on the line
    (a trailing comment after code is a comment token too, but its line also
    carries code and is therefore not a comment-only line).

    ``special_rows`` maps a line number to ``"shebang"`` or ``"encoding"`` for
    the two lines that are comment-like but are not comment blocks.
    """
    comment_rows: dict[int, int] = {}
    code_rows: set[int] = set()
    special_rows: dict[int, str] = {}

    raw_lines = source.splitlines()
    for idx, raw in enumerate(raw_lines, start=1):
        if _is_shebang(raw):
            special_rows[idx] = "shebang"
        elif idx <= 2 and _is_encoding_declaration(raw):
            special_rows[idx] = "encoding"

    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # A file that does not tokenize is reported as-is; the census then
        # covers only the lines we can read confidently.
        tokens = []

    for tok in tokens:
        if tok.type == tokenize.COMMENT:
            row = tok.start[0]
            comment_rows[row] = comment_rows.get(row, 0) + 1
        elif tok.type in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
                          tokenize.DEDENT, tokenize.ENDMARKER,
                          tokenize.ENCODING):
            continue
        else:
            code_rows.add(tok.start[0])

    return comment_rows, code_rows, special_rows


def extract_blocks(source: str, file_label: str) -> list:
    """Extract the comment blocks of one Python source string.

    A *block* is a maximal run of consecutive comment-only lines.  Docstrings
    are not blocks.  Trailing (inline) comments are not blocks.  The shebang
    line and a PEP 263 encoding declaration are recorded separately and are
    not blocks.
    """
    comment_rows, code_rows, special_rows = comment_only_lines(source)
    raw_lines = source.splitlines()

    block_rows = []
    for row in sorted(comment_rows):
        if row in special_rows:
            continue
        if row in code_rows:
            # A line that carries code as well as a comment is an inline
            # comment, not a comment-only line.
            continue
        block_rows.append(row)

    blocks = []
    current: list[int] = []
    for row in block_rows:
        if current and row == current[-1] + 1:
            current.append(row)
            continue
        if current:
            blocks.append(_make_block(raw_lines, current, file_label))
        current = [row]
    if current:
        blocks.append(_make_block(raw_lines, current, file_label))
    return blocks


def _make_block(raw_lines: list, rows: list, file_label: str) -> CommentBlock:
    return CommentBlock(
        file=file_label,
        start_line=rows[0],
        end_line=rows[-1],
        lines=[raw_lines[row - 1] for row in rows],
    )


def census_files(repo_root: str, pattern: str = DEFAULT_GLOB) -> list:
    """Return the sorted list of files matching ``pattern`` under ``repo_root``."""
    return sorted(glob.glob(os.path.join(repo_root, pattern)))


def census(repo_root: str = REPO_ROOT, pattern: str = DEFAULT_GLOB) -> list:
    """Return every comment block in the census population, in file order."""
    blocks: list[CommentBlock] = []
    for path in census_files(repo_root, pattern):
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        label = os.path.relpath(path, repo_root)
        blocks.extend(extract_blocks(source, label))
    return blocks


# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------

# The Phase-0 classifier's table.  It was written by reading every block the
# extractor produced (the extractor's output is the only input), one entry
# per block, keyed by ``<file>:<start line>``.  The codes are:
#
#   K  KeepProvenanceInvariant
#   D  DecisionRecord
#   S  Stale
#   E  Excuse
#   T  Tautology
#
# The keyword rules further down are the *fallback* for a block that is not
# in this table (for example when bin/ changes after this measurement).  The
# ledger records which path classified each block (``method``).
CLASSIFICATION_TABLE = {
    "bin/agent.py":
        "76:K 102:D 132:K 139:K 142:K 291:K 306:D 310:D 313:K 350:K 540:K "
        "550:D 560:K 660:D 698:T 702:T 738:D 774:D 851:D 876:D 995:T 1006:D "
        "1067:K 1088:K 1106:T 1126:K 1139:K 1165:K 1172:T 1192:D 1261:K "
        "1274:T 1287:T",
    "bin/ast_grep_phase0_scan.py": "52:K 76:T 97:T 115:D 133:K",
    "bin/attention.py": "92:T 151:T 209:T 305:K 314:T 415:T",
    "bin/codemap.py":
        "55:T 58:K 61:K 64:D 67:T 70:T 73:T 75:K 87:T 98:K 173:D 190:T 248:T "
        "323:T 458:T 470:D 482:D 605:T 631:T 643:T 756:T",
    "bin/contract_check.py": "48:T 78:K 87:K 102:K 111:K 125:K 148:K",
    "bin/dashboard.py":
        "71:K 73:K 77:E 86:K 91:K 94:T 96:K 99:T 103:T 160:T 199:T 383:T "
        "423:T 436:T 518:T 672:T 705:T 728:T 795:T 951:T 1055:T 1084:T 1115:T "
        "1387:T 1391:T 1518:T 1664:T",
    "bin/delete_comment_phase0.py":
        "77:K 89:T 169:K 203:T 247:T 251:K 343:K 404:K 439:K 527:K 620:T",
    "bin/dry_run.py": "177:D",
    "bin/edd.py": "299:T",
    "bin/experiments.py":
        "71:K 83:K 89:T 203:T 275:K 332:T 340:T 356:T 449:T 527:T",
    "bin/factory_failure_tickets.py":
        "94:K 107:D 125:T 173:T 247:T 335:T 396:K",
    "bin/filesize_phase0.py": "66:K 71:D 83:T 256:T 411:T 674:T 713:T",
    "bin/flagbatch.py": "47:K",
    "bin/goldfish.py": "89:D 96:K 133:T 157:T 223:T 269:T 288:T",
    "bin/grade_run.py": "69:K 319:T 329:T 343:T 453:D",
    "bin/headroom_phase0.py":
        "69:K 88:K 94:K 98:K 101:E 106:K 110:T 114:T 231:T 270:T 282:K 292:K "
        "304:T 474:K 562:T",
    "bin/jittest.py":
        "168:K 207:T 260:T 426:T 615:T 664:K 741:T 810:T 854:T 914:T 1060:T "
        "1142:T 1185:T",
    "bin/judging_files.py": "140:T",
    "bin/metrics_rollup.py":
        "66:K 74:T 86:T 194:T 353:D 360:K 422:T 519:T 624:T 629:T",
    "bin/plan_coder.py":
        "61:D 77:K 81:K 86:K 104:T 109:T 181:T 253:D 272:T 435:T 486:T 593:T "
        "709:T",
    "bin/plans.py": "288:K",
    "bin/ponytail_phase0.py":
        "68:K 102:T 143:T 192:T 460:D 468:T 505:T 569:D 619:T 708:T",
    "bin/prompt_check.py":
        "60:K 69:K 93:K 137:K 140:K 149:T 159:D 185:K 202:T 239:T 266:D "
        "303:T 435:T 481:T 508:D",
    "bin/prompt_check_harness_tune.py": "35:K 87:K",
    "bin/prompt_check_supervisor.py": "36:K 51:K 173:K 246:K",
    "bin/provenance_phase0.py":
        "73:T 76:K 83:E 100:T 162:T 360:T 433:T 519:T 524:T 529:T 533:T "
        "633:E 639:T 665:T 843:T 885:T 917:T 940:T",
    "bin/read_guard_phase0.py": "45:K 48:K 122:D 132:K",
    "bin/receipt.py": "120:K",
    "bin/state_write.py": "92:T",
    "bin/structural_discovery_phase0.py":
        "78:K 98:T 135:T 142:K 165:T 297:T 407:T 429:T 569:D 613:T 672:T "
        "724:T 841:K",
    "bin/sync_public.py": "67:D 93:K 134:T 141:T 150:T 160:T",
    "bin/test_tamper.py":
        "58:T 73:D 90:T 195:T 200:T 207:K 220:T 308:K 322:K 373:T 404:T 422:T "
        "446:T 451:T 462:T",
    "bin/tokeneconomizer_phase0.py":
        "71:T 79:T 116:T 125:K 131:T 331:T 389:T 402:T 479:T 514:T 535:K "
        "559:T 625:K 680:T 691:T 733:T",
    "bin/trajectory_coupling_phase0.py":
        "73:T 195:T 259:T 396:T 471:T 508:T 544:T 556:T 577:T 595:T 633:T "
        "691:T 696:D 702:T 895:T",
    "bin/verify_stamp.py": "82:T 212:T 323:T 388:T",
    "bin/worktree_slots.py": "188:D 256:K",
}

CATEGORY_BY_CODE = {
    "K": "KeepProvenanceInvariant",
    "D": "DecisionRecord",
    "S": "Stale",
    "E": "Excuse",
    "T": "Tautology",
}

# Per-block rationale notes.  A block without an entry gets the generic note
# of its category; the entries below record the decisive reason.
BLOCK_NOTES = {
    "bin/agent.py:102":
        "records why the malformed-plan validation sits outside the parse guard",
    "bin/agent.py:132":
        "states the capacity invariant (legacy providers keep their order)",
    "bin/agent.py:291":
        "provenance: issue #52 + the Phase-1 spike doc + the measured basis",
    "bin/agent.py:306":
        "records where the cap came from (the Phase-1 report measurement)",
    "bin/agent.py:550":
        "records the measurement behind the mid-run no-edit gate",
    "bin/agent.py:876":
        "records the 2026-10-26 write fault that forced the temp-file write",
    "bin/agent.py:1192":
        "records why the base commit is pinned before the worker starts",
    "bin/ast_grep_phase0_scan.py:115":
        "records why the C5 candidate was kept inline and not shipped",
    "bin/codemap.py:64":
        "records why a too-common name is not mapped (noise, not coverage)",
    "bin/codemap.py:173":
        "records why the trigger-word pre-filter keeps the build linear",
    "bin/codemap.py:470":
        "records why the indexes are built after the bulk insert",
    "bin/codemap.py:482":
        "records why the throwaway index may use the fastest safe journal",
    "bin/dashboard.py:77":
        "excuses the 2h cycle bucketing: the ledger carries no cycle id",
    "bin/dashboard.py:86":
        "provenance: the derived rollup is bin/metrics_rollup.py and optional",
    "bin/experiments.py:71":
        "provenance: METRICS.md section 6 defines the verdict vocabulary",
    "bin/factory_failure_tickets.py:94":
        "provenance: the supervisor prompt quotes these exact strings",
    "bin/grade_run.py:453":
        "records why the deliverable check never moves the score",
    "bin/headroom_phase0.py:101":
        "excuses the measurement: a capped run's share is only a lower bound",
    "bin/metrics_rollup.py:66":
        "provenance: the shepherd ledger starts 2026-11-09 (spec section 2)",
    "bin/plans.py:288":
        "states the lock invariant checked before route availability",
    "bin/prompt_check.py:159":
        "records why the render values are synthetic and hermetic",
    "bin/prompt_check.py:266":
        "records the 2026-11-08 incident that made the whole file scanned",
    "bin/provenance_phase0.py:83":
        "excuses the path-shape heuristic: a hint, never proof",
    "bin/provenance_phase0.py:633":
        "excuses the automatic label: it was too coarse, so a human checked",
    "bin/read_guard_phase0.py:122":
        "records how the saturated-read upper bound is derived",
    "bin/sync_public.py:67":
        "records why the forbidden literals are assembled from fragments",
    "bin/test_tamper.py:73":
        "records why the path check pads a leading slash",
    "bin/worktree_slots.py:188":
        "records why a stale slot is dropped and reported not found",
}

# Excuse clusters (issue #142 section 3 part B): the same underlying
# hack/excuse lands in one cluster.
EXCUSE_CLUSTERS = {
    "bin/dashboard.py:77": "accepted-approximation",
    "bin/headroom_phase0.py:101": "measurement-lower-bound",
    "bin/provenance_phase0.py:83": "heuristic-not-proof",
    "bin/provenance_phase0.py:633": "heuristic-not-proof",
}

GENERIC_NOTES = {
    "KeepProvenanceInvariant":
        "carries provenance, a reference, a config/spec value or an invariant",
    "DecisionRecord":
        "records a non-obvious choice and why it was made",
    "Stale":
        "describes code or behaviour that no longer exists",
    "Excuse":
        "excuses a hack, an approximation or a shortcut",
    "Tautology":
        "restates what the surrounding code does",
}


def table_lookup(file_label: str) -> dict:
    """Return the ``{start_line: code}`` map for one file of the table."""
    raw = CLASSIFICATION_TABLE.get(file_label)
    if raw is None:
        return {}
    out = {}
    for item in raw.split():
        line, code = item.split(":")
        out[int(line)] = code
    return out


# Keyword evidence for each category.  The rules are applied in a fixed
# priority order (Keep first, then DecisionRecord, Stale, Excuse, Tautology)
# so the result is deterministic.
#
# Each entry is a (pattern, note) pair: the pattern is a case-insensitive
# regular expression matched against the block text; the note is the reason
# written into the ledger.
KEEP_RULES = (
    (r"\bspdx\b|licen[cs]e|copyright", "licence/copyright header"),
    (r"coding[:=]", "encoding declaration"),
    (r"\bspec(?:ification)?\b|\brfc\b", "external spec reference"),
    (r"\bequation\b|\bformula\b|\bderived from\b", "equation/formula source"),
    (r"\bunits?\b|\bms\b|\bseconds\b|\bbytes\b|\bhz\b", "units"),
    (r"\bsee (?:bin|docs|tests|config|prompts|schemas)/", "pointer to another file"),
    (r"https?://|www\.", "external reference"),
    (r"\bdo not edit\b|\bdon't edit\b|\bdo not modify\b|\bgenerated\b"
     r"|\bauto-?generated\b", "generated/do-not-edit marker"),
    (r"\bmust\b|\bnever\b|\binvariant\b|\brequired\b|\bonly if\b", "stated invariant"),
    (r"\bconfig(?:uration)?\b|\bdeployment\.local|\benv(?:ironment)? var",
     "configuration reference"),
    (r"\bschema\b|\bfield\b|\bcolumn\b|\bkey\b", "schema/field reference"),
    (r"\bper\b.*\bspec\b|\baccording to\b", "provenance reference"),
    (r"\bexample\b|\bsample\b|\bformat(?:ted)? as\b", "format/example reference"),
    (r"\bphase[- ]?\d\b|\bphase 0\b", "phase/provenance marker"),
    (r"\bsee also\b|\brefer to\b|\bcf\.\b", "cross-reference"),
)

DECISION_RULES = (
    (r"\bbecause\b|\bsince we\b", "rationale"),
    (r"\bdecision\b|\bdecided\b|\bwe chose\b|\bchose\b|\bchoice\b",
     "explicit decision record"),
    (r"\bintentional(?:ly)?\b|\bon purpose\b|\bdeliberate(?:ly)?\b",
     "deliberate choice"),
    (r"\binstead of\b|\brather than\b|\btrade-?off\b|\balternative to\b",
     "rejected alternative"),
    (r"\bpolicy\b|\bconvention\b|\brule:\b", "policy/convention record"),
    (r"\bwe (?:use|keep|keep it|prefer)\b", "house convention"),
)

STALE_RULES = (
    (r"\bno longer\b|\bnot used any ?more\b|\bunused now\b", "describes removed code"),
    (r"\bused to\b|\bformerly\b|\bpreviously\b|\bonce was\b", "past-tense description"),
    (r"\bnow (?:lives?|is|are) in\b|\bmoved to\b|\bwas moved\b", "moved elsewhere"),
    (r"\bremoved (?:in|from|by)\b|\bdeleted\b|\bdropped\b", "removed code"),
    (r"\bwas (?:called|named)\b|\bold name\b", "renamed code"),
    (r"\bback (?:when|before)\b|\bin the old\b", "historical note"),
)

EXCUSE_RULES = (
    (r"\bhack(?:y|ed)?\b|\bkludge\b|\bwork-?around\b", "names a hack"),
    (r"\btemporar(?:y|ily)\b|\bfor now\b|\bquick(?: and dirty)?\b",
     "temporary concession"),
    (r"\btodo\b|\bfixme\b|\bxxx\b|\bto-?do\b", "unfinished-work marker"),
    (r"\bugly\b|\bgross\b|\bdirty\b|\bmeh\b|\bmeh\b", "apologetic tone"),
    (r"\bugh\b|\bsigh\b|\bsorry\b|\bapologi[sz]e\b", "apologetic tone"),
    (r"\bunfortunately\b|\bafraid\b|\bbummer\b|\boops\b", "apologetic tone"),
    (r"\bno idea\b|\bdon'?t know\b|\bnot sure\b|\bconfus(?:ed|ing)\b",
     "uncertainty"),
    (r"\bwhy\?|\bhow\?\?|\bwhat\?\?|\bweird\b|\bstrange\b|\bodd\b",
     "bewilderment"),
    (r"\bshould be\b|\bshould have\b|\bwould be better\b|\bmust have\b",
     "unfulfilled intent"),
    (r"\bleave (?:this|it|that)\b|\bdon'?t touch\b|\bdo not touch\b",
     "keep-the-hack instruction"),
    (r"\bdup(?:licate|d)\b|\bcopy-?paste\b|\bcopied\b", "duplication excuse"),
    (r"\bjust\b|\beasi(?:er|est)\b|\bsimpl(?:e|est)\b|\bquick\b",
     "convenience excuse"),
    (r"\binline\b|\bone-?liner\b|\binline\b", "inline shortcut"),
    (r"\bfast(?:er)?\b|\bspeed\b|\bperf(?:ormance)?\b|\boptimi[sz]e",
     "performance excuse"),
    (r"\bbecause (?:it|of) (?:works|is easier)\b", "convenience excuse"),
    (r"\bignore\b|\bskip(?:ping)?\b|\bnot (?:worth|needed)\b", "skip excuse"),
    (r"\bcareful\b|\bbeware\b|\bwarning\b|\bdanger\b|\btrap\b|\bgotcha\b"
     r"|\bpitfall\b|\bfragile\b", "hazard warning without a fact"),
    (r"\bnever\b|\bdon'?t\b|\balways\b", "imperative without a reason"),
    (r"\bcouldn'?t\b|\bcan'?t\b|\bcould not\b|\bcannot\b", "inability excuse"),
    (r"\bunless\b|\bexcept when\b", "exception carve-out"),
)

TAUTOLOGY_RULES = (
    (r"\b(increment|decrement|loop over|iterate over|set|assign|call|"
     r"return|append|print|read|write|open|close|parse|check|create|"
     r"remove|delete|sort|filter|count)\b", "restates an operation"),
    (r"\bthis (?:function|method|loop|block|section|list|dict|file)\b",
     "labels the surrounding code"),
    (r"\bimport\b", "restates an import"),
)

# Excuse cluster themes: the same underlying hack/excuse must land in one
# cluster.  The theme is chosen by the first matching pattern.
CLUSTER_RULES = (
    ("legacy-code", r"\blegacy\b|\bold code\b|\bdeprecated\b"),
    ("temporary-hack", r"\btemporar(?:y|ily)\b|\bfor now\b|\bquick(?: and dirty)?\b"),
    ("unfinished-work", r"\btodo\b|\bfixme\b|\bxxx\b|\bshould be\b|\bshould have\b"),
    ("uncertainty", r"\bno idea\b|\bdon'?t know\b|\bnot sure\b|\bconfus"),
    ("apologetic-tone", r"\bugly\b|\bgross\b|\bdirty\b|\bmeh\b|\bugh\b|\bsigh\b"
     r"|\bsorry\b|\bapolog|\bunfortunately\b|\bbummer\b"),
    ("bewilderment", r"\bwhy\?|\bweird\b|\bstrange\b|\bodd\b|\bwhat\??"),
    ("duplication", r"\bdup(?:licate|d)\b|\bcopy-?paste\b|\bcopied\b"),
    ("convenience", r"\bjust\b|\beasi(?:er|est)\b|\bsimpl(?:e|est)\b"),
    ("inline-shortcut", r"\binline\b|\bone-?liner\b"),
    ("performance", r"\bperf(?:ormance)?\b|\boptimi[sz]e|\bfast(?:er)?\b"),
    ("hazard-warning", r"\bcareful\b|\bbeware\b|\bwarning\b|\bdanger\b|\btrap\b"
     r"|\bgotcha\b|\bpitfall\b|\bfragile\b"),
    ("inability", r"\bcouldn'?t\b|\bcan'?t\b|\bcould not\b|\bcannot\b"),
    ("keep-the-hack", r"\bleave (?:this|it|that)\b|\bdon'?t touch\b"
     r"|\bdo not touch\b"),
    ("skip", r"\bignore\b|\bskip(?:ping)?\b|\bnot (?:worth|needed)\b"),
    ("excuse-marked", r"\bhack|\bwork-?around\b|\bkludge\b"),
)


def _match(rules: tuple, text: str):
    """Return the first ``(note, matched_text)`` whose pattern hits ``text``."""
    for pattern, note in rules:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return note, match.group(0)
    return None, None


def classify(block: CommentBlock) -> dict:
    """Classify one block into exactly one of the five categories.

    The classifier table is consulted first (the Phase-0 reading of every
    block).  The keyword rules are the deterministic fallback for a block the
    table does not cover.
    """
    table = table_lookup(block.file)
    code = table.get(block.start_line)
    if code is not None:
        category = CATEGORY_BY_CODE[code]
        note = BLOCK_NOTES.get(block.key, GENERIC_NOTES[category])
        return {"category": category, "note": note, "cluster": "",
                "method": "table"}

    text = block.text
    for rules, category in (
        (KEEP_RULES, "KeepProvenanceInvariant"),
        (DECISION_RULES, "DecisionRecord"),
        (STALE_RULES, "Stale"),
        (EXCUSE_RULES, "Excuse"),
        (TAUTOLOGY_RULES, "Tautology"),
    ):
        note, matched = _match(rules, text)
        if note:
            return {
                "category": category,
                "note": f"{note} ({matched.strip()!r})",
                "cluster": "",
                "method": "rule-fallback",
            }
    return {
        "category": "Tautology",
        "note": "no provenance marker found; treated as a restatement",
        "cluster": "",
        "method": "rule-fallback",
    }


def stale_hints(block: CommentBlock, code_lines: list) -> list:
    """Return identifiers the block names that do not occur in the file's code.

    A non-empty result is a *hint* that the comment may be stale (it names
    something the file no longer contains).  It is recorded in the ledger so
    the Stale count can be audited; it never decides a category by itself.
    """
    body = "\n".join(code_lines)
    words = set(re.findall(r"\b[a-z][a-z0-9_]*_[a-z0-9_]+\b", block.text))
    return sorted(word for word in words if word not in body)


def cluster_for(block: CommentBlock) -> str:
    """Return the excuse-cluster theme of an excuse block."""
    text = block.text
    for theme, pattern in CLUSTER_RULES:
        if re.search(pattern, text, re.IGNORECASE):
            return theme
    return "uncategorised-excuse"


# --------------------------------------------------------------------------
# ledger
# --------------------------------------------------------------------------


ADJUDICATION_TOKENS_PER_BLOCK = 40
STAGING_STUDIO_FILES = 77
STAGING_STUDIO_COMMENT_LINES = 2643


def project_audit(blocks: int, comment_lines: int, comment_chars: int) -> dict:
    """Extrapolate one full audit of staging-studio ``src/components``.

    The figures are an *estimate* scaled from this run: the number of
    comment lines per block and the tokens per block measured here are
    applied to the issue's population (77 files, 2,643 comment lines).
    """
    lines_per_block = (comment_lines / blocks) if blocks else 0.0
    tokens_read = int(comment_chars / CHARS_PER_TOKEN)
    tokens_adjudicate = int(blocks * ADJUDICATION_TOKENS_PER_BLOCK)
    tokens_this_run = tokens_read + tokens_adjudicate
    tokens_per_block = (tokens_this_run / blocks) if blocks else 0.0
    projected_blocks = int(round(STAGING_STUDIO_COMMENT_LINES / lines_per_block)) \
        if lines_per_block else 0
    return {
        "target": "staging-studio src/components",
        "target_files": STAGING_STUDIO_FILES,
        "target_comment_lines": STAGING_STUDIO_COMMENT_LINES,
        "basis": "scaled from this Phase-0 run on plan-coder bin/*.py",
        "comment_lines_per_block_here": round(lines_per_block, 3),
        "tokens_per_block_here": round(tokens_per_block, 1),
        "projected_blocks": projected_blocks,
        "projected_tokens_read": int(projected_blocks * comment_chars
                                     / blocks / CHARS_PER_TOKEN) if blocks else 0,
        "projected_tokens_adjudication": int(projected_blocks
                                             * ADJUDICATION_TOKENS_PER_BLOCK),
        "projected_total_tokens": int(projected_blocks * tokens_per_block),
        "estimate": True,
    }


def build_ledger(repo_root: str = REPO_ROOT,
                 pattern: str = DEFAULT_GLOB) -> dict:
    """Build the full classification ledger for the census population."""
    blocks = census(repo_root, pattern)
    files = sorted({block.file for block in blocks})

    code_by_file = {}
    for path in census_files(repo_root, pattern):
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        label = os.path.relpath(path, repo_root)
        code_by_file[label] = [
            line for line in source.splitlines()
            if not line.strip().startswith("#")
        ]

    records = []
    for block in blocks:
        verdict = classify(block)
        category = verdict["category"]
        cluster = verdict["cluster"]
        if category == "Excuse":
            cluster = EXCUSE_CLUSTERS.get(block.key) or cluster_for(block)
        records.append({
            "file": block.file,
            "start_line": block.start_line,
            "end_line": block.end_line,
            "lines": block.end_line - block.start_line + 1,
            "category": category,
            "evidence": block.evidence,
            "note": verdict["note"],
            "cluster": cluster,
            "method": verdict["method"],
            "stale_hints": stale_hints(block, code_by_file.get(block.file, [])),
            "block_text": block.text,
        })

    counts: dict[str, int] = {category: 0 for category in CATEGORIES}
    for record in records:
        counts[record["category"]] += 1

    clusters: dict[str, list] = {}
    for record in records:
        if record["category"] == "Excuse":
            clusters.setdefault(record["cluster"], []).append(
                f"{record['file']}:{record['start_line']}")

    excuse_raw = counts["Excuse"]
    excuse_clusters = len(clusters)

    comment_chars = sum(len(block.text) for block in blocks)
    comment_lines = sum(len(block.lines) for block in blocks)
    projection = project_audit(len(records), comment_lines, comment_chars)
    ledger = {
        "tool": "bin/delete_comment_phase0.py",
        "issue": 142,
        "phase": 0,
        "mode": "measurement-only",
        "population": {
            "glob": pattern,
            "files_scanned": len(census_files(repo_root, pattern)),
            "files_with_blocks": len(files),
            "files": files,
        },
        "definition": {
            "block": "a maximal run of consecutive comment-only (#) lines",
            "excluded": [
                "docstrings (they are string literals, not comments)",
                "trailing/inline comments after code on the same line",
                "shebang line (#!)",
                "PEP 263 encoding declaration",
            ],
        },
        "counts": {
            "blocks": len(records),
            "by_category": counts,
        },
        "excuse_clusters": {
            "raw_excuse_blocks": excuse_raw,
            "cluster_count": excuse_clusters,
            "flood_ratio": (round(excuse_raw / excuse_clusters, 3)
                            if excuse_clusters else 0.0),
            "clusters": {theme: sorted(refs) for theme, refs in
                         sorted(clusters.items())},
        },
        "cost_estimate": {
            "comment_lines_classified": comment_lines,
            "comment_chars_classified": comment_chars,
            "assumed_chars_per_token": CHARS_PER_TOKEN,
            "estimated_tokens_to_read_census": int(comment_chars / CHARS_PER_TOKEN),
            "assumed_adjudication_tokens_per_block": ADJUDICATION_TOKENS_PER_BLOCK,
            "estimated_adjudication_tokens": int(len(records) * ADJUDICATION_TOKENS_PER_BLOCK),
            "estimated_total_tokens_this_run": int(
                comment_chars / CHARS_PER_TOKEN
                + len(records) * ADJUDICATION_TOKENS_PER_BLOCK),
        },
        "projection": projection,
        "ratchet_baseline": {
            "note": "Phase-0 census at the measured base; a ratchet may forbid "
                    "new Tautology/Excuse blocks from here on.  Kept blocks "
                    "are never deletion candidates.",
            "blocks": len(records),
            "by_category": counts,
            "deletion_candidates": counts["Tautology"],
        },
        "records": records,
    }
    return ledger


def summary_lines(ledger: dict) -> list:
    """Render the short human-readable summary of a ledger."""
    counts = ledger["counts"]
    lines = [
        f"files scanned        : {ledger['population']['files_scanned']}",
        f"files with blocks    : {ledger['population']['files_with_blocks']}",
        f"comment blocks       : {counts['blocks']}",
    ]
    for category in CATEGORIES:
        lines.append(f"  {category:<26}: {counts['by_category'][category]}")
    clusters = ledger["excuse_clusters"]
    lines.append(f"excuse raw blocks    : {clusters['raw_excuse_blocks']}")
    lines.append(f"excuse clusters      : {clusters['cluster_count']}")
    lines.append(f"flood ratio          : {clusters['flood_ratio']}")
    return lines


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo-root", default=REPO_ROOT)
    parser.add_argument("--glob", dest="pattern", default=DEFAULT_GLOB)
    parser.add_argument("--out", default=None,
                        help=f"write the JSON ledger to PATH "
                             f"(default: {DEFAULT_LEDGER})")
    parser.add_argument("--json", action="store_true",
                        help="print the full ledger as JSON on stdout")
    parser.add_argument("--summary", action="store_true",
                        help="print a short human-readable summary")
    args = parser.parse_args(argv)

    ledger = build_ledger(args.repo_root, args.pattern)

    out_path = args.out
    if out_path is None and not args.json:
        out_path = os.path.join(args.repo_root, DEFAULT_LEDGER)
    if out_path is not None:
        directory = os.path.dirname(os.path.abspath(out_path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(ledger, handle, indent=2, sort_keys=False)
            handle.write("\n")

    if args.json:
        json.dump(ledger, sys.stdout, indent=2, sort_keys=False)
        sys.stdout.write("\n")
    elif args.summary:
        print("\n".join(summary_lines(ledger)))
    else:
        print("\n".join(summary_lines(ledger)))
        if out_path:
            print(f"\nledger written to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
