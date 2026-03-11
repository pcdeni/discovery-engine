"""
Schema + quality validation for extraction results.

Used by:
- Contributors: `discovery validate result.json` (local check before submitting)
- GitHub Actions CI: validates every PR submission
- Quality monitoring: periodic batch checks on the full dataset

Returns a list of issues. Empty list = valid.
"""

import json
import re
from pathlib import Path
from typing import Any

# Bridge tag blocklist: domain-specific nouns and statistical terms that
# should NOT appear as bridge tags (they fail the "3 unrelated fields" test)
BRIDGE_TAG_BLOCKLIST = {
    # Domain nouns (too specific to one field)
    "graphene", "insulin", "perovskite", "crispr", "bitcoin", "dopamine",
    "serotonin", "collagen", "cellulose", "silicon", "lithium", "cobalt",
    "titanium", "zeolite", "fullerene", "nanotube", "polymer", "enzyme",
    "antibody", "ribosome", "chlorophyll", "hemoglobin", "keratin",
    "magnetite", "quartz", "diamond", "sapphire",
    # Statistical / methodological terms (not functional)
    "p-value", "regression", "anova", "chi-square", "t-test", "correlation",
    "standard deviation", "confidence interval", "sample size", "effect size",
    "meta-analysis", "systematic review", "randomized controlled trial",
    "cross-validation", "overfitting", "underfitting",
    # Field names (too broad)
    "biology", "chemistry", "physics", "engineering", "medicine",
    "computer science", "mathematics", "ecology", "geology",
    "neuroscience", "psychology", "economics",
    # Technique names (not functional descriptors)
    "machine learning", "deep learning", "neural network", "random forest",
    "gradient descent", "backpropagation", "reinforcement learning",
    "spectroscopy", "chromatography", "microscopy", "crystallography",
    "mass spectrometry", "pcr", "western blot", "elisa",
}

SCHEMA_PATH = Path(__file__).parent.parent / "schemas" / "extraction.schema.json"


def validate_result(data: dict, strict: bool = False) -> list[str]:
    """
    Validate a single extraction result.

    Args:
        data: The parsed JSON extraction result
        strict: If True, also check optional quality criteria

    Returns:
        List of issue strings. Empty = valid.
    """
    issues = []

    # ── Structural checks ──────────────────────────────────────────────

    # Top-level keys
    if "paper_analysis" not in data and "analysis" not in data:
        issues.append("MISSING: top-level 'paper_analysis' key")
    if "entities" not in data:
        issues.append("MISSING: top-level 'entities' key")
    if "cross_domain" not in data:
        issues.append("MISSING: top-level 'cross_domain' key")

    # If we can't find the basic structure, no point continuing
    if issues:
        return issues

    pa = data.get("paper_analysis", data.get("analysis", {}))
    cd = data.get("cross_domain", {})
    entities = data.get("entities", [])

    # ── Paper analysis checks ──────────────────────────────────────────

    for field in ["objective", "method", "key_findings", "limitations", "context"]:
        if field not in pa:
            issues.append(f"MISSING: paper_analysis.{field}")
        elif field in ("objective", "method", "context"):
            if not isinstance(pa[field], str) or len(pa[field]) < 20:
                issues.append(f"TOO_SHORT: paper_analysis.{field} (need 20+ chars)")
        elif field in ("key_findings", "limitations"):
            if not isinstance(pa[field], list) or len(pa[field]) < 1:
                issues.append(f"EMPTY: paper_analysis.{field} (need at least 1 item)")

    if "cross_domain_yield" not in pa:
        issues.append("MISSING: paper_analysis.cross_domain_yield")
    elif pa["cross_domain_yield"] not in ("HIGH", "MEDIUM", "LOW", "NONE"):
        issues.append(f"INVALID: cross_domain_yield must be HIGH/MEDIUM/LOW/NONE, got '{pa['cross_domain_yield']}'")

    # ── Entity checks ──────────────────────────────────────────────────

    if not isinstance(entities, list) or len(entities) < 1:
        issues.append("EMPTY: entities (need at least 1)")
    else:
        entity_ids = set()
        valid_types = {"material", "device", "process", "organism", "phenomenon",
                       "structure", "compound", "protein", "gene", "disease",
                       "method", "other"}
        for i, ent in enumerate(entities):
            if not isinstance(ent, dict):
                issues.append(f"FORMAT: entities[{i}] is {type(ent).__name__}, expected dict")
                continue
            eid = ent.get("id", "")
            if not re.match(r"^E\d+$", eid):
                issues.append(f"FORMAT: entities[{i}].id must match E[0-9]+ (got '{eid}')")
            entity_ids.add(eid)
            if not ent.get("name"):
                issues.append(f"MISSING: entities[{i}].name")
            if ent.get("type") not in valid_types:
                issues.append(f"INVALID: entities[{i}].type '{ent.get('type')}' not in valid types")
            if not isinstance(ent.get("domains"), list) or len(ent.get("domains", [])) < 1:
                issues.append(f"MISSING: entities[{i}].domains (need 1-3)")

    # ── Property checks (optional but validated if present) ────────────

    for i, prop in enumerate(data.get("properties", [])):
        if not isinstance(prop, dict):
            issues.append(f"FORMAT: properties[{i}] is {type(prop).__name__}, expected dict")
            continue
        if not re.match(r"^P\d+$", prop.get("id", "")):
            issues.append(f"FORMAT: properties[{i}].id must match P[0-9]+")
        if prop.get("entity_id") and prop["entity_id"] not in entity_ids:
            issues.append(f"FK_BROKEN: properties[{i}].entity_id '{prop['entity_id']}' not in entities")
        if not prop.get("value"):
            issues.append(f"MISSING: properties[{i}].value (every property needs a value)")

    # ── Relation checks (optional but validated if present) ────────────

    for i, rel in enumerate(data.get("relations", [])):
        if not isinstance(rel, dict):
            issues.append(f"FORMAT: relations[{i}] is {type(rel).__name__}, expected dict")
            continue
        if not re.match(r"^R\d+$", rel.get("id", "")):
            issues.append(f"FORMAT: relations[{i}].id must match R[0-9]+")
        if rel.get("source_entity") and rel["source_entity"] not in entity_ids:
            issues.append(f"FK_BROKEN: relations[{i}].source_entity '{rel['source_entity']}' not in entities")
        if rel.get("target_entity") and rel["target_entity"] not in entity_ids:
            issues.append(f"FK_BROKEN: relations[{i}].target_entity '{rel['target_entity']}' not in entities")
        rel_type = rel.get("type", "")
        if rel_type and not re.match(r"^[a-z][a-z0-9_]*$", rel_type):
            issues.append(f"FORMAT: relations[{i}].type '{rel_type}' must be snake_case")
        mechanism = rel.get("mechanism", "")
        if isinstance(mechanism, str) and len(mechanism) < 20:
            issues.append(f"TOO_SHORT: relations[{i}].mechanism (need 20+ chars, got {len(mechanism)})")

    # ── Cross-domain checks (Layer 2) ──────────────────────────────────

    if not isinstance(cd, dict):
        issues.append(f"FORMAT: cross_domain is {type(cd).__name__}, expected dict")
        return issues

    # Core friction
    cf = cd.get("core_friction", "")
    if not cf or (isinstance(cf, str) and len(cf) < 30):
        issues.append("TOO_SHORT: cross_domain.core_friction (need 30+ chars)")

    # Mechanism
    mech = cd.get("mechanism", {})
    if isinstance(mech, dict):
        if not mech.get("description") or len(mech.get("description", "")) < 30:
            issues.append("TOO_SHORT: cross_domain.mechanism.description (need 30+ chars)")
    elif isinstance(mech, str):
        issues.append("FORMAT: cross_domain.mechanism should be dict, not string")
    else:
        issues.append("MISSING: cross_domain.mechanism")

    # Unsolved tensions (NEVER empty)
    tensions = cd.get("unsolved_tensions", [])
    if not isinstance(tensions, list) or len(tensions) < 1:
        issues.append("EMPTY: cross_domain.unsolved_tensions (MUST have at least 1)")
    else:
        for i, t in enumerate(tensions):
            if isinstance(t, str):
                issues.append(f"FORMAT: unsolved_tensions[{i}] is string, expected dict with 'tension' and 'constraint_class'")
            elif isinstance(t, dict):
                if not t.get("tension") or len(t.get("tension", "")) < 15:
                    issues.append(f"TOO_SHORT: unsolved_tensions[{i}].tension")
                if not t.get("constraint_class"):
                    issues.append(f"MISSING: unsolved_tensions[{i}].constraint_class")

    # Bridge tags
    tags = cd.get("bridge_tags", [])
    if not isinstance(tags, list) or len(tags) < 1:
        issues.append("EMPTY: cross_domain.bridge_tags (need at least 1)")
    else:
        for tag in tags:
            if not isinstance(tag, str):
                issues.append(f"FORMAT: bridge_tag '{tag}' should be string")
                continue
            tag_lower = tag.lower().strip()
            if tag_lower in BRIDGE_TAG_BLOCKLIST:
                issues.append(f"BLOCKLISTED: bridge_tag '{tag}' is a domain noun or statistical term")

    # Interface (provides / requires) — the PRIMARY discovery mechanism
    iface = cd.get("interface", {})
    if not isinstance(iface, dict):
        issues.append(f"FORMAT: cross_domain.interface is {type(iface).__name__}, expected dict")
    else:
        for direction in ("provides", "requires"):
            ops = iface.get(direction, [])
            if not isinstance(ops, list) or len(ops) < 1:
                issues.append(f"EMPTY: cross_domain.interface.{direction} (need at least 1)")
            else:
                for i, op in enumerate(ops):
                    if isinstance(op, str):
                        issues.append(f"FORMAT: interface.{direction}[{i}] is string, expected dict with 'operation' and 'description'")
                    elif isinstance(op, dict):
                        operation = op.get("operation", "")
                        if not operation:
                            issues.append(f"MISSING: interface.{direction}[{i}].operation")
                        elif not re.match(r"^[a-z][a-z0-9_]*$", operation):
                            issues.append(f"FORMAT: interface.{direction}[{i}].operation '{operation}' must be snake_case")
                        desc = op.get("description", "")
                        if not desc or len(desc) < 15:
                            issues.append(f"TOO_SHORT: interface.{direction}[{i}].description (need 15+ chars)")
                    else:
                        issues.append(f"FORMAT: interface.{direction}[{i}] is {type(op).__name__}, expected dict")

    # ── Strict quality checks (optional, for periodic review) ──────────

    if strict:
        # Check entity name length (suspiciously short names)
        for ent in entities:
            if isinstance(ent, dict) and len(ent.get("name", "")) < 3:
                issues.append(f"QUALITY: entity '{ent.get('name')}' name too short (likely abbreviation without expansion)")

        # Check for at least some properties
        if len(data.get("properties", [])) < 1:
            issues.append("QUALITY: no properties extracted (most papers have at least one measurable fact)")

        # Check mechanism has conditions or failure modes
        if isinstance(mech, dict):
            if not mech.get("conditions") and not mech.get("failure_modes"):
                issues.append("QUALITY: mechanism has no conditions or failure_modes")

    return issues


def validate_file(filepath: str | Path, strict: bool = False) -> tuple[bool, list[str]]:
    """
    Validate a JSON file.

    Returns:
        (is_valid, issues) tuple
    """
    filepath = Path(filepath)
    if not filepath.exists():
        return False, [f"FILE_NOT_FOUND: {filepath}"]
    if not filepath.suffix == ".json":
        return False, [f"NOT_JSON: {filepath}"]

    try:
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return False, [f"PARSE_ERROR: {e}"]

    issues = validate_result(data, strict=strict)
    return len(issues) == 0, issues


def validate_batch(directory: str | Path, strict: bool = False) -> dict:
    """
    Validate all JSON files in a directory.

    Returns:
        {
            "total": int,
            "valid": int,
            "invalid": int,
            "files": {filename: [issues]}
        }
    """
    directory = Path(directory)
    results = {"total": 0, "valid": 0, "invalid": 0, "files": {}}

    for fp in sorted(directory.glob("*.json")):
        results["total"] += 1
        is_valid, issues = validate_file(fp, strict=strict)
        if is_valid:
            results["valid"] += 1
        else:
            results["invalid"] += 1
            results["files"][fp.name] = issues

    return results


# ── CLI interface ──────────────────────────────────────────────────────

def main():
    import sys

    if len(sys.argv) < 2:
        print("Usage: python validate.py <file_or_directory> [--strict]")
        print("  Validates extraction results against the schema.")
        print("  --strict: enable quality checks (not just schema)")
        sys.exit(1)

    target = Path(sys.argv[1])
    strict = "--strict" in sys.argv

    if target.is_file():
        is_valid, issues = validate_file(target, strict=strict)
        if is_valid:
            print(f"PASS: {target.name}: VALID")
        else:
            print(f"FAIL: {target.name}: {len(issues)} issue(s)")
            for issue in issues:
                print(f"  - {issue}")
        sys.exit(0 if is_valid else 1)

    elif target.is_dir():
        results = validate_batch(target, strict=strict)
        print(f"Validated {results['total']} files: {results['valid']} valid, {results['invalid']} invalid")
        if results["files"]:
            print()
            for fname, issues in results["files"].items():
                print(f"FAIL: {fname}: {len(issues)} issue(s)")
                for issue in issues:
                    print(f"  - {issue}")
        sys.exit(0 if results["invalid"] == 0 else 1)

    else:
        print(f"Error: {target} is not a file or directory")
        sys.exit(1)


if __name__ == "__main__":
    main()
