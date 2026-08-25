#!/usr/bin/env python3
import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
SEVERITIES = ["critical", "high", "medium", "low"]
CATEGORIES = ["bug", "data", "content", "image", "ui", "ux", "consistency", "cleanup"]


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def norm_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")


def coerce_severity(value: str) -> str:
    token = (value or "").strip().lower()
    return token if token in SEVERITY_ORDER else "medium"


def coerce_category(value: str) -> str:
    token = (value or "").strip().lower()
    return token if token in CATEGORIES else "cleanup"


def coerce_ready(value: str) -> str:
    return "yes" if str(value).strip().lower() == "yes" else "no"


def finding_key(item: dict) -> str:
    area = norm_token(item.get("area", ""))
    title = norm_token(item.get("title", ""))
    what = norm_token(item.get("what_is_wrong", ""))[:60]
    return f"{area}|{title}|{what}"


def load_jsonl(path: Path) -> list[dict]:
    findings = []
    if not path.exists():
        return findings
    for idx, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        raw = line.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            findings.append(
                {
                    "severity": "low",
                    "category": "cleanup",
                    "area": path.stem,
                    "title": f"Línea JSON inválida en {path.name}:{idx}",
                    "what_is_wrong": raw,
                    "evidence": f"{path}:{idx}",
                    "suggested_fix_direction": "Corregir formato JSONL antes de consolidar.",
                    "ready_for_prompt": "no",
                }
            )
            continue

        findings.append(
            {
                "severity": coerce_severity(obj.get("severity", "medium")),
                "category": coerce_category(obj.get("category", "cleanup")),
                "area": normalize_text(obj.get("area", "sin-area")) or "sin-area",
                "title": normalize_text(obj.get("title", "Hallazgo sin título")) or "Hallazgo sin título",
                "what_is_wrong": normalize_text(obj.get("what_is_wrong", "")) or "Sin detalle",
                "evidence": normalize_text(obj.get("evidence", "")) or "Sin evidencia",
                "suggested_fix_direction": normalize_text(obj.get("suggested_fix_direction", "")) or "Definir dirección de fix",
                "ready_for_prompt": coerce_ready(obj.get("ready_for_prompt", "no")),
            }
        )
    return findings


def dedupe_findings(items: list[dict]) -> list[dict]:
    by_key = {}
    for item in items:
        key = finding_key(item)
        if key not in by_key:
            by_key[key] = item
            continue

        current = by_key[key]
        if SEVERITY_ORDER[item["severity"]] < SEVERITY_ORDER[current["severity"]]:
            by_key[key] = item
            continue

        if current["ready_for_prompt"] == "no" and item["ready_for_prompt"] == "yes":
            by_key[key] = item

    return sorted(by_key.values(), key=lambda x: (SEVERITY_ORDER[x["severity"]], x["area"].lower(), x["title"].lower()))


def top_items(findings: list[dict], limit: int = 5) -> list[dict]:
    return findings[:limit]


def suspicious(findings: list[dict]) -> list[dict]:
    markers = [
        "duplic",
        "inconsisten",
        "placeholder",
        "cover",
        "absurd",
        "contradic",
        "naming",
    ]
    out = []
    for item in findings:
        blob = f"{item['title']} {item['what_is_wrong']} {item['category']}".lower()
        if any(token in blob for token in markers):
            out.append(item)
    return out


def backlog(findings: list[dict]) -> dict[str, list[dict]]:
    data = {"Arreglar ya": [], "Arreglar pronto": [], "Limpieza / mejora": []}
    for item in findings:
        sev = item["severity"]
        if sev in ("critical", "high"):
            data["Arreglar ya"].append(item)
        elif sev == "medium":
            data["Arreglar pronto"].append(item)
        else:
            data["Limpieza / mejora"].append(item)
    return data


def render_report(findings: list[dict]) -> str:
    total = len(findings)
    sev_counts = Counter(f["severity"] for f in findings)
    cat_counts = Counter(f["category"] for f in findings)
    areas = defaultdict(list)
    for f in findings:
        areas[f["area"]].append(f)

    top5 = top_items(findings, 5)
    top10 = top_items(findings, 10)
    suspect = suspicious(findings)
    grouped_backlog = backlog(findings)

    lines = []
    lines.append("# QA Report")
    lines.append("")
    lines.append("## 1. RESUMEN EJECUTIVO")
    lines.append("")
    lines.append(f"- Total hallazgos: `{total}`")
    lines.append("- Severidad:")
    for sev in SEVERITIES:
        lines.append(f"  - {sev}: `{sev_counts.get(sev, 0)}`")
    lines.append("- Categorías:")
    for cat in CATEGORIES:
        lines.append(f"  - {cat}: `{cat_counts.get(cat, 0)}`")
    lines.append("")

    if total == 0:
        status = "No se detectaron hallazgos en esta corrida. Queda riesgo residual por cobertura de pruebas manuales."
    elif sev_counts.get("critical", 0) > 0:
        status = "Estado general comprometido: hay hallazgos críticos que afectan lógica base, persistencia o funcionamiento principal."
    elif sev_counts.get("high", 0) > 0:
        status = "Estado general estable con riesgos altos: se recomienda corregir antes de incorporar nuevas features."
    else:
        status = "Estado general aceptable con mejoras pendientes de calidad y consistencia."
    lines.append("Estado general:")
    lines.append(status)
    lines.append("")

    lines.append("Top 5 problemas más importantes:")
    if not top5:
        lines.append("1. No aplica (sin hallazgos)")
    else:
        for idx, item in enumerate(top5, start=1):
            lines.append(f"{idx}. [{item['severity']}] {item['title']} ({item['area']})")

    lines.append("")
    lines.append("## 2. TABLA PRINCIPAL DE HALLAZGOS")
    lines.append("")
    lines.append("| ID | Severity | Category | Area | Title | What is wrong | Evidence | Suggested fix direction | Ready for prompt |")
    lines.append("|---|---|---|---|---|---|---|---|---|")

    for idx, item in enumerate(findings, start=1):
        item_id = f"QA-{idx:03d}"
        item["id"] = item_id
        row = [
            item_id,
            item["severity"],
            item["category"],
            item["area"],
            item["title"],
            item["what_is_wrong"],
            item["evidence"],
            item["suggested_fix_direction"],
            item["ready_for_prompt"],
        ]
        escaped = [str(part).replace("|", "\\|") for part in row]
        lines.append("| " + " | ".join(escaped) + " |")

    lines.append("")
    lines.append("## 3. HALLAZGOS AGRUPADOS POR ÁREA")
    lines.append("")
    if not areas:
        lines.append("Sin hallazgos para agrupar.")
    else:
        for area in sorted(areas.keys(), key=str.lower):
            lines.append(f"### {area}")
            for item in areas[area]:
                lines.append(f"- {item['id']}: [{item['severity']}/{item['category']}] {item['title']}")
            lines.append("")

    lines.append("## 4. BACKLOG PRIORIZADO")
    lines.append("")
    for bucket in ["Arreglar ya", "Arreglar pronto", "Limpieza / mejora"]:
        lines.append(f"### {bucket}")
        entries = grouped_backlog[bucket]
        if not entries:
            lines.append("- Sin items.")
        else:
            for item in entries:
                lines.append(f"- {item['id']}: {item['title']} ({item['area']})")
        lines.append("")

    lines.append("## 5. DUPLICADOS / DATOS SOSPECHOSOS")
    lines.append("")
    if not suspect:
        lines.append("- No se detectaron casos marcados como sospechosos en esta corrida.")
    else:
        for item in suspect:
            lines.append(f"- {item['id']}: [{item['category']}] {item['title']} ({item['area']})")

    lines.append("")
    lines.append("## 6. OBSERVACIONES GENERALES")
    lines.append("")
    if total == 0:
        lines.append("- Sin observaciones relevantes en esta corrida.")
    else:
        dominant_category = cat_counts.most_common(1)[0][0]
        dominant_area = Counter(f["area"] for f in findings).most_common(1)[0][0]
        lines.append(f"- Patrón dominante por categoría: `{dominant_category}`.")
        lines.append(f"- Área más afectada: `{dominant_area}`.")
        lines.append("- Revisar primero hallazgos `critical/high` con `ready_for_prompt=yes` para delegar fixes rápidos.")

    lines.append("")
    lines.append("## Recommended next actions")
    lines.append("")
    if not top10:
        lines.append("1. Ejecutar una nueva corrida de auditoría con mayor cobertura manual.")
    else:
        for idx, item in enumerate(top10, start=1):
            lines.append(f"{idx}. Resolver {item['id']} ({item['severity']}): {item['suggested_fix_direction']}")

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Consolidar hallazgos JSONL y generar QA_REPORT.md")
    parser.add_argument("--input-dir", default="agents/collection-qa-auditor/runs/latest", help="Directorio con stage1_functional.jsonl, stage2_data.jsonl y stage3_visual.jsonl")
    parser.add_argument("--output", default="QA_REPORT.md", help="Ruta de salida del reporte markdown")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    sources = [
        input_dir / "stage1_functional.jsonl",
        input_dir / "stage2_data.jsonl",
        input_dir / "stage3_visual.jsonl",
    ]

    all_items = []
    for path in sources:
        all_items.extend(load_jsonl(path))

    findings = dedupe_findings(all_items)
    report = render_report(findings)
    Path(args.output).write_text(report, encoding="utf-8")

    print(f"Generated {args.output} with {len(findings)} finding(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
