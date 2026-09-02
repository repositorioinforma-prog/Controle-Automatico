def make_value_labels_syntax(label_sets):
    lines = ["* VALUE LABELS gerados pelo Gerador de Controle Geral.", ""]
    for variable, labels in label_sets.items():
        lines.append(f"VALUE LABELS {variable}")
        for code in sorted(labels):
            label = str(labels[code]).replace("'", "''")
            lines.append(f"  {code} '{label}'")
        lines += ["  .", ""]
    lines.append("EXECUTE.")
    return "\n".join(lines)
