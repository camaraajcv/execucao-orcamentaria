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

def extrair_csv_bytes(zip_bytes: bytes, csv_name: str) -> tuple[bytes, str]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        names = z.namelist()
        if csv_name in names:
            chosen = csv_name
        else:
            csvs = [n for n in names if n.lower().endswith(".csv")]
            if not csvs:
                raise RuntimeError(f"Não encontrei CSV no ZIP. Arquivos: {names[:30]}")
            chosen = csvs[0]
        with z.open(chosen) as f:
            return f.read(), chosen

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
    # tolerante: remove NBSP, R$, espaços, separador de milhar e converte vírgula decimal
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
    # aceita "12,3", "12.3", "12,3%" etc.
    x = (
        s.astype(str)
        .str.replace("\xa0", "", regex=False)
        .str.replace("%", "", regex=False)
        .str.replace(" ", "", regex=False)
        .str.replace(".", "", regex=False)   # em alguns CSVs o ponto pode ser milhar; se for decimal, geralmente vem vírgula
        .str.replace(",", ".", regex=False)
        .str.strip()
    )
    out = pd.to_numeric(x, errors="coerce")
    # se vier 0-1, converte para 0-100 (raro, mas já vi)
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
# STATE
# ==========================
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
st.caption("Dashboard interativo (download de dados → filtros → gráficos comparáveis com escala fixa).")

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
            csv_bytes, chosen_name = extrair_csv_bytes(zip_bytes, csv_name_expected)

        with st.spinner("Lendo CSV…"):
            df = ler_csv(csv_bytes)

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
# DETECÇÃO DE COLUNAS IMPORTANTES (nomes exatos do seu arquivo)
# ==========================
COL_ATUALIZADO = find_col(df, "orçamento atualizado")
COL_EMPENHADO  = find_col(df, "orçamento empenhado")
COL_REALIZADO  = find_col(df, "orçamento realizado")
COL_PCT        = find_col(df, "% realizado")

# dimensões sugeridas (mas vamos permitir qualquer)
COL_ACAO_COD   = find_col(df, "código ação") or find_col(df, "codigo ação")
COL_GND_NOME   = find_col(df, "nome grupo de despesa") or find_col(df, "grupo de despesa")

# ==========================
# FILTROS DINÂMICOS (qualquer coluna)
# ==========================
with st.sidebar:
    all_cols = list(df.columns)

    # sugestões comuns (você pode remover/ajustar)
    suggest = [c for c in [
        find_col(df, "código órgão superior"),
        find_col(df, "código órgão subordinado"),
        find_col(df, "código unidade orçamentária"),
        find_col(df, "código ação"),
        find_col(df, "nome ação"),
        find_col(df, "nome programa"),
        find_col(df, "nome função"),
        find_col(df, "nome subfunção"),
    ] if c is not None]

    filter_cols = st.multiselect(
        "Colunas para filtrar",
        options=all_cols,
        default=list(dict.fromkeys(suggest))[:4],
        key="filter_cols_any",
    )

filtros = {}
for c in filter_cols:
    uniques = df[c].astype(str).fillna("").unique().tolist()
    uniques = [u for u in uniques if u != ""]
    # proteção UI
    if len(uniques) > 4000:
        st.sidebar.warning(f"'{c}' tem muitos valores ({len(uniques)}). Filtre outra coluna antes.")
        continue
    selecionados = st.sidebar.multiselect(f"{c}", options=sorted(uniques), key=f"ms_{c}")
    if selecionados:
        filtros[c] = selecionados

df_f = filtrar_df(df, filtros)

# ==========================
# PREPARA MÉTRICAS NUMÉRICAS (sempre as 4 que você pediu)
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
        + "\n\nAbra a tabela e me diga o nome exato (copiar/colar) para eu ajustar o mapeamento."
    )
    st.stop()

dfm = df_f.copy()
dfm["_atualizado"] = parse_brl_number_series(dfm[COL_ATUALIZADO]).fillna(0)
dfm["_empenhado"]  = parse_brl_number_series(dfm[COL_EMPENHADO]).fillna(0)
dfm["_realizado"]  = parse_brl_number_series(dfm[COL_REALIZADO]).fillna(0)
dfm["_pct"]        = parse_percent_series(dfm[COL_PCT]).fillna(0)

# KPIs gerais (mais “painel”)
total_at = float(dfm["_atualizado"].sum())
total_em = float(dfm["_empenhado"].sum())
total_re = float(dfm["_realizado"].sum())
pct_geral = (total_re / total_at * 100) if total_at else 0.0

k1, k2, k3, k4 = st.columns(4)
k1.metric("Orçamento Atualizado (R$)", f"{total_at:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
k2.metric("Orçamento Empenhado (R$)",  f"{total_em:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
k3.metric("Orçamento Realizado (R$)",  f"{total_re:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
k4.metric("% Realizado (geral)", f"{pct_geral:.2f}%")

# ==========================
# CONTROLES DE VISUALIZAÇÃO (sem Top N invisível)
# ==========================
with st.sidebar:
    st.divider()
    st.header("3) Visualização")
    mostrar_tudo = st.checkbox("Mostrar todas as categorias (pode pesar)", value=False)
    limite_n = st.number_input("Se não mostrar tudo, limitar para N", min_value=5, max_value=1000, value=50, step=5)

# ==========================
# FUNÇÃO: gráfico profissional com escala fixa e % (eixo secundário)
# ==========================
def chart_budget_and_pct(agg: pd.DataFrame, dim_label: str, y_domain_max: float):
    """
    agg precisa ter colunas: dim, atualizado, empenhado, realizado, pct
    """
    # dados long para barras (3 orçamentos)
    bars_long = agg.melt(
        id_vars=["dim"],
        value_vars=["atualizado", "empenhado", "realizado"],
        var_name="métrica",
        value_name="valor",
    )

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

    # linha de percentual (eixo secundário fixo 0-100)
    line = alt.Chart(agg).mark_line(point=True).encode(
        x=alt.X("dim:N", title=dim_label, sort="-y"),
        y=alt.Y("pct:Q", title="% Realizado (0–100)", scale=alt.Scale(domain=[0, 100])),
        tooltip=[
            alt.Tooltip("dim:N", title=dim_label),
            alt.Tooltip("pct:Q", title="% Realizado", format=".2f"),
        ],
    )

    # camada com eixos independentes
    layered = alt.layer(bars, line).resolve_scale(y="independent").properties(height=380)

    return layered

# ==========================
# AGREGAÇÕES E TABS (painel “mais profissional”)
# ==========================
tab1, tab2, tab3, tab4 = st.tabs(["Visão Geral", "Por Ação (código)", "Por Grupo de Despesa", "Tabela & Exportação"])

def build_agg(dim_col: str, dim_label: str) -> pd.DataFrame:
    tmp = dfm[[dim_col]].copy()
    tmp["atualizado"] = dfm["_atualizado"]
    tmp["empenhado"]  = dfm["_empenhado"]
    tmp["realizado"]  = dfm["_realizado"]
    tmp["pct"]        = dfm["_pct"]

    agg = tmp.groupby(dim_col, dropna=False).agg(
        atualizado=("atualizado", "sum"),
        empenhado=("empenhado", "sum"),
        realizado=("realizado", "sum"),
        pct=("pct", "mean"),  # percentuais: média faz mais sentido do que soma
    ).reset_index()

    agg = agg.rename(columns={dim_col: "dim"})

    # ordena pelo realizado (boa leitura)
    agg = agg.sort_values("realizado", ascending=False)

    if not mostrar_tudo:
        agg = agg.head(int(limite_n))

    # remove dimensões vazias
    agg["dim"] = agg["dim"].astype(str).replace({"": "(vazio)"})
    return agg

# domínio Y FIXO: usa o máximo global dos 3 orçamentos após filtros (não por gráfico)
ymax_global = float(
    max(
        dfm["_atualizado"].max(skipna=True),
        dfm["_empenhado"].max(skipna=True),
        dfm["_realizado"].max(skipna=True),
        1.0,
    )
)
# para agregar, o máximo pode aumentar (soma). Então usamos soma total por dim: máximo entre colunas agregadas
# definimos depois de montar os aggs, mas já deixamos uma base
y_domain_default = max(1.0, dfm["_atualizado"].sum(), dfm["_empenhado"].sum(), dfm["_realizado"].sum()) if mostrar_tudo else 1.0

with tab1:
    st.subheader("Visão Geral")
    st.caption("Aqui você pode comparar rapidamente os totais e navegar para as análises por Ação e por Grupo de Despesa.")

    # Um gráfico “top” por dimensão escolhida (livre), para explorar sem precisar ir em tabs
    dim_all = [c for c in df.columns if c not in [COL_ATUALIZADO, COL_EMPENHADO, COL_REALIZADO, COL_PCT]]
    dim_choice = st.selectbox("Dimensão para análise rápida", options=dim_all, index=dim_all.index(COL_ACAO_COD) if COL_ACAO_COD in dim_all else 0)

    agg_any = build_agg(dim_choice, dim_choice)

    # y fixo baseado no máximo agregado (tornando comparável dentro do gráfico)
    y_max = float(max(agg_any["atualizado"].max(), agg_any["empenhado"].max(), agg_any["realizado"].max(), 1.0)) * 1.05

    st.altair_chart(chart_budget_and_pct(agg_any, dim_choice, y_max), use_container_width=True)
    st.dataframe(agg_any, use_container_width=True, hide_index=True)

with tab2:
    st.subheader("Por Ação Orçamentária (Código Ação)")
    if not COL_ACAO_COD:
        st.warning("Não encontrei a coluna de Código Ação no CSV.")
    else:
        agg_acao = build_agg(COL_ACAO_COD, "Código Ação")
        y_max = float(max(agg_acao["atualizado"].max(), agg_acao["empenhado"].max(), agg_acao["realizado"].max(), 1.0)) * 1.05

        st.altair_chart(chart_budget_and_pct(agg_acao, "Código Ação", y_max), use_container_width=True)
        st.dataframe(agg_acao, use_container_width=True, hide_index=True)

with tab3:
    st.subheader("Por Grupo de Despesa (Nome Grupo de Despesa)")
    if not COL_GND_NOME:
        st.warning("Não encontrei a coluna de Nome Grupo de Despesa no CSV.")
    else:
        agg_gnd = build_agg(COL_GND_NOME, "Grupo de Despesa")
        y_max = float(max(agg_gnd["atualizado"].max(), agg_gnd["empenhado"].max(), agg_gnd["realizado"].max(), 1.0)) * 1.05

        st.altair_chart(chart_budget_and_pct(agg_gnd, "Grupo de Despesa", y_max), use_container_width=True)
        st.dataframe(agg_gnd, use_container_width=True, hide_index=True)

with tab4:
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
# RODAPÉ (FONTE)
# ==========================
st.markdown("---")
st.caption(
    f"Fonte dos dados: {st.session_state.fonte_url} | CSV utilizado: {st.session_state.csv_name_used} "
    f"| Portal da Transparência — Download de dados (Orçamento/Despesa)"
)
