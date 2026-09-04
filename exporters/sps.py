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


def make_exclusion_syntax(id_variable: str, ids_to_exclude: list[str], id_is_string: bool,
                           comment: str = "") -> str:
    """
    Gera uma sintaxe SPSS que apaga (SELECT IF ... EXECUTE) os casos cujo ID
    está na lista de exclusão por duplicidade — os demais casos permanecem.
    Usa AND encadeado (em vez de ANY(), que tem limite de argumentos no SPSS)
    para funcionar com qualquer quantidade de IDs, e quebra em várias linhas
    só por legibilidade.
    """
    lines = [
        "* Sintaxe gerada pelo Gerador de Controle Geral — exclusão de duplicidades.",
        f"* Variável identificadora: {id_variable}",
        f"* Casos a excluir: {len(ids_to_exclude)}",
    ]
    if comment:
        lines.append(f"* {comment}")
    lines.append("")

    if not ids_to_exclude:
        lines.append("* Nenhum caso a excluir — nada para fazer.")
        return "\n".join(lines)

    def literal(value: str) -> str:
        if id_is_string:
            return "'" + str(value).replace("'", "''") + "'"
        return str(value)

    conditions = [f"{id_variable} <> {literal(v)}" for v in ids_to_exclude]
    lines.append("SELECT IF (")
    per_line = 4
    for i in range(0, len(conditions), per_line):
        chunk = conditions[i:i + per_line]
        suffix = " AND" if i + per_line < len(conditions) else ""
        lines.append("    " + " AND ".join(chunk) + suffix)
    lines.append(").")
    lines.append("EXECUTE.")
    return "\n".join(lines)
