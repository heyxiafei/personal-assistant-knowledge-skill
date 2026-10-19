#!/usr/bin/env python3
"""Offline reverse coverage of Agent-selected source details and observed output.

No tool calls, extraction, account access, page writes or semantic truth checks.
"""
from __future__ import annotations
import argparse
import copy
import json
import re


class CoverageError(ValueError):
    pass


ROLES = {"identity", "experience", "resources", "goals", "projects", "results", "sources"}
STATES = {"read", "partial", "discovered_unread", "inaccessible", "stale"}
DISPOSITIONS = {"retained", "excluded", "quarantined", "unread"}


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise CoverageError(label + " must be nonempty text")
    return value


def review_finalization(data, section_rows):
    """Reconcile a supplied record inventory and displayed quotation contexts.

    This does not choose semantic units, infer speakers, or certify that the
    caller supplied every source/quotation. Full paragraphs are a mechanical
    context boundary, not a semantic truth guarantee.
    """
    supplied = data.get("source_records")
    quotations = data.get("quotations", [])
    if not isinstance(quotations, list):
        raise CoverageError("quotations must be a list")
    if supplied is None:
        if quotations:
            raise CoverageError("quotation checks require source records")
        return {"record_inventory_checked": False, "quotation_inventory_declared": "quotations" in data,
                "quotation_contexts_checked": 0,
                "issues": [], "limitation": "No record inventory supplied; source omissions unchecked"}
    if not isinstance(supplied, list) or not supplied:
        raise CoverageError("source records must be a nonempty list")
    records = {}
    for record in supplied:
        if not isinstance(record, dict):
            raise CoverageError("source record object required")
        rid = text(record.get("id"), "source record identifier")
        if rid in records:
            raise CoverageError("duplicate source record identifier")
        text(record.get("source_id"), "source identity")
        if record.get("disposition") not in DISPOSITIONS:
            raise CoverageError("source record disposition invalid")
        if not isinstance(record.get("text"), str):
            raise CoverageError("actual returned record text required")
        records[rid] = record
    mapped, issues = set(), []
    for section, row in zip(data["sections"], section_rows):
        ids = section.get("record_ids")
        if not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids) \
                or len(ids) != len(set(ids)):
            raise CoverageError("inventory review requires unique record IDs for each section")
        for rid in ids:
            if rid not in records or records[rid]["source_id"] != row["source_id"]:
                raise CoverageError("section record identity mismatch")
            if records[rid]["disposition"] == "retained" and row["status"] != "read":
                issues.append({"record_id": rid, "section_id": row["section_id"],
                               "kind": "retained_record_unresolved", "status": row["status"]})
        mapped.update(ids)
    issues.extend({"record_id": rid, "kind": "retained_record_unmapped"}
              for rid, record in records.items()
              if record["disposition"] == "retained" and rid not in mapped)
    seen_quotes = set()
    for quote in quotations:
        if not isinstance(quote, dict):
            raise CoverageError("quotation object required")
        qid = text(quote.get("id"), "quotation identifier")
        if qid in seen_quotes:
            raise CoverageError("duplicate quotation identifier")
        seen_quotes.add(qid)
        rid = quote.get("record_id")
        if not isinstance(rid, str) or rid not in records:
            raise CoverageError("quotation cites unknown record")
        excerpt = text(quote.get("text"), "quotation text")
        context = text(quote.get("context"), "displayed source context")
        page = text(quote.get("page_ref"), "quotation projection reference")
        source = records[rid]["text"]
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", source) if part.strip()]
        context_parts = [part.strip() for part in re.split(r"\n\s*\n", context) if part.strip()]
        continuous = context in source and any(
            paragraphs[i:i + len(context_parts)] == context_parts
            for i in range(len(paragraphs)))
        # The declared display itself must preserve the full context; merely
        # finding a good paragraph elsewhere on the page cannot redeem it.
        if excerpt != context or not continuous:
            issues.append({"quotation_id": qid, "record_id": rid,
                           "kind": "quotation_context_incomplete"})
        # Check the actual supplied readback, including every projection at this
        # target. A good primary must not conceal a bad copy of the same quote.
        targets = [p for p in data["projections"] if p["page_ref"] == page]
        if not targets or any(not p["readback"] or context not in
                              re.sub(r"(?m)^> ?", "", p["text"]) for p in targets):
            issues.append({"quotation_id": qid, "kind": "quotation_context_not_displayed",
                           "page_ref": page})
    return {"record_inventory_checked": True, "quotation_inventory_declared": "quotations" in data,
            "records": len(records),
            "retained_records": sum(r["disposition"] == "retained" for r in records.values()),
            "quotation_contexts_checked": len(quotations), "issues": issues,
            "limitation": "Only supplied inventory, annotations and actual readbacks; semantics and completeness need independent review"}


def review_content(data):
    """Check annotated details against supplied actual body text, not field counts.

    The Agent must select sections/details from raw sources before seeing output
    and provide actual readback bodies. A pass only means these annotations match;
    independent source-to-body judgment remains necessary.
    """
    if not isinstance(data, dict) or not isinstance(data.get("sections"), list) \
            or not data["sections"] or not isinstance(data.get("projections"), list):
        raise CoverageError("nonempty source sections and projection list required")
    rows, atoms, issues, coverage_limits = [], {}, [], []
    section_ids = set()
    for section in data["sections"]:
        if not isinstance(section, dict):
            raise CoverageError("section object required")
        section_id = text(section.get("id"), "section identifier")
        if section_id in section_ids:
            raise CoverageError("duplicate section identifier")
        section_ids.add(section_id)
        source_id = text(section.get("source_id"), "actual source identifier")
        status = section.get("status")
        if status not in STATES:
            raise CoverageError("source read status invalid")
        scope = text(section.get("scope"), "actual read scope")
        versions = [section.get(k) for k in ("read_version", "latest_version")]
        if (versions[0] is None) != (versions[1] is None):
            raise CoverageError("version comparison needs both observed versions")
        for v in versions:
            if v is not None and (isinstance(v, bool) or not isinstance(v, (int, str))
                                  or isinstance(v, str) and not v.strip()):
                raise CoverageError("invalid observed version")
        changed_version = all(v is not None for v in versions) and versions[0] != versions[1]
        if changed_version:
            status = "stale"
        row = {"section_id": section_id, "source_id": source_id, "status": status,
               "scope": scope, "atom_ids": []}
        if status != "read":
            reason = text(section.get("reason") or ("来源版本在本次核对时变化" if changed_version else None),
                          "unread or stale section reason")
            coverage_limits.append({"section_id": section_id, "status": status, "reason": reason})
        excluded = section.get("excluded_reason")
        values = section.get("atoms", [])
        if not isinstance(values, list):
            raise CoverageError("section atoms must be a list")
        if excluded is not None:
            row["excluded_reason"] = text(excluded, "exclusion reason")
            if values:
                raise CoverageError("excluded section cannot silently include facts")
        elif status == "read" and not values:
            issues.append({"section_id": section_id, "kind": "section_unmapped"})
        for atom in values:
            if not isinstance(atom, dict):
                raise CoverageError("atom object required")
            aid = text(atom.get("id"), "atom identifier")
            if aid in atoms:
                raise CoverageError("duplicate atom identifier")
            role = atom.get("role")
            if role not in ROLES:
                raise CoverageError("atom must map to an existing category")
            terms = atom.get("required_terms")
            if not isinstance(terms, list) or not terms \
                    or any(not isinstance(t, str) or not t.strip() for t in terms):
                raise CoverageError("selected entity/action/date/status/boundary terms required")
            atoms[aid] = {"section_id": section_id, "source_id": source_id, "role": role,
                          "required_terms": list(dict.fromkeys(terms)), "source_status": status}
            row["atom_ids"].append(aid)
        rows.append(row)
    targets = {}
    for projection in data["projections"]:
        if not isinstance(projection, dict) or projection.get("role") not in ROLES:
            raise CoverageError("projection role invalid")
        if projection.get("readback") not in (True, False) or not isinstance(projection.get("readback"), bool):
            raise CoverageError("actual readback status required")
        text(projection.get("page_ref"), "actual page or local target reference")
        body = text(projection.get("text"), "observed projection body")
        ids, source_ids = projection.get("atom_ids"), projection.get("source_ids")
        if not isinstance(ids, list) or any(not isinstance(i, str) or not i.strip() for i in ids) \
                or not isinstance(source_ids, list) \
                or any(not isinstance(s, str) or not s.strip() for s in source_ids):
            raise CoverageError("projection atom and source identifiers required")
        if len(ids) != len(set(ids)):
            raise CoverageError("duplicate atom in one projection")
        kind = projection.get("kind", "body")
        if kind not in {"body", "reference"}:
            raise CoverageError("projection kind must be body or reference")
        references = projection.get("references", {})
        if kind == "reference" and (not isinstance(references, dict) or set(references) != set(ids)):
            raise CoverageError("reference projection needs one explicit link per atom")
        for aid in ids:
            if not isinstance(aid, str) or aid not in atoms:
                raise CoverageError("projection cites unknown source atom")
            atom = atoms[aid]
            record = {"page_ref": projection["page_ref"], "readback": projection["readback"], "kind": kind,
                      "role_match": projection["role"] == atom["role"],
                      "source_match": atom["source_id"] in source_ids,
                      "missing_terms": [t for t in atom["required_terms"] if t not in body]}
            if kind == "reference":
                ref = references[aid]
                if not isinstance(ref, dict):
                    raise CoverageError("reference object required")
                destination = text(ref.get("target_ref"), "observed canonical link destination")
                literal = text(ref.get("literal"), "observed literal Markdown link")
                if not re.fullmatch(r"\[[^\]\n]+\]\((?:<" + re.escape(destination) + r">|"
                                    + re.escape(destination) + r")\)", literal):
                    raise CoverageError("reference literal must link to the stated canonical target")
                record.update(reference_target=destination, missing_terms=[] if literal in body else [literal])
            targets.setdefault(aid, []).append(record)
    def matches(record):
        return record["readback"] and record["role_match"] and record["source_match"] and not record["missing_terms"]

    # A link-only projection must resolve to an observed body for this same atom.
    # Do not follow chains or invent target content from the reference annotation.
    for found in targets.values():
        for record in found:
            if record["kind"] == "reference":
                canonical = any(r["kind"] == "body" and r["page_ref"] == record["reference_target"]
                                and matches(r) for r in found)
                record.update(role_match=canonical, source_match=canonical, target_verified=canonical)
    for aid, atom in atoms.items():
        found = targets.get(aid, [])
        if not found:
            issues.append({"atom_id": aid, "section_id": atom["section_id"],
                           "kind": "atom_unmapped", "targets": []})
        for record in found:
            if not matches(record):
                issues.append({"atom_id": aid, "section_id": atom["section_id"],
                               "kind": "reference_not_verified" if record["kind"] == "reference" else "detail_not_retained",
                               "targets": [record]})
        if atom["source_status"] != "read" and found:
            issues.append({"atom_id": aid, "section_id": atom["section_id"],
                           "kind": "unread_or_stale_source_projected"})
    finalization = review_finalization(data, rows)
    issues.extend(finalization["issues"])
    return {"status": "annotations_matched" if not issues else "gaps_found",
            "sections": rows, "atoms": copy.deepcopy(atoms), "issues": issues,
            "finalization": finalization,
            "coverage_limits": coverage_limits,
            "read_scope_complete": not coverage_limits,
            "limitation": "仅核所列源章节及短语与提供的实际正文；不证明源清单完整、语义正确、权限、用户认可或账户全量"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="private source annotations and actual readback bodies")
    parser.add_argument("--require-finalization", action="store_true",
                        help="exit nonzero on gaps or absent record/quotation inventories; not a semantic acceptance verdict")
    args = parser.parse_args()
    with open(args.input, encoding="utf-8") as handle:
        data = json.load(handle)
    result = review_content(data)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.require_finalization and (result["issues"]
            or not result["finalization"]["record_inventory_checked"]
            or not result["finalization"]["quotation_inventory_declared"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
