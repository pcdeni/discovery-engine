"""
Auto-normalize extraction results to fix common LLM output issues.

The LLM doesn't always follow the schema perfectly. This module fixes
known deviations without changing semantic content:

- 'analysis' → 'paper_analysis' (key rename)
- String tensions → structured dict with tension + constraint_class
- String provides/requires → structured dict with operation + description
- Human-readable operation names → snake_case
- String interface → dict with provides/requires lists

Run this BEFORE validation. The validate module expects normalized data.
"""

import re
import json
from pathlib import Path
from typing import Any


def to_snake_case(text: str) -> str:
    """Convert human-readable text to snake_case operation name."""
    # Already valid snake_case? Return as-is
    if re.match(r"^[a-z][a-z0-9_]*$", text):
        return text[:80]
    # Remove special characters, keep alphanumeric, spaces, and underscores
    cleaned = re.sub(r"[^a-zA-Z0-9\s_]", "", text)
    # Split on spaces, camelCase boundaries, or underscores
    words = re.split(r"[\s_]+", cleaned.strip())
    # Join with underscores, lowercase
    result = "_".join(w.lower() for w in words if w)
    # Truncate to reasonable length (max 80 chars)
    if len(result) > 80:
        result = result[:80].rsplit("_", 1)[0]
    return result


def normalize_result(data: dict) -> dict:
    """
    Auto-fix common LLM output issues.

    Returns a new dict (does not modify input).
    """
    data = json.loads(json.dumps(data))  # deep copy

    # ── Fix 1: 'analysis' → 'paper_analysis' ──────────────────────────
    if "analysis" in data and "paper_analysis" not in data:
        data["paper_analysis"] = data.pop("analysis")

    # ── Fix 2: Ensure cross_domain exists ──────────────────────────────
    # Some older prompts put these fields inside paper_analysis
    pa = data.get("paper_analysis", {})
    if "cross_domain" not in data:
        # Try to reconstruct from paper_analysis
        cd_fields = ["core_friction", "mechanism", "unsolved_tensions",
                     "bridge_tags", "interface", "mechanism_components"]
        if any(f in pa for f in cd_fields):
            data["cross_domain"] = {}
            for f in cd_fields:
                if f in pa:
                    data["cross_domain"][f] = pa.pop(f)

    cd = data.get("cross_domain", {})

    # ── Fix 3: String tensions → structured dicts ──────────────────────
    tensions = cd.get("unsolved_tensions", [])
    if isinstance(tensions, list):
        fixed_tensions = []
        for t in tensions:
            if isinstance(t, str):
                # Convert string to dict
                fixed_tensions.append({
                    "tension": t,
                    "constraint_class": _infer_constraint_class(t),
                    "severity": "practical"
                })
            elif isinstance(t, dict):
                # Ensure required fields
                if "tension" not in t and "description" in t:
                    t["tension"] = t.pop("description")
                if "constraint_class" not in t:
                    t["constraint_class"] = _infer_constraint_class(t.get("tension", ""))
                fixed_tensions.append(t)
            else:
                fixed_tensions.append(t)
        cd["unsolved_tensions"] = fixed_tensions

    # ── Fix 4: String interface → structured dict ──────────────────────
    iface = cd.get("interface", {})
    if isinstance(iface, str):
        # Can't recover structured data from a string, create empty
        cd["interface"] = {"provides": [], "requires": []}
        iface = cd["interface"]

    if isinstance(iface, dict):
        for direction in ("provides", "requires"):
            ops = iface.get(direction, [])
            if isinstance(ops, list):
                fixed_ops = []
                for op in ops:
                    if isinstance(op, str):
                        # Convert string to structured dict
                        fixed_ops.append({
                            "operation": to_snake_case(op),
                            "description": op
                        })
                    elif isinstance(op, dict):
                        # Fix operation name to snake_case
                        operation = op.get("operation", "")
                        if operation and not re.match(r"^[a-z][a-z0-9_]*$", operation):
                            op["operation"] = to_snake_case(operation)
                        # If operation is missing but description exists, derive it
                        if not op.get("operation") and op.get("description"):
                            op["operation"] = to_snake_case(op["description"][:60])
                        fixed_ops.append(op)
                    else:
                        fixed_ops.append(op)
                iface[direction] = fixed_ops

    # ── Fix 5: Normalize relation types to snake_case ──────────────────
    for rel in data.get("relations", []):
        if isinstance(rel, dict) and rel.get("type"):
            rel_type = rel["type"]
            if not re.match(r"^[a-z][a-z0-9_]*$", rel_type):
                rel["type"] = to_snake_case(rel_type)

    # ── Fix 6: Mechanism string → dict ─────────────────────────────────
    mech = cd.get("mechanism")
    if isinstance(mech, str):
        cd["mechanism"] = {
            "description": mech,
            "structural_pattern": ""
        }

    # ── Fix 7: Ensure _meta exists ─────────────────────────────────────
    if "_meta" not in data:
        data["_meta"] = {}

    data["cross_domain"] = cd
    return data


def _infer_constraint_class(tension_text: str) -> str:
    """
    Infer a constraint_class from tension text.
    Simple heuristic: look for common tradeoff patterns.
    """
    text = tension_text.lower()

    patterns = [
        (r"speed.*accuracy|accuracy.*speed|fast.*precise", "speed_vs_accuracy"),
        (r"throughput.*selectiv|selectiv.*throughput", "throughput_vs_selectivity"),
        (r"cost.*quality|quality.*cost|expensive", "cost_vs_quality"),
        (r"scalab|scale", "scalability"),
        (r"generali[sz]|specific.*general|general.*specific", "generality_vs_specificity"),
        (r"sensitiv.*specific|specific.*sensitiv", "sensitivity_vs_specificity"),
        (r"complex.*simpl|simpl.*complex", "complexity_vs_simplicity"),
        (r"resolut|precision.*recall|recall.*precision", "resolution_tradeoff"),
        (r"energy.*efficien|power.*consum", "energy_efficiency"),
        (r"stabil.*reactiv|reactiv.*stabil", "stability_vs_reactivity"),
        (r"robustness|robust.*fragil", "robustness"),
        (r"reproduc|replicab", "reproducibility"),
    ]

    for pattern, cls in patterns:
        if re.search(pattern, text):
            return cls

    return "general_tradeoff"


def normalize_file(filepath: str | Path) -> dict:
    """Read, normalize, and return a JSON file."""
    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)
    return normalize_result(data)


def normalize_file_inplace(filepath: str | Path) -> list[str]:
    """
    Normalize a file in place. Returns list of changes made.
    """
    filepath = Path(filepath)
    with open(filepath, encoding="utf-8") as f:
        original = json.load(f)

    normalized = normalize_result(original)

    # Detect what changed
    changes = _diff_changes(original, normalized)

    if changes:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(normalized, f, indent=2, ensure_ascii=False)

    return changes


def _diff_changes(original: dict, normalized: dict) -> list[str]:
    """List human-readable changes between original and normalized."""
    changes = []

    if "analysis" in original and "paper_analysis" not in original:
        changes.append("RENAMED: 'analysis' -> 'paper_analysis'")

    if "cross_domain" not in original and "cross_domain" in normalized:
        changes.append("RESTRUCTURED: moved cross_domain fields from paper_analysis")

    cd_orig = original.get("cross_domain", {})
    cd_norm = normalized.get("cross_domain", {})

    # Check tensions
    orig_tensions = cd_orig.get("unsolved_tensions", [])
    if orig_tensions and isinstance(orig_tensions[0], str):
        changes.append(f"CONVERTED: {len(orig_tensions)} string tensions -> structured dicts")

    # Check interface operations
    for direction in ("provides", "requires"):
        orig_ops = cd_orig.get("interface", {}).get(direction, [])
        norm_ops = cd_norm.get("interface", {}).get(direction, [])
        for i, (orig_op, norm_op) in enumerate(zip(orig_ops, norm_ops)):
            if isinstance(orig_op, dict) and isinstance(norm_op, dict):
                if orig_op.get("operation") != norm_op.get("operation"):
                    changes.append(f"SNAKE_CASE: interface.{direction}[{i}].operation: '{orig_op.get('operation')}' -> '{norm_op.get('operation')}'")
            elif isinstance(orig_op, str):
                changes.append(f"CONVERTED: interface.{direction}[{i}] string -> dict")

    return changes


def main():
    import sys

    if len(sys.argv) < 2:
        print("Usage: python normalize.py <file_or_directory> [--inplace]")
        print("  Normalizes extraction results to fix common LLM output issues.")
        print("  --inplace: modify files in place (default: print normalized to stdout)")
        sys.exit(1)

    target = Path(sys.argv[1])
    inplace = "--inplace" in sys.argv

    if target.is_file():
        if inplace:
            changes = normalize_file_inplace(target)
            if changes:
                print(f"Normalized {target.name}: {len(changes)} change(s)")
                for c in changes:
                    print(f"  {c}")
            else:
                print(f"No changes needed: {target.name}")
        else:
            normalized = normalize_file(target)
            print(json.dumps(normalized, indent=2, ensure_ascii=False))

    elif target.is_dir():
        total_changes = 0
        for fp in sorted(target.glob("*.json")):
            if inplace:
                changes = normalize_file_inplace(fp)
                if changes:
                    total_changes += len(changes)
                    print(f"  {fp.name}: {len(changes)} change(s)")
            else:
                print(f"--- {fp.name} ---")
                normalized = normalize_file(fp)
                print(json.dumps(normalized, indent=2, ensure_ascii=False)[:500])
                print("...\n")
        if inplace:
            print(f"Total: {total_changes} changes across {len(list(target.glob('*.json')))} files")
    else:
        print(f"Error: {target} not found")
        sys.exit(1)


if __name__ == "__main__":
    main()
