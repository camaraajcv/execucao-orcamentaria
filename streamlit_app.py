import streamlit as st
import requests
import zipfile
import io
import pandas as pd
import altair as alt

# ==========================
# CONFIG
# ==========================
st.set_page_config(
    page_title="Painel Orçamento/Despesa — Portal da Transparência",
    layout="wide",
    initial_sidebar_state="expanded",
)

BASE_PAGE = "https://portaldatransparencia.gov.br/download-de-dados/orcamento-despesa"
DEFAULT_YEAR = 2026  # ajuste se quiser

# ==========================
# FUNÇÕES (download + leitura)
# ==========================
@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)
def baixar_zip(url: str) -> bytes:
    headers = {
        "User-Agent": "Mozilla/5.0 (StreamlitCloud)",
        "Accept": "*/*",
        "Referer": "https://portaldatransparencia.gov.br/",
    }
    r = requests.get(url, headers=headers, timeout=240)
    r.raise_for_status()
    return r.content

def listar_arquivos_zip(zip_bytes: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        return z.namelist()

def extrair_csv_bytes(zip_bytes: bytes, csv_name: str):
    """
    Retorna:
    - bytes do CSV
    - nome do arquivo
    - data/hora de modificação do CSV (datetime)
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        names = z.namelist()

        if csv_name in names:
            chosen = csv_name
        else:
            csvs = [n for n in names if n.lower().endswith(".csv")]
            if not csvs:
                raise RuntimeError("Não encontrei CSV no ZIP.")
            chosen = csvs[0]

        info = z.getinfo(chosen)
        # info.date_time -> (YYYY, MM, DD, HH, MM, SS)
        dt_mod = pd.Timestamp(
            year=info.date_time[0],
            month=info.date_time[1],
            day=info.date_time[2],
            hour=info.date_time[3],
            minute=info.date_time[4],
            second=info.date_time[5],
        )

        with z.open(chosen) as f:
            return f.read(), chosen, dt_mod


def ler_csv(csv_bytes: bytes) -> pd.DataFrame:
    attempts = [
        {"sep": ";", "encoding": "utf-8-sig"},
        {"sep": ";", "encoding": "latin-1"},
        {"sep": ",", "encoding": "utf-8-sig"},
        {"sep": ",", "encoding": "latin-1"},
    ]
    last_err = None
    for a in attempts:
        try:
            bio = io.BytesIO(csv_bytes)
            df = pd.read_csv(bio, sep=a["sep"], encoding=a["encoding"], low_memory=False)
            return df
        except Exception as e:
            last_err = e
    raise RuntimeError(f"Falha ao ler CSV. Último erro: {last_err}")

# ==========================
# FUNÇÕES (limpeza/numéricos)
# ==========================
def norm_col(c: str) -> str:
    return str(c).strip().lower()

def find_col(df: pd.DataFrame, must_contain: str) -> str | None:
    m = must_contain.strip().lower()
    for c in df.columns:
        if m in norm_col(c):
            return c
    return None

def parse_brl_number_series(s: pd.Series) -> pd.Series:
    x = (
        s.astype(str)
        .str.replace("\xa0", "", regex=False)
        .str.replace("R$", "", regex=False)
        .str.replace(" ", "", regex=False)
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
        .str.strip()
    )
    return pd.to_numeric(x, errors="coerce")

def parse_percent_series(s: pd.Series) -> pd.Series:
    x = (
        s.astype(str)
        .str.replace("\xa0", "", regex=False)
        .str.replace("%", "", regex=False)
        .str.replace(" ", "", regex=False)
        .str.replace(".", "", regex=False)   # geralmente ponto é milhar; decimal vem com vírgula
        .str.replace(",", ".", regex=False)
        .str.strip()
    )
    out = pd.to_numeric(x, errors="coerce")
    if out.notna().any() and out.max(skipna=True) <= 1.5:
        out = out * 100
    return out

def filtrar_df(df: pd.DataFrame, filtros: dict) -> pd.DataFrame:
    out = df
    for col, vals in filtros.items():
        if vals and col in out.columns:
            out = out[out[col].astype(str).isin([str(v) for v in vals])]
    return out

def to_excel_bytes(df: pd.DataFrame) -> bytes:
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="dados")
    return out.getvalue()

# ==========================
# FORMATAÇÃO (tabelas)
# ==========================
def fmt_brl(x):
    """Formata número como moeda BR (R$ 1.234.567,89)."""
    try:
        v = float(x)
    except Exception:
        return x
    s = f"{v:,.2f}"                  # 1,234,567.89
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"

def pretty_agg_display(agg: pd.DataFrame) -> pd.DataFrame:
    df_show = agg.copy()

    # rename robusto (case-insensitive)
    rename_map = {}
    for c in df_show.columns:
        lc = str(c).strip().lower()
        if lc == "dim":
            rename_map[c] = "Dimensão"
        elif lc == "atualizado":
            rename_map[c] = "LOA (R$)"
        elif lc == "empenhado":
            rename_map[c] = "Orçamento Empenhado (R$)"
        elif lc == "realizado":
            rename_map[c] = "Orçamento Realizado (R$)"
        elif lc == "pct":
            rename_map[c] = "% Realizado (médio)"
    df_show = df_show.rename(columns=rename_map)

    money_cols = [c for c in [
        "LOA (R$)",
        "Orçamento Empenhado (R$)",
        "Orçamento Realizado (R$)",
    ] if c in df_show.columns]

    # garante numérico e transforma em string moeda
    for c in money_cols:
        df_show[c] = pd.to_numeric(df_show[c], errors="coerce").fillna(0.0).map(fmt_brl)

    if "% Realizado (médio)" in df_show.columns:
        df_show["% Realizado (médio)"] = pd.to_numeric(
            df_show["% Realizado (médio)"], errors="coerce"
        ).map(lambda x: "" if pd.isna(x) else f"{x:.2f}%")

    return df_show


# ==========================
# STATE
# ==========================
if "csv_updated_at" not in st.session_state:
    st.session_state.csv_updated_at = None

if "df" not in st.session_state:
    st.session_state.df = None
if "ano_carregado" not in st.session_state:
    st.session_state.ano_carregado = None
if "fonte_url" not in st.session_state:
    st.session_state.fonte_url = None
if "zip_files" not in st.session_state:
    st.session_state.zip_files = None
if "csv_name_used" not in st.session_state:
    st.session_state.csv_name_used = None

# ==========================
# TÍTULO
# ==========================
st.title("📊 Painel Orçamento/Despesa — Portal da Transparência")
st.caption("Dashboard interativo (FAÇA SEU FILTRO NO MENU LATERAL - CLICAR NAS SETINHAS).")
if st.session_state.csv_updated_at is not None:
    csv_dt = st.session_state.csv_updated_at

    # garante timezone
    if getattr(csv_dt, "tzinfo", None) is None:
        csv_dt = csv_dt.tz_localize("UTC")

    csv_dt = csv_dt.tz_convert("America/Sao_Paulo")

    st.markdown(
        f"""
        <div style="color:gray;font-size:0.9em; margin-top:-8px;">
        📅 Dados atualizados em: <b>{csv_dt.strftime('%d/%m/%Y às %H:%M')}</b> (horário de Brasília)
        </div>
        """,
        unsafe_allow_html=True
    )

# ==========================
# SIDEBAR (carregamento + filtros)
# ==========================
with st.sidebar:
    st.header("1) Carregar dados")
    ano = st.number_input(
        "Ano",
        min_value=2011,
        max_value=2100,
        value=int(st.session_state.ano_carregado) if st.session_state.ano_carregado else DEFAULT_YEAR,
        step=1,
    )
    fonte_url = f"{BASE_PAGE}/{int(ano)}"
    csv_name_expected = f"{int(ano)}_OrcamentoDespesa.csv"

    st.caption("Fonte:")
    st.write(fonte_url)
    st.caption("CSV esperado no ZIP:")
    st.code(csv_name_expected)

    c1, c2 = st.columns(2)
    with c1:
        carregar = st.button("⬇️ Carregar", use_container_width=True)
    with c2:
        limpar = st.button("🧹 Limpar", use_container_width=True)

    st.divider()
    st.header("2) Filtros")
    st.caption("Selecione colunas e valores. Os gráficos atualizam automaticamente.")

# limpar state
if limpar:
    st.session_state.df = None
    st.session_state.ano_carregado = None
    st.session_state.fonte_url = None
    st.session_state.zip_files = None
    st.session_state.csv_name_used = None
    st.rerun()

# carregar
if carregar:
    try:
        with st.spinner("Baixando ZIP do Portal…"):
            zip_bytes = baixar_zip(fonte_url)
        zip_files = listar_arquivos_zip(zip_bytes)

        with st.spinner("Extraindo CSV…"):
            csv_bytes, chosen_name, csv_updated_at = extrair_csv_bytes(
                zip_bytes, csv_name_expected
)


        with st.spinner("Lendo CSV…"):
            df = ler_csv(csv_bytes)
        st.session_state.csv_updated_at = csv_updated_at

        st.session_state.df = df
        st.session_state.ano_carregado = int(ano)
        st.session_state.fonte_url = fonte_url
        st.session_state.zip_files = zip_files
        st.session_state.csv_name_used = chosen_name

        st.success(f"✅ Carregado: {len(df):,} linhas × {len(df.columns)} colunas".replace(",", "."))
    except Exception as e:
        st.error("Erro ao carregar dados.")
        st.exception(e)

if st.session_state.df is None:
    st.info("Escolha o ano e clique em **Carregar**.")
    st.stop()

df = st.session_state.df

# ==========================
# DETECÇÃO DE COLUNAS IMPORTANTES (métricas)
# ==========================
COL_ATUALIZADO = find_col(df, "orçamento atualizado")
COL_EMPENHADO  = find_col(df, "orçamento empenhado")
COL_REALIZADO  = find_col(df, "orçamento realizado")
COL_PCT        = find_col(df, "% realizado")

# ==========================
# DETECÇÃO DE DIMENSÕES (abas)
# ==========================
COL_ACAO_COD   = find_col(df, "código ação") or find_col(df, "codigo ação")
COL_GND_NOME   = find_col(df, "nome grupo de despesa") or find_col(df, "grupo de despesa")
COL_ELEM_NOME  = find_col(df, "nome elemento de despesa") or find_col(df, "elemento de despesa")
COL_FUNCAO_NOME = find_col(df, "nome função") or find_col(df, "funcao")

# ==========================
# FILTROS DINÂMICOS (qualquer coluna)
# ==========================
with st.sidebar:
    all_cols = list(df.columns)
    suggest = [c for c in [
        find_col(df, "código órgão superior"),
        find_col(df, "código órgão subordinado"),
        find_col(df, "código unidade orçamentária"),
        COL_FUNCAO_NOME,
        COL_GND_NOME,
        COL_ELEM_NOME,
        COL_ACAO_COD,
        find_col(df, "nome ação"),
        find_col(df, "nome programa"),
    ] if c is not None]

    filter_cols = st.multiselect(
        "Colunas para filtrar",
        options=all_cols,
        default=list(dict.fromkeys(suggest))[:5],
        key="filter_cols_any",
    )

filtros = {}
for c in filter_cols:
    uniques = df[c].astype(str).fillna("").unique().tolist()
    uniques = [u for u in uniques if u != ""]
    if len(uniques) > 4000:
        st.sidebar.warning(f"'{c}' tem muitos valores ({len(uniques)}). Filtre outra coluna antes.")
        continue
    selecionados = st.sidebar.multiselect(f"{c}", options=sorted(uniques), key=f"ms_{c}")
    if selecionados:
        filtros[c] = selecionados

df_f = filtrar_df(df, filtros)

# ==========================
# VALIDA MÉTRICAS
# ==========================
missing = [name for name, col in [
    ("ORÇAMENTO ATUALIZADO (R$)", COL_ATUALIZADO),
    ("ORÇAMENTO EMPENHADO (R$)", COL_EMPENHADO),
    ("ORÇAMENTO REALIZADO (R$)", COL_REALIZADO),
    ("% REALIZADO DO ORÇAMENTO", COL_PCT),
] if col is None]

if missing:
    st.error(
        "Não consegui localizar automaticamente estas colunas no seu CSV:\n\n- "
        + "\n- ".join(missing)
        + "\n\nAbra a tabela e me diga o nome exato (copiar/colar) para eu ajustar."
    )
    st.stop()

# ==========================
# PREPARA DF DE MÉTRICAS NUMÉRICAS
# ==========================
dfm = df_f.copy()
dfm["_atualizado"] = parse_brl_number_series(dfm[COL_ATUALIZADO]).fillna(0)
dfm["_empenhado"]  = parse_brl_number_series(dfm[COL_EMPENHADO]).fillna(0)
dfm["_realizado"]  = parse_brl_number_series(dfm[COL_REALIZADO]).fillna(0)
dfm["_pct"]        = parse_percent_series(dfm[COL_PCT]).fillna(0)

# KPIs
total_at = float(dfm["_atualizado"].sum())
total_em = float(dfm["_empenhado"].sum())
total_re = float(dfm["_realizado"].sum())
pct_geral = (total_re / total_at * 100) if total_at else 0.0

k1, k2, k3, k4 = st.columns(4)
k1.metric("LOA (R$)", f"{total_at:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
k2.metric("Orçamento Empenhado (R$)",  f"{total_em:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
k3.metric("Orçamento Realizado (R$)",  f"{total_re:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
k4.metric("% Realizado (geral)", f"{pct_geral:.2f}%")

# ==========================
# CONTROLES DE VISUALIZAÇÃO
# ==========================
with st.sidebar:
    st.divider()
    st.header("3) Visualização")

    mostrar_tudo = st.checkbox("Mostrar todas as categorias (pode pesar)", value=False)
    limite_n = st.number_input("Se não mostrar tudo, limitar para N", min_value=5, max_value=2000, value=50, step=5)

    st.divider()
    st.subheader("Métricas no gráfico")

    metric_options = [
        "LOA (R$)",
        "Orçamento Empenhado (R$)",
        "Orçamento Realizado (R$)",
    ]
    metric_map = {
        "LOA (R$)": "atualizado",
        "Orçamento Empenhado (R$)": "empenhado",
        "Orçamento Realizado (R$)": "realizado",
    }

    selected_metrics = st.multiselect(
        "Selecione as métricas (barras)",
        options=metric_options,
        default=metric_options,
    )

    # Opção 2: % desligado por padrão (você disse que ficou melhor)
    show_pct_line = st.checkbox("Mostrar % Realizado", value=False)

    if not selected_metrics:
        st.warning("Selecione pelo menos 1 métrica.")
        selected_metrics = ["Orçamento Realizado (R$)"]

metric_keys = [metric_map[m] for m in selected_metrics]

# ==========================
# GRÁFICO Altair
# ==========================
def chart_budget_and_pct(agg: pd.DataFrame, dim_label: str, y_domain_max: float, metric_keys: list[str], show_pct: bool):
    bars_long = agg.melt(
        id_vars=["dim"],
        value_vars=metric_keys,
        var_name="métrica",
        value_name="valor",
    )

    # legenda amigável
    legend_names = {
        "atualizado": "LOA",
        "empenhado": "Empenhado",
        "realizado": "Realizado",
    }
    bars_long["métrica"] = bars_long["métrica"].map(legend_names).fillna(bars_long["métrica"])

    base = alt.Chart(bars_long).encode(
        x=alt.X("dim:N", sort="-y", title=dim_label),
        tooltip=[
            alt.Tooltip("dim:N", title=dim_label),
            alt.Tooltip("métrica:N", title="Métrica"),
            alt.Tooltip("valor:Q", title="Valor (R$)", format=",.2f"),
        ],
    )

    bars = base.mark_bar().encode(
        y=alt.Y("valor:Q", title="R$ (escala fixa)", scale=alt.Scale(domain=[0, y_domain_max])),
        color=alt.Color("métrica:N", title="Métrica"),
        xOffset="métrica:N",
    )

    if not show_pct:
        return bars.properties(height=380)

    # Se ativar o %: pontos apenas (sem linhas), para não poluir
    points = alt.Chart(agg).mark_point(filled=True, size=60).encode(
        x=alt.X("dim:N", title=dim_label, sort="-y"),
        y=alt.Y("pct:Q", title="% Realizado (0–100)", scale=alt.Scale(domain=[0, 100])),
        tooltip=[
            alt.Tooltip("dim:N", title=dim_label),
            alt.Tooltip("pct:Q", title="% Realizado", format=".2f"),
        ],
    )

    return alt.layer(bars, points).resolve_scale(y="independent").properties(height=380)

# ==========================
# AGREGAÇÃO
# ==========================
def build_agg(dim_col: str) -> pd.DataFrame:
    tmp = dfm[[dim_col]].copy()
    tmp["atualizado"] = dfm["_atualizado"]
    tmp["empenhado"]  = dfm["_empenhado"]
    tmp["realizado"]  = dfm["_realizado"]
    tmp["pct"]        = dfm["_pct"]

    agg = tmp.groupby(dim_col, dropna=False).agg(
        atualizado=("atualizado", "sum"),
        empenhado=("empenhado", "sum"),
        realizado=("realizado", "sum"),
        pct=("pct", "mean"),
    ).reset_index()

    agg = agg.rename(columns={dim_col: "dim"})
    agg["dim"] = agg["dim"].astype(str).replace({"": "(vazio)"})
    agg = agg.sort_values("realizado", ascending=False)

    if not mostrar_tudo:
        agg = agg.head(int(limite_n))

    return agg

def y_max_from_agg(agg: pd.DataFrame, metric_keys: list[str]) -> float:
    cols = [c for c in metric_keys if c in agg.columns]
    if not cols:
        cols = ["realizado"] if "realizado" in agg.columns else list(agg.columns)

    maxv = 1.0
    for c in cols:
        v = agg[c].max()
        if pd.notna(v):
            maxv = max(maxv, float(v))
    return maxv * 1.05

# ==========================
# TABS
# ==========================
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "Visão Geral",
    "Por Ação (código)",
    "Por Grupo de Despesa",
    "Por Elemento de Despesa",
    "Por Função",
    "Tabela & Exportação"
])

with tab1:
    st.subheader("Visão Geral")

    dim_all = [c for c in df.columns if c not in [COL_ATUALIZADO, COL_EMPENHADO, COL_REALIZADO, COL_PCT]]
    default_idx = dim_all.index(COL_ACAO_COD) if COL_ACAO_COD in dim_all else 0
    dim_choice = st.selectbox("Dimensão para análise rápida", options=dim_all, index=default_idx)

    agg_any = build_agg(dim_choice)
    y_max = y_max_from_agg(agg_any,metric_keys)

    st.altair_chart(chart_budget_and_pct(agg_any, dim_choice, y_max, metric_keys, show_pct_line), use_container_width=True)
    st.dataframe(pretty_agg_display(agg_any), use_container_width=True)

with tab2:
    st.subheader("Por Ação Orçamentária (Código Ação)")
    if not COL_ACAO_COD:
        st.warning("Não encontrei a coluna de Código Ação no CSV.")
    else:
        agg_acao = build_agg(COL_ACAO_COD)
        y_max = y_max_from_agg(agg_acao, metric_keys)
        st.altair_chart(chart_budget_and_pct(agg_acao, "Código Ação", y_max, metric_keys, show_pct_line), use_container_width=True)
        st.dataframe(pretty_agg_display(agg_acao), use_container_width=True)

with tab3:
    st.subheader("Por Grupo de Despesa")
    if not COL_GND_NOME:
        st.warning("Não encontrei a coluna de Grupo de Despesa no CSV.")
    else:
        agg_gnd = build_agg(COL_GND_NOME)
        y_max = y_max_from_agg(agg_gnd, metric_keys)
        st.altair_chart(chart_budget_and_pct(agg_gnd, "Grupo de Despesa", y_max, metric_keys, show_pct_line), use_container_width=True)
        st.dataframe(pretty_agg_display(agg_gnd), use_container_width=True)

with tab4:
    st.subheader("Por Elemento de Despesa")
    if not COL_ELEM_NOME:
        st.warning("Não encontrei a coluna de Elemento de Despesa no CSV.")
    else:
        agg_elem = build_agg(COL_ELEM_NOME)
        y_max = y_max_from_agg(agg_elem, metric_keys)
        st.altair_chart(chart_budget_and_pct(agg_elem, "Elemento de Despesa", y_max, metric_keys, show_pct_line), use_container_width=True)
        st.dataframe(pretty_agg_display(agg_elem), use_container_width=True)

with tab5:
    st.subheader("Por Função")
    if not COL_FUNCAO_NOME:
        st.warning("Não encontrei a coluna de Função no CSV.")
    else:
        agg_func = build_agg(COL_FUNCAO_NOME)
        y_max = y_max_from_agg(agg_func, metric_keys)
        st.altair_chart(chart_budget_and_pct(agg_func, "Função", y_max, metric_keys, show_pct_line), use_container_width=True)
        st.dataframe(pretty_agg_display(agg_func), use_container_width=True)

with tab6:
    st.subheader("Tabela (dados filtrados) & Exportação")

    with st.expander("📦 Arquivos encontrados no ZIP"):
        st.write(st.session_state.zip_files or [])

    st.dataframe(df_f, use_container_width=True)

    cexp1, cexp2 = st.columns(2)
    with cexp1:
        st.download_button(
            "Baixar CSV (filtrado)",
            data=df_f.to_csv(index=False).encode("utf-8"),
            file_name=f"orcamento_despesa_{int(st.session_state.ano_carregado)}_filtrado.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with cexp2:
        st.download_button(
            "Baixar Excel (filtrado)",
            data=to_excel_bytes(df_f),
            file_name=f"orcamento_despesa_{int(st.session_state.ano_carregado)}_filtrado.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )

# ==========================
# RODAPÉ
# ==========================
st.markdown("---")
st.caption(
    f"Fonte dos dados: {st.session_state.fonte_url} | CSV utilizado: {st.session_state.csv_name_used} "
    f"| Portal da Transparência — Download de dados (Orçamento/Despesa)"
)
