from __future__ import annotations

import pandas as pd

from core.normalization import normalize_text


def project_label_lookup(labels: dict) -> dict[str, tuple[object, str]]:
    lookup = {}
    for code, label in labels.items():
        key = normalize_text(label)
        if key and key not in lookup:
            lookup[key] = (code, label)
    return lookup


def build_geographic_coherence_report(
    audit_df: pd.DataFrame,
    configs: list[dict],
    label_sets: dict[str, dict],
) -> pd.DataFrame:
    """Compare municipality control with municipality implied by bairro/distrito."""
    municipal = [c for c in configs if c.get("geographic_type") == "municipio"]
    children = [c for c in configs if c.get("geographic_type") in {"bairro", "distrito"}]
    if not municipal or not children or audit_df.empty:
        return pd.DataFrame()

    city_cfg = municipal[0]
    city_rows = audit_df[audit_df["variavel_controle"] == city_cfg["output_name"]].copy()
    city_rows = city_rows.set_index("ID", drop=False)
    city_project = project_label_lookup(label_sets[city_cfg["label_set_name"]])
    issues = []

    for child_cfg in children:
        child_rows = audit_df[audit_df["variavel_controle"] == child_cfg["output_name"]]
        for _, child in child_rows.iterrows():
            rid = child["ID"]
            implied = str(child.get("municipio_base", "") or "").strip()
            if not implied or rid not in city_rows.index:
                continue
            city = city_rows.loc[rid]
            if isinstance(city, pd.DataFrame):
                city = city.iloc[0]
            city_value = str(city.get("label_sugerido", "") or "").strip()
            if not city_value or normalize_text(city_value) == normalize_text(implied):
                continue

            valid = city_project.get(normalize_text(implied))
            if valid:
                status = "INCONSISTENTE"
                recommendation = f"Alterar {city_cfg['output_name']} para {valid[1]}"
                code = valid[0]
                reason = (
                    f"{child_cfg['output_name']} foi identificado como {child.get('localidade_base', '')}, "
                    f"pertencente a {implied}. O município está nos VALUE LABELS válidos do projeto."
                )
            else:
                status = "FORA DA AMOSTRA"
                recommendation = "Revisar; não recodificar automaticamente"
                code = None
                reason = (
                    f"{child_cfg['output_name']} indica o município {implied}, mas esse município não está "
                    f"nos VALUE LABELS de {city_cfg['output_name']} deste projeto."
                )
            issues.append({
                "ID": rid,
                "status": status,
                "cidade_controle_atual": city_value,
                "localidade_informada": child.get("texto_interpretado", ""),
                "localidade_identificada": child.get("localidade_base", ""),
                "municipio_base": implied,
                "codigo_cidade_sugerido": code,
                "recomendacao": recommendation,
                "motivo": reason,
            })
    return pd.DataFrame(issues)
