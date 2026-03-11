"""
Automated quality gate — replaces trust levels entirely.

Every submission goes through the same checks, every time. No shortcuts
for "trusted" contributors. Three lines of defense:

1. GROUNDING CHECK: Verify extracted entities actually appear in the paper text.
   If someone submits fabricated data, entity names won't match the paper.

2. HONEYPOT CHECK: Known-answer papers with reference extractions. If a
   contributor's output diverges significantly, the entire submission is flagged.

3. STATISTICAL CHECKS: Detect anomalous outputs — too few entities, suspiciously
   uniform structure, copy-paste patterns across papers, etc.

4. CROSS-SUBMISSION DEDUP: If a paper was already extracted by someone else,
   compare the two. High divergence = one of them is wrong.

Usage:
    from discovery.quality_gate import run_quality_gate
    verdict = run_quality_gate(result, paper_text=text)
    # verdict: {"pass": bool, "flags": [...], "score": float}
"""

import json
import re
import hashlib
from pathlib import Path
from typing import Optional
from collections import Counter


# Honeypot reference directory
HONEYPOT_DIR = Path(__file__).parent.parent / "honeypots"


def run_quality_gate(
    result: dict,
    paper_text: Optional[str] = None,
    all_results: Optional[list[dict]] = None,
) -> dict:
    """
    Run all automated quality checks on a single extraction result.

    Args:
        result: The parsed extraction JSON
        paper_text: Original paper text (for grounding check). If not available,
                    grounding check is skipped.
        all_results: Other results in the same submission batch (for cross-check).

    Returns:
        {
            "pass": bool,       # True = accepted, False = rejected
            "score": float,     # 0.0-1.0 quality score
            "flags": [...],     # List of flag strings (warnings, not necessarily failures)
            "blocks": [...],    # List of blocking issues (cause rejection)
        }
    """
    flags = []
    blocks = []

    paper_id = result.get("_meta", {}).get("paper_id", "unknown")

    # 1. Grounding check
    if paper_text:
        grounding = _check_grounding(result, paper_text)
        flags.extend(grounding["flags"])
        blocks.extend(grounding["blocks"])

    # 2. Honeypot check
    honeypot = _check_honeypot(result)
    flags.extend(honeypot["flags"])
    blocks.extend(honeypot["blocks"])

    # 3. Statistical anomaly checks
    stats = _check_statistical(result)
    flags.extend(stats["flags"])
    blocks.extend(stats["blocks"])

    # 4. Copy-paste / template detection
    template = _check_template_abuse(result)
    flags.extend(template["flags"])
    blocks.extend(template["blocks"])

    # 5. Cross-submission dedup (if batch provided)
    if all_results:
        dedup = _check_cross_submission(result, all_results)
        flags.extend(dedup["flags"])
        blocks.extend(dedup["blocks"])

    # Calculate score (1.0 = perfect, each flag = -0.1, each block = instant fail)
    score = max(0.0, 1.0 - len(flags) * 0.1)
    passed = len(blocks) == 0 and score >= 0.3

    return {
        "pass": passed,
        "score": round(score, 2),
        "flags": flags,
        "blocks": blocks,
        "paper_id": paper_id,
    }


def run_quality_gate_batch(results: list[dict], paper_texts: Optional[dict] = None) -> dict:
    """
    Run quality gate on a batch of results.

    Args:
        results: List of extraction results
        paper_texts: Optional dict {paper_id: text} for grounding checks

    Returns:
        {
            "total": int, "passed": int, "failed": int,
            "verdicts": [{"paper_id": str, "pass": bool, ...}, ...]
        }
    """
    paper_texts = paper_texts or {}
    verdicts = []

    for result in results:
        pid = result.get("_meta", {}).get("paper_id", "unknown")
        text = paper_texts.get(pid)
        verdict = run_quality_gate(result, paper_text=text, all_results=results)
        verdicts.append(verdict)

    passed = sum(1 for v in verdicts if v["pass"])
    return {
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "verdicts": verdicts,
    }


# ── Check 1: Grounding ──────────────────────────────────────────────


def _check_grounding(result: dict, paper_text: str) -> dict:
    """
    Verify that extracted entities actually appear in the paper text.

    A fabricated extraction will have entity names that don't match anything
    in the source paper. We check what fraction of entities can be found.
    """
    flags = []
    blocks = []

    text_lower = paper_text.lower()
    entities = result.get("entities", [])

    if not entities:
        return {"flags": [], "blocks": []}

    found = 0
    total = len(entities)

    for ent in entities:
        if not isinstance(ent, dict):
            continue
        name = ent.get("name", "").lower().strip()
        if not name:
            continue

        # Check if entity name (or significant part of it) appears in paper
        if name in text_lower:
            found += 1
        elif len(name) > 5:
            # Try matching significant words (skip 1-2 char words)
            words = [w for w in name.split() if len(w) > 2]
            word_hits = sum(1 for w in words if w in text_lower)
            if words and word_hits / len(words) >= 0.5:
                found += 1

    grounding_ratio = found / total if total > 0 else 0

    if grounding_ratio < 0.3:
        blocks.append(
            f"GROUNDING_FAIL: Only {found}/{total} entities found in paper text "
            f"({grounding_ratio:.0%}). Likely fabricated."
        )
    elif grounding_ratio < 0.5:
        flags.append(
            f"GROUNDING_LOW: {found}/{total} entities found ({grounding_ratio:.0%}). "
            f"Possibly hallucinated entities."
        )

    return {"flags": flags, "blocks": blocks}


# ── Check 2: Honeypot ───────────────────────────────────────────────


def _check_honeypot(result: dict) -> dict:
    """
    Compare against known-answer reference extractions.

    If this paper has a honeypot reference, check that the contributor's
    extraction broadly agrees. Major divergence = likely gaming the system.
    """
    flags = []
    blocks = []

    paper_id = result.get("_meta", {}).get("paper_id", "")
    if not paper_id:
        return {"flags": [], "blocks": []}

    # Load honeypot reference if it exists
    safe_name = paper_id.replace(":", "__").replace("/", "_") + ".json"
    honeypot_file = HONEYPOT_DIR / safe_name
    if not honeypot_file.exists():
        return {"flags": [], "blocks": []}

    try:
        reference = json.loads(honeypot_file.read_text(encoding="utf-8"))
    except Exception:
        return {"flags": [], "blocks": []}

    # Compare bridge tags (most gameable part)
    ref_tags = set(t.lower() for t in reference.get("cross_domain", {}).get("bridge_tags", []))
    sub_tags = set(t.lower() for t in result.get("cross_domain", {}).get("bridge_tags", []))

    if ref_tags and sub_tags:
        overlap = len(ref_tags & sub_tags)
        total = len(ref_tags | sub_tags)
        jaccard = overlap / total if total > 0 else 0

        if jaccard < 0.1:
            blocks.append(
                f"HONEYPOT_FAIL: Bridge tags have <10% overlap with reference "
                f"(Jaccard={jaccard:.2f}). Paper {paper_id} is a known-answer test."
            )
        elif jaccard < 0.2:
            flags.append(
                f"HONEYPOT_LOW: Bridge tag overlap with reference is low "
                f"(Jaccard={jaccard:.2f}) for honeypot paper {paper_id}."
            )

    # Compare entity count (should be in similar ballpark)
    ref_ent_count = len(reference.get("entities", []))
    sub_ent_count = len(result.get("entities", []))
    if ref_ent_count > 0 and sub_ent_count > 0:
        ratio = sub_ent_count / ref_ent_count
        if ratio < 0.2 or ratio > 5.0:
            flags.append(
                f"HONEYPOT_ENTITY_COUNT: {sub_ent_count} entities vs {ref_ent_count} "
                f"in reference (ratio={ratio:.1f}x)"
            )

    return {"flags": flags, "blocks": blocks}


# ── Check 3: Statistical anomalies ──────────────────────────────────


def _check_statistical(result: dict) -> dict:
    """
    Detect statistically anomalous outputs that suggest gaming or low quality.
    """
    flags = []
    blocks = []

    entities = result.get("entities", [])
    properties = result.get("properties", [])
    relations = result.get("relations", [])
    cd = result.get("cross_domain", {})
    tensions = cd.get("unsolved_tensions", [])
    bridge_tags = cd.get("bridge_tags", [])
    iface = cd.get("interface", {})
    provides = iface.get("provides", [])
    requires = iface.get("requires", [])

    # Suspiciously low content (minimum viable garbage)
    if len(entities) == 1 and len(properties) == 0 and len(relations) == 0:
        flags.append("MINIMAL: Only 1 entity, 0 properties, 0 relations — suspiciously minimal")

    # Suspiciously high content (LLM hallucination dump)
    if len(entities) > 30:
        flags.append(f"EXCESSIVE: {len(entities)} entities — possible hallucination dump")
    if len(bridge_tags) > 15:
        flags.append(f"EXCESSIVE: {len(bridge_tags)} bridge tags — quality likely poor")

    # All tensions have same constraint_class (copy-paste)
    if len(tensions) >= 3:
        classes = [t.get("constraint_class", "") for t in tensions if isinstance(t, dict)]
        if len(set(classes)) == 1 and classes[0]:
            flags.append(
                f"UNIFORM: All {len(tensions)} tensions have same constraint_class "
                f"'{classes[0]}' — likely copy-paste"
            )

    # All provides/requires have nearly identical descriptions
    for direction in ("provides", "requires"):
        ops = iface.get(direction, [])
        if len(ops) >= 2:
            descs = [o.get("description", "") for o in ops if isinstance(o, dict)]
            if descs:
                # Check if descriptions are suspiciously similar
                avg_len = sum(len(d) for d in descs) / len(descs)
                if avg_len < 20:
                    flags.append(
                        f"SHALLOW: {direction} descriptions average {avg_len:.0f} chars — too short"
                    )

    # Bridge tags are all single words (often low quality)
    if bridge_tags:
        single_word = sum(1 for t in bridge_tags if isinstance(t, str) and " " not in t.strip())
        if single_word == len(bridge_tags) and len(bridge_tags) >= 3:
            flags.append("SHALLOW: All bridge tags are single words — likely surface-level")

    # Check for suspiciously round numbers in properties
    round_count = 0
    for prop in properties:
        if isinstance(prop, dict):
            val = str(prop.get("value", ""))
            if re.match(r"^\d+\.0+$", val) or re.match(r"^\d{2,}$", val):
                round_count += 1
    if round_count >= 3:
        flags.append(f"SUSPICIOUS: {round_count} properties have suspiciously round values")

    return {"flags": flags, "blocks": blocks}


# ── Check 4: Template abuse ─────────────────────────────────────────


def _check_template_abuse(result: dict) -> dict:
    """
    Detect copy-paste or template-based submissions.

    If someone uses a template to mass-generate fake extractions, they'll
    have repeating patterns. We hash key fields and check for suspicion.
    """
    flags = []
    blocks = []

    cd = result.get("cross_domain", {})

    # Check if core_friction is suspiciously generic
    friction = cd.get("core_friction", "")
    if isinstance(friction, str):
        generic_patterns = [
            r"^the (?:main|key|primary|central) (?:challenge|problem|issue) is",
            r"^this paper (?:addresses|tackles|investigates|explores)",
            r"^the (?:fundamental|core) (?:limitation|constraint|barrier)",
        ]
        for pat in generic_patterns:
            if re.match(pat, friction.lower()):
                flags.append("GENERIC: core_friction matches a generic template pattern")
                break

    # Check mechanism description for boilerplate
    mech = cd.get("mechanism", {})
    if isinstance(mech, dict):
        desc = mech.get("description", "")
        if isinstance(desc, str) and len(desc) > 0:
            # Very short mechanism + long paper = probably template
            if len(desc) < 50:
                flags.append(f"SHALLOW: mechanism.description only {len(desc)} chars")

    # Hash the structure to detect identical patterns across papers
    # (useful in batch mode)
    structure_hash = _structure_fingerprint(result)
    result.setdefault("_meta", {})["_structure_hash"] = structure_hash

    return {"flags": flags, "blocks": blocks}


# ── Check 5: Cross-submission ────────────────────────────────────────


def _check_cross_submission(result: dict, all_results: list[dict]) -> dict:
    """
    Compare this result against others in the same batch for:
    - Identical structure (copy-paste different paper_ids onto same template)
    - Duplicate paper_ids
    """
    flags = []
    blocks = []

    my_hash = _structure_fingerprint(result)
    my_pid = result.get("_meta", {}).get("paper_id", "")

    duplicate_hashes = 0
    duplicate_pids = 0

    for other in all_results:
        other_pid = other.get("_meta", {}).get("paper_id", "")
        if other_pid == my_pid:
            duplicate_pids += 1
            continue  # Skip self

        other_hash = _structure_fingerprint(other)
        if other_hash == my_hash:
            duplicate_hashes += 1

    if duplicate_pids > 1:
        blocks.append(f"DUPLICATE: paper_id '{my_pid}' appears {duplicate_pids} times in batch")

    if duplicate_hashes >= 2:
        blocks.append(
            f"TEMPLATE: {duplicate_hashes + 1} results have identical structure. "
            f"Likely template-based fabrication."
        )
    elif duplicate_hashes == 1:
        flags.append("SIMILAR: Another result in batch has identical structure — check manually")

    return {"flags": flags, "blocks": blocks}


# ── Utilities ────────────────────────────────────────────────────────


def _structure_fingerprint(result: dict) -> str:
    """
    Create a structural fingerprint of the result (ignoring paper-specific content).

    Two results with the same fingerprint have identical structure — likely
    generated from a template rather than actual paper extraction.
    """
    # Hash the structure: number of entities, properties, relations,
    # entity types, relation types, number of tensions, provides, requires
    parts = []

    entities = result.get("entities", [])
    parts.append(f"e={len(entities)}")
    parts.append(f"et={sorted(set(e.get('type', '') for e in entities if isinstance(e, dict)))}")

    parts.append(f"p={len(result.get('properties', []))}")
    parts.append(f"r={len(result.get('relations', []))}")

    cd = result.get("cross_domain", {})
    parts.append(f"t={len(cd.get('unsolved_tensions', []))}")
    parts.append(f"bt={len(cd.get('bridge_tags', []))}")

    iface = cd.get("interface", {})
    parts.append(f"prov={len(iface.get('provides', []))}")
    parts.append(f"req={len(iface.get('requires', []))}")

    # Hash the relation types
    rels = result.get("relations", [])
    rel_types = sorted(r.get("type", "") for r in rels if isinstance(r, dict))
    parts.append(f"rt={rel_types}")

    # Hash tension constraint classes
    tensions = cd.get("unsolved_tensions", [])
    classes = sorted(t.get("constraint_class", "") for t in tensions if isinstance(t, dict))
    parts.append(f"cc={classes}")

    fingerprint = "|".join(parts)
    return hashlib.md5(fingerprint.encode()).hexdigest()[:12]
