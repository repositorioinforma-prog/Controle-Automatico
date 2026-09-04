from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import streamlit as st

from core.duplicates import (
    DATE_LABEL_PATTERNS, DuplicateConfig, NAME_LABEL_PATTERNS, PHONE_LABEL_PATTERNS,
    find_column_by_label, find_duplicates,
)
from core.geographic_validation import build_geographic_coherence_report, build_cidade_bairro_realocation
from core.recoding import ControlVariableConfig, recode_dataframe
from core.value_labels import parse_all_value_labels_sps
from exporters.sps import make_exclusion_syntax, make_value_labels_syntax
from exporters.sav import write_sav_bytes
from geography.database import load_geography_excel, UF_OPTIONS, uf_option_to_sigla
from importers.sav import read_sav_bytes, variable_catalog


st.set_page_config(page_title="Gerador de Controle Geral", layout="wide")
st.title("Gerador de Controle Geral")
st.caption("MVP 2.1 — Base Brasil, recodificação geográfica e coerência Cidade × Bairro × Distrito")

# Versão do formato dos objetos gravados na sessão do Streamlit.
# Quando o app evolui e novas colunas entram na auditoria, uma sessão antiga pode
# continuar viva após o arquivo app.py ser substituído. Nesse caso limpamos apenas
# os resultados derivados, preservando os arquivos/configurações que ainda forem válidos.
SESSION_SCHEMA_VERSION = 2
if st.session_state.get("session_schema_version") != SESSION_SCHEMA_VERSION:
    for key in ["audit_df", "output_df", "generated_label_sets", "coherence_df", "realocacao_df"]:
        st.session_state.pop(key, None)
    st.session_state.session_schema_version = SESSION_SCHEMA_VERSION

if "control_configs" not in st.session_state:
    st.session_state.control_configs = []


# --- Caches: sem isso, o Streamlit reprocessa banco, syntax e Base Brasil do
# zero a cada interação em QUALQUER parte do app (mudar um slider na etapa 2,
# por exemplo), porque ele reexecuta o script inteiro a cada clique. Com
# st.cache_data/st.cache_resource, a mesma entrada (mesmos bytes do arquivo)
# retorna do cache em vez de reler/reparsear tudo de novo.
@st.cache_data(show_spinner="Lendo banco .sav...")
def _cached_read_sav(data: bytes):
    return read_sav_bytes(data)


@st.cache_data(show_spinner="Lendo VALUE LABELS...")
def _cached_parse_sps(text: str):
    return parse_all_value_labels_sps(text)


@st.cache_resource(show_spinner="Carregando Base Brasil interna...")
def _cached_geography_from_path(path_str: str):
    path = Path(path_str)
    return load_geography_excel(path, source_name=path.name)


@st.cache_data(show_spinner="Carregando Base Brasil...")
def _cached_geography_from_bytes(data: bytes, name: str):
    return load_geography_excel(data, source_name=name)


def _load_internal_geography():
    candidates = [
        Path("data/Cidades_Bairros_Distritos - Brasil.xlsx"),
        Path("data/Cidades_Bairros_Distritos - Brasil(1).xlsx"),
    ]
    for path in candidates:
        if path.exists():
            return _cached_geography_from_path(str(path))
    return None


step1, step2, step3, step4, step5, step6 = st.tabs([
    "1. Importação", "2. Configuração", "3. Processamento", "4. Revisão", "5. Resultados / Exportação",
    "6. Duplicidades",
])

with step1:
    st.subheader("Arquivos do projeto")
    col1, col2 = st.columns(2)
    with col1:
        sav_file = st.file_uploader("Banco da pesquisa (.sav)", type=["sav"], key="sav")
    with col2:
        sps_file = st.file_uploader("VALUE LABELS do projeto (.sps)", type=["sps"], key="sps")

    st.subheader("Base geográfica do Brasil")
    internal_geo = _load_internal_geography()
    if internal_geo is not None:
        st.session_state.geography_db = internal_geo
        st.success(f"Base Brasil interna carregada: {internal_geo.source_name} — não precisa enviar de novo.")
        with st.expander("Usar outra planilha só desta vez (opcional)"):
            geo_file_override = st.file_uploader(
                "Base Brasil alternativa (.xlsx/.xls)", type=["xlsx", "xls"], key="geo_override"
            )
            if geo_file_override:
                try:
                    st.session_state.geography_db = _cached_geography_from_bytes(
                        geo_file_override.getvalue(), geo_file_override.name
                    )
                    st.info("Usando a planilha enviada nesta sessão, no lugar da base interna.")
                except Exception as exc:
                    st.error(f"Não foi possível ler a Base Brasil enviada: {exc}")
    else:
        st.info(
            "A Base Brasil ainda não está embutida nesta cópia do projeto. Envie a planilha abaixo. "
            "Ao distribuir o app, basta colocá-la na pasta data/ para não precisar enviá-la a cada uso."
        )
        geo_file = st.file_uploader(
            "Base Brasil — Cidades, Bairros e Distritos (.xlsx/.xls)", type=["xlsx", "xls"], key="geo"
        )
        if geo_file:
            try:
                st.session_state.geography_db = _cached_geography_from_bytes(
                    geo_file.getvalue(), geo_file.name
                )
            except Exception as exc:
                st.error(f"Não foi possível ler a Base Brasil: {exc}")

    if "geography_db" in st.session_state:
        geo = st.session_state.geography_db
        summary = geo.summary()
        cols = st.columns(5)
        for col, key, title in zip(
            cols,
            ["registros", "municipios", "distritos", "bairros", "ufs"],
            ["Registros", "Municípios", "Distritos", "Bairros", "UFs"],
        ):
            col.metric(title, summary[key])
        with st.expander("Prévia da Base Brasil"):
            st.dataframe(geo.dataframe().head(500), hide_index=True, use_container_width=True)

    if sav_file and sps_file:
        with st.spinner("Lendo banco e VALUE LABELS..."):
            df, meta = _cached_read_sav(sav_file.getvalue())
            label_sets = _cached_parse_sps(
                sps_file.getvalue().decode("utf-8-sig", errors="replace")
            )
        st.session_state.df = df
        st.session_state.meta = meta
        st.session_state.label_sets = label_sets
        st.success(f"Banco carregado: {len(df):,} entrevistas e {len(df.columns)} variáveis.")
        st.success(f"Syntax carregada: {len(label_sets)} conjunto(s) de VALUE LABELS encontrado(s).")
        st.dataframe(pd.DataFrame(variable_catalog(df, meta)), hide_index=True, use_container_width=True)
        with st.expander("VALUE LABELS encontrados"):
            for name, labels in label_sets.items():
                st.write(f"**{name}** — {len(labels)} categorias")

if "df" not in st.session_state or "label_sets" not in st.session_state:
    st.info("Envie um banco .sav e uma syntax .sps na etapa 1 para continuar.")
    st.stop()


df = st.session_state.df
meta = st.session_state.meta
label_sets = st.session_state.label_sets
columns = list(df.columns)
label_names = list(label_sets)

if not label_names:
    st.error("Nenhum bloco VALUE LABELS válido foi encontrado na syntax.")
    st.stop()

with step2:
    st.subheader("Configuração do projeto")
    id_default = next(
        (i for i, c in enumerate(columns) if c.lower() in {"respondent_id", "responde", "id"}), 0
    )
    st.selectbox("Variável identificadora (ID)", columns, index=id_default, key="id_column")

    st.divider()
    st.markdown("#### Abrangência geográfica da pesquisa")
    st.caption(
        "Restringe a Base Brasil aos estados da pesquisa antes de comparar. Reduz ambiguidade "
        "(ex.: um bairro 'Centro' que existe em várias cidades do Brasil passa a ser buscado só "
        "dentro do(s) estado(s) escolhido(s)) e deixa o processamento mais rápido."
    )
    abrangencia = st.radio(
        "A pesquisa cobre:",
        ["Um ou mais estados específicos", "Brasil inteiro"],
        horizontal=True,
        key="abrangencia_geografica",
    )
    if abrangencia == "Um ou mais estados específicos":
        estados_selecionados = st.multiselect(
            "Estado(s) da pesquisa", UF_OPTIONS,
            help="Ex.: uma pesquisa no Rio de Janeiro deve marcar só 'RJ — Rio de Janeiro'.",
            key="estados_pesquisa",
        )
        st.session_state.ufs_pesquisa = [uf_option_to_sigla(o) for o in estados_selecionados]
    else:
        st.session_state.ufs_pesquisa = []

    if "geography_db" in st.session_state:
        base_geo = st.session_state.geography_db
        efetiva = base_geo.filter_by_uf(st.session_state.get("ufs_pesquisa") or None)
        if st.session_state.get("ufs_pesquisa"):
            st.info(
                f"Base Brasil restrita a {', '.join(st.session_state.ufs_pesquisa)}: "
                f"{efetiva.summary()['registros']:,} de {base_geo.summary()['registros']:,} registros serão usados."
            )

    st.divider()
    st.markdown("#### Nova variável de controle")
    c1, c2, c3 = st.columns(3)
    with c1:
        output_name = st.text_input("Nome da nova variável", placeholder="Ex.: Cidades")
    with c2:
        geographic_type_ui = st.selectbox(
            "Tipo de interpretação",
            ["Município", "Bairro", "Distrito", "Não geográfica"],
            help="Uma fonte de bairro pode ser usada para descobrir o município quando o tipo escolhido for Município.",
        )
    with c3:
        label_set = st.selectbox("VALUE LABELS que define a amostra/códigos", label_names)

    source_columns = st.multiselect(
        "Variáveis-fonte (na ordem de prioridade)",
        columns,
        help="Selecione também fontes auxiliares. Ex.: para Cidades, cidade + cidade_outros + bairro.",
    )
    c4, c5 = st.columns(2)
    with c4:
        fuzzy_cutoff = st.slider("Fuzzy: mínimo para sugerir", 0.50, 1.00, 0.82, 0.01)
    with c5:
        auto_fuzzy_threshold = st.slider("Fuzzy: mínimo para ajuste automático", 0.50, 1.00, 0.93, 0.01)

    geographic_type = {
        "Município": "municipio",
        "Bairro": "bairro",
        "Distrito": "distrito",
        "Não geográfica": None,
    }[geographic_type_ui]

    if st.button("Adicionar variável de controle", type="primary"):
        if not output_name.strip():
            st.error("Informe o nome da nova variável.")
        elif not source_columns:
            st.error("Selecione ao menos uma variável-fonte.")
        elif geographic_type and "geography_db" not in st.session_state:
            st.error("Para uma variável geográfica, carregue primeiro a Base Brasil na etapa 1.")
        elif any(c["output_name"].casefold() == output_name.strip().casefold() for c in st.session_state.control_configs):
            st.error("Já existe uma variável com esse nome.")
        else:
            st.session_state.control_configs.append({
                "output_name": output_name.strip(),
                "label_set_name": label_set,
                "source_columns": tuple(source_columns),
                "geographic_type": geographic_type,
                "fuzzy_cutoff": fuzzy_cutoff,
                "auto_fuzzy_threshold": auto_fuzzy_threshold,
            })
            st.success("Variável adicionada.")

    if st.session_state.control_configs:
        st.dataframe(pd.DataFrame([
            {
                "Nova variável": c["output_name"],
                "Tipo": c.get("geographic_type") or "não geográfica",
                "VALUE LABELS": c["label_set_name"],
                "Fontes": " → ".join(c["source_columns"]),
            }
            for c in st.session_state.control_configs
        ]), hide_index=True, use_container_width=True)
        if st.button("Limpar configuração"):
            st.session_state.control_configs = []
            for key in ["audit_df", "output_df", "generated_label_sets", "coherence_df", "realocacao_df"]:
                st.session_state.pop(key, None)
            st.rerun()

with step3:
    st.subheader("Processamento")
    if not st.session_state.control_configs:
        st.info("Configure ao menos uma variável de controle na etapa 2.")
    elif st.button("Processar controle", type="primary"):
        all_audits = []
        output_df = df.copy()
        generated_label_sets = {}
        bank_value_labels = getattr(meta, "variable_value_labels", {}) or {}
        geography_db = st.session_state.get("geography_db")
        ufs_pesquisa = st.session_state.get("ufs_pesquisa") or None
        if geography_db is not None and ufs_pesquisa:
            geography_db = geography_db.filter_by_uf(ufs_pesquisa)

        with st.spinner("Interpretando respostas e verificando a Base Brasil..."):
            for raw in st.session_state.control_configs:
                config = ControlVariableConfig(**raw)
                labels = label_sets[config.label_set_name]
                audit = recode_dataframe(
                    df, config, labels, bank_value_labels, st.session_state.id_column,
                    geography_db=geography_db,
                )
                all_audits.append(audit)
                codes = pd.Series(pd.NA, index=df.index, dtype="Float64")
                auto = audit["decisao_automatica"] & audit["codigo_sugerido"].notna()
                codes.loc[auto.to_numpy()] = audit.loc[auto, "codigo_sugerido"].astype(float).to_numpy()
                output_df[config.output_name] = codes
                generated_label_sets[config.output_name] = labels

        audit_df = pd.concat(all_audits, ignore_index=True)
        coherence_df = build_geographic_coherence_report(
            audit_df, st.session_state.control_configs, label_sets
        )
        st.session_state.audit_df = audit_df
        st.session_state.output_df = output_df
        st.session_state.generated_label_sets = generated_label_sets
        st.session_state.coherence_df = coherence_df
        st.session_state.pop("realocacao_df", None)

        st.success("Processamento concluído.")
        counts = audit_df["status"].value_counts()
        statuses = [
            "CONFIRMADO", "AJUSTADO AUTOMATICAMENTE", "SUGESTÃO", "AMBÍGUO",
            "FORA DA AMOSTRA", "NÃO IDENTIFICADO",
        ]
        for col, status in zip(st.columns(6), statuses):
            col.metric(status.title(), int(counts.get(status, 0)))

        st.dataframe(audit_df, hide_index=True, use_container_width=True)
        if not coherence_df.empty:
            st.error(f"Foram encontradas {len(coherence_df)} inconsistência(s) de coerência geográfica.")
            st.dataframe(coherence_df, hide_index=True, use_container_width=True)

with step4:
    st.subheader("Revisão")
    if "audit_df" not in st.session_state:
        st.info("Execute o processamento na etapa 3.")
    else:
        audit_df = st.session_state.audit_df
        review_status = {"SUGESTÃO", "AMBÍGUO", "FORA DA AMOSTRA", "NÃO IDENTIFICADO"}
        review = audit_df[audit_df["status"].isin(review_status)].copy()
        if review.empty:
            st.success("Não há pendências individuais de matching para revisão.")
        else:
            st.warning(f"{len(review)} caso(s) requerem revisão humana.")
            cols_review = [
                "ID", "variavel_controle", "texto_interpretado", "texto_normalizado", "fonte_utilizada", "status",
                "label_sugerido", "localidade_base", "tipo_localidade_base", "municipio_base",
                "uf_base", "candidatos", "metodo", "confianca",
            ]
            # reindex é intencional: além de tolerar resultados antigos mantidos pela
            # sessão do Streamlit, evita KeyError se uma futura modalidade de auditoria
            # não produzir alguma coluna geográfica opcional.
            review_display = review.reindex(columns=cols_review, fill_value="")
            st.dataframe(review_display, hide_index=True, use_container_width=True)

            st.divider()
            st.markdown("#### Aceitar sugestões em lote")
            st.caption(
                "Aplica de uma vez os casos com status SUGESTÃO acima de uma confiança mínima — "
                "você decide o corte, em vez do sistema decidir sozinho. Fica registrado no relatório "
                "quais casos foram aceitos em lote (não é a mesma coisa que 'automático')."
            )
            sugestoes = audit_df[audit_df["status"] == "SUGESTÃO"].copy()
            if sugestoes.empty:
                st.info("Não há sugestões pendentes no momento.")
            else:
                vars_disponiveis = sorted(sugestoes["variavel_controle"].unique())
                col_a, col_b = st.columns([2, 1])
                with col_a:
                    vars_escolhidas = st.multiselect(
                        "Variáveis", vars_disponiveis, default=vars_disponiveis, key="lote_vars"
                    )
                with col_b:
                    corte_confianca = st.slider("Confiança mínima", 0.50, 1.00, 0.85, 0.01, key="lote_corte")
                elegiveis = sugestoes[
                    sugestoes["variavel_controle"].isin(vars_escolhidas)
                    & (sugestoes["confianca"] >= corte_confianca)
                    & sugestoes["codigo_sugerido"].notna()
                ]
                st.write(f"**{len(elegiveis)}** de {len(sugestoes)} sugestões atingem esse corte.")
                if not elegiveis.empty:
                    st.dataframe(
                        elegiveis[["ID", "variavel_controle", "texto_interpretado", "label_sugerido",
                                   "metodo", "confianca"]],
                        hide_index=True, use_container_width=True,
                    )
                if st.button("Aplicar aceitação em lote", type="primary", disabled=elegiveis.empty):
                    output_df = st.session_state.output_df
                    audit_df_atualizado = st.session_state.audit_df.copy()
                    for var in vars_escolhidas:
                        var_audit = audit_df_atualizado[audit_df_atualizado["variavel_controle"] == var]
                        mask = (
                            (var_audit["status"] == "SUGESTÃO")
                            & (var_audit["confianca"] >= corte_confianca)
                            & var_audit["codigo_sugerido"].notna()
                        )
                        if var not in output_df.columns or not mask.any():
                            continue
                        positions = mask.to_numpy()
                        output_df.loc[output_df.index[positions], var] = (
                            var_audit.loc[mask, "codigo_sugerido"].astype(float).to_numpy()
                        )
                        idx_global = var_audit.index[mask]
                        audit_df_atualizado.loc[idx_global, "status"] = "ACEITO EM LOTE"
                        audit_df_atualizado.loc[idx_global, "decisao_automatica"] = True
                    st.session_state.output_df = output_df
                    st.session_state.audit_df = audit_df_atualizado
                    st.success(f"{len(elegiveis)} caso(s) aplicado(s). Confira o resultado na Etapa 5.")
                    st.rerun()

        coherence = st.session_state.get("coherence_df", pd.DataFrame())
        if not coherence.empty:
            st.markdown("#### Cidade × Bairro/Distrito")
            st.dataframe(coherence, hide_index=True, use_container_width=True)

        st.divider()
        st.markdown("#### Verificação cruzada Cidade × Bairro (realocação)")
        st.caption(
            "Confere as duas pontas: (1) o bairro respondido pertence, na Base Brasil, a outro município "
            "da amostra; (2) o que foi escrito no campo de Bairro é, na verdade, o nome de outra cidade "
            "(mesmo que outra cidade tenha sido marcada na pergunta de Cidade). Só sugere quando o município "
            "alternativo está nos VALUE LABELS válidos do projeto — nunca aplica sozinho."
        )
        configs = st.session_state.control_configs
        municipal_cfgs = [c for c in configs if c.get("geographic_type") == "municipio"]
        child_cfgs = [c for c in configs if c.get("geographic_type") in {"bairro", "distrito"}]
        if not municipal_cfgs or not child_cfgs:
            st.info("Configure uma variável de Município e uma de Bairro/Distrito na Etapa 2 para usar esta verificação.")
        else:
            city_cfg = municipal_cfgs[0]
            if len(child_cfgs) == 1:
                child_cfg_choice = child_cfgs[0]
            else:
                child_cfg_choice = st.selectbox(
                    "Variável de Bairro/Distrito a usar na verificação",
                    child_cfgs, format_func=lambda c: c["output_name"], key="realoc_child_cfg",
                )
            if st.button("Verificar realocações Cidade × Bairro"):
                with st.spinner("Cruzando as duas respostas..."):
                    geography_db_atual = st.session_state.get("geography_db")
                    ufs = st.session_state.get("ufs_pesquisa") or None
                    if geography_db_atual is not None and ufs:
                        geography_db_atual = geography_db_atual.filter_by_uf(ufs)
                    bank_value_labels_atual = getattr(meta, "variable_value_labels", {}) or {}
                    st.session_state.realocacao_df = build_cidade_bairro_realocation(
                        df, st.session_state.audit_df, city_cfg, child_cfg_choice,
                        label_sets, bank_value_labels_atual, geography_db_atual,
                        st.session_state.id_column,
                    )

            realoc_df = st.session_state.get("realocacao_df")
            if realoc_df is not None:
                if realoc_df.empty:
                    st.success("Nenhuma realocação sugerida com os dados atuais.")
                else:
                    aplicaveis = realoc_df[realoc_df["status"] == "INCONSISTENTE"]
                    ambiguos = realoc_df[realoc_df["status"] == "AMBÍGUO"]
                    if not aplicaveis.empty:
                        st.warning(f"{len(aplicaveis)} entrevista(s) com realocação de Cidade sugerida.")
                        st.dataframe(aplicaveis, hide_index=True, use_container_width=True)
                    if not ambiguos.empty:
                        st.info(
                            f"{len(ambiguos)} caso(s) em que o texto do Bairro bate como bairro de uma cidade "
                            "E como nome de outra cidade ao mesmo tempo — as duas leituras discordam, então "
                            "não sugerimos sozinho. Revise manualmente:"
                        )
                        st.dataframe(ambiguos, hide_index=True, use_container_width=True)
                    if not aplicaveis.empty and st.button("Aplicar realocações à variável de Cidade", type="primary"):
                        output_df = st.session_state.output_df
                        audit_df_atualizado = st.session_state.audit_df.copy()
                        city_var = city_cfg["output_name"]
                        if city_var not in output_df.columns:
                            st.error(f"A variável '{city_var}' não existe no banco processado.")
                        else:
                            id_series = df[st.session_state.id_column].astype(str)
                            realoc_ids = aplicaveis["ID"].astype(str)
                            code_by_id = dict(zip(realoc_ids, pd.to_numeric(aplicaveis["codigo_cidade_sugerido"])))
                            label_by_id = dict(zip(realoc_ids, aplicaveis["municipio_base"]))
                            mask = id_series.isin(realoc_ids)
                            output_df.loc[mask, city_var] = id_series[mask].map(code_by_id).astype(float).to_numpy()

                            audit_ids = audit_df_atualizado["ID"].astype(str)
                            row_mask = (audit_df_atualizado["variavel_controle"] == city_var) & audit_ids.isin(realoc_ids)
                            audit_df_atualizado.loc[row_mask, "status"] = "REALOCADO (Cidade × Bairro)"
                            audit_df_atualizado.loc[row_mask, "codigo_sugerido"] = audit_ids[row_mask].map(code_by_id).to_numpy()
                            audit_df_atualizado.loc[row_mask, "label_sugerido"] = audit_ids[row_mask].map(label_by_id).to_numpy()
                            audit_df_atualizado.loc[row_mask, "decisao_automatica"] = True

                            st.session_state.output_df = output_df
                            st.session_state.audit_df = audit_df_atualizado
                            st.success(f"{len(aplicaveis)} entrevista(s) realocada(s). Confira o resultado na Etapa 5.")
                            st.rerun()

with step5:
    st.subheader("Resultados / Exportação")
    if "audit_df" not in st.session_state:
        st.info("Execute o processamento na etapa 3.")
    else:
        audit_df = st.session_state.audit_df
        status_filter = st.multiselect(
            "Filtrar status",
            sorted(audit_df["status"].unique()),
            default=sorted(audit_df["status"].unique()),
        )
        st.dataframe(
            audit_df[audit_df["status"].isin(status_filter)],
            hide_index=True,
            use_container_width=True,
        )

        st.download_button(
            "Baixar auditoria (.csv)",
            audit_df.to_csv(index=False).encode("utf-8-sig"),
            "controle_geral_auditoria.csv",
            "text/csv",
        )

        coherence = st.session_state.get("coherence_df", pd.DataFrame())
        if not coherence.empty:
            st.download_button(
                "Baixar inconsistências geográficas (.csv)",
                coherence.to_csv(index=False).encode("utf-8-sig"),
                "inconsistencias_geograficas.csv",
                "text/csv",
            )

        buffer = io.BytesIO()
        st.session_state.output_df.to_excel(buffer, index=False)
        st.download_button(
            "Baixar banco controlado provisório (.xlsx)",
            buffer.getvalue(),
            "banco_controlado_provisorio.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        syntax = make_value_labels_syntax(st.session_state.generated_label_sets)
        st.download_button(
            "Baixar VALUE LABELS gerados (.sps)",
            syntax.encode("utf-8-sig"),
            "aplicar_value_labels_controle.sps",
            "text/plain",
        )

        st.divider()
        st.markdown("#### Banco final em SPSS (.sav)")
        st.caption(
            "Gera o banco original com as novas variáveis de controle já incluídas (código numérico + "
            "VALUE LABELS), prontas para abrir no SPSS. Casos ainda não resolvidos automaticamente ficam "
            "em branco na variável, para não aplicar uma decisão sem confiança suficiente."
        )
        if st.button("Gerar banco .sav com as variáveis de controle"):
            with st.spinner("Gravando .sav (isso pode levar alguns segundos em bancos grandes)..."):
                sav_output_df = df.copy()
                sav_column_labels = dict(getattr(meta, "column_names_to_labels", {}) or {})
                sav_value_labels = dict(getattr(meta, "variable_value_labels", {}) or {})
                for config_raw in st.session_state.control_configs:
                    name = config_raw["output_name"]
                    sav_output_df[name] = st.session_state.output_df[name]
                    sav_column_labels[name] = f"{name} — gerado pelo Controle Geral"
                    sav_value_labels[name] = st.session_state.generated_label_sets[name]
                sav_bytes = write_sav_bytes(
                    sav_output_df,
                    column_labels=sav_column_labels,
                    variable_value_labels=sav_value_labels,
                    file_label="Banco controlado - Gerador de Controle Geral",
                )
                st.session_state.sav_bytes = sav_bytes
        if "sav_bytes" in st.session_state:
            st.download_button(
                "Baixar banco controlado (.sav)",
                st.session_state.sav_bytes,
                "banco_controlado.sav",
                "application/octet-stream",
            )
        st.caption(
            "Casos não automáticos permanecem vazios no banco provisório. A auditoria preserva a resposta, "
            "a interpretação da Base Brasil, a validade na amostra e o motivo da pendência."
        )

with step6:
    st.subheader("Duplicidades")
    if True:
        st.caption(
            "Detecta entrevistas duplicadas por nome + telefone. As variáveis são achadas automaticamente "
            "pelo LABEL delas no banco (ex.: label 'Nome', label 'Tel.') — confira/ajuste se necessário."
        )
        column_labels = getattr(meta, "column_names_to_labels", {}) or {}
        auto_name = find_column_by_label(column_labels, NAME_LABEL_PATTERNS)
        auto_phone = find_column_by_label(column_labels, PHONE_LABEL_PATTERNS)
        auto_date = find_column_by_label(column_labels, DATE_LABEL_PATTERNS, also_check_names=True)

        columns = list(df.columns)
        none_option = "(nenhuma)"
        options = [none_option] + columns

        def _label_of(col):
            if col == none_option:
                return col
            lbl = column_labels.get(col, "")
            return f"{col} — {lbl}" if lbl else col

        col1, col2, col3 = st.columns(3)
        with col1:
            name_col = st.selectbox(
                "Variável de Nome", options,
                index=options.index(auto_name) if auto_name in options else 0,
                format_func=_label_of, key="dup_name_col",
            )
        with col2:
            phone_col = st.selectbox(
                "Variável de Telefone", options,
                index=options.index(auto_phone) if auto_phone in options else 0,
                format_func=_label_of, key="dup_phone_col",
            )
        with col3:
            date_col = st.selectbox(
                "Variável de Data (para saber qual é a mais antiga)", options,
                index=options.index(auto_date) if auto_date in options else 0,
                format_func=_label_of, key="dup_date_col",
            )

        if name_col == none_option and phone_col == none_option:
            st.warning("Selecione ao menos Nome ou Telefone para poder buscar duplicidades.")
        else:
            if st.button("Analisar duplicidades", type="primary"):
                with st.spinner("Comparando nomes e telefones..."):
                    dup_config = DuplicateConfig(
                        id_column=st.session_state.id_column,
                        name_column=None if name_col == none_option else name_col,
                        phone_column=None if phone_col == none_option else phone_col,
                        date_column=None if date_col == none_option else date_col,
                    )
                    st.session_state.duplicates_df = find_duplicates(df, dup_config)
                    st.session_state.duplicates_id_column = dup_config.id_column

        if "duplicates_df" in st.session_state:
            dup_df = st.session_state.duplicates_df
            if dup_df.empty:
                st.success("Nenhuma duplicidade encontrada com os critérios atuais.")
            else:
                tipo_filter = st.multiselect(
                    "Filtrar por tipo de duplicidade",
                    ["certa", "provavel"],
                    default=["certa", "provavel"],
                    format_func=lambda t: {
                        "certa": "Certa (mesmo telefone)",
                        "provavel": "Provável (mesmo nome + telefone parecido)",
                    }[t],
                )
                dup_df_filtrado = dup_df[dup_df["tipo_duplicidade"].isin(tipo_filter)]

                n_grupos = dup_df_filtrado["grupo"].nunique()
                n_excluir = int((dup_df_filtrado["recomendacao"] == "excluir").sum())
                c1, c2, c3 = st.columns(3)
                c1.metric("Grupos de duplicidade", n_grupos)
                c2.metric("Entrevistas envolvidas", len(dup_df_filtrado))
                c3.metric("Recomendadas para exclusão", n_excluir)

                st.dataframe(dup_df_filtrado, hide_index=True, use_container_width=True)
                st.caption(
                    "Nenhuma entrevista é excluída automaticamente do banco — isso só acontece se você "
                    "aplicar a sintaxe .sps abaixo no seu banco no SPSS. Os arquivos abaixo seguem o "
                    "filtro de tipo de duplicidade selecionado acima."
                )

                ids_excluir = dup_df_filtrado.loc[dup_df_filtrado["recomendacao"] == "excluir", "ID"].tolist()
                st.divider()
                st.markdown("#### Exportar exclusões")

                txt_content = "\n".join(ids_excluir)
                st.download_button(
                    "Baixar códigos a excluir (.txt)",
                    txt_content.encode("utf-8-sig"),
                    "entrevistas_duplicadas_excluir.txt",
                    "text/plain",
                    disabled=not ids_excluir,
                )

                id_col_name = st.session_state.duplicates_id_column
                id_is_string = not pd.api.types.is_numeric_dtype(df[id_col_name])
                syntax = make_exclusion_syntax(
                    id_col_name, ids_excluir, id_is_string,
                    comment="Mantida a entrevista mais antiga de cada grupo; demais marcadas para exclusão.",
                )
                st.download_button(
                    "Baixar sintaxe SPSS para excluir os duplicados (.sps)",
                    syntax.encode("utf-8-sig"),
                    "excluir_duplicidades.sps",
                    "text/plain",
                    disabled=not ids_excluir,
                )
                with st.expander("Ver sintaxe gerada"):
                    st.code(syntax, language="sql")
