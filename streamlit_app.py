import streamlit as st
import requests
import zipfile
import io
import pandas as pd

# ==========================
# CONFIG
# ==========================
st.set_page_config(page_title="Orçamento/Despesa — Download de Dados", layout="wide")

BASE_PAGE = "https://portaldatransparencia.gov.br/download-de-dados/orcamento-despesa"
DEFAULT_YEAR = 2026  # ajuste se quiser

# ==========================
# FUNÇÕES
# ==========================
@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)
def baixar_zip(url: str) -> bytes:
    headers = {
        "User-Agent": "Mozilla/5.0 (StreamlitCloud)",
        "Accept": "*/*",
        "Referer": "https://portaldatransparencia.gov.br/",
    }
    r = requests.get(url, headers=headers, timeout=180)
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

def filtrar_df(df: pd.DataFrame, filtros: dict) -> pd.DataFrame:
    out = df
    for col, vals in filtros.items():
        if vals and col in out.columns:
            out = out[out[col].astype(str).isin([str(v) for v in vals])]
    return out

def detectar_colunas_orcamento(df: pd.DataFrame) -> list[str]:
    cols = []
    for c in df.columns:
        c_strip = str(c).strip()
        if c_strip.lower().startswith("orçamento"):
            cols.append(c)
    return cols

def detectar_colunas_percentuais(df: pd.DataFrame) -> list[str]:
    cols = []
    for c in df.columns:
        c_strip = str(c).strip()
        if c_strip.startswith("%"):
            cols.append(c)
    return cols

def escolher_top3_orcamento(cols_orc: list[str]) -> list[str]:
    norm = {c: str(c).strip().lower() for c in cols_orc}
    preferencias = [
        "orçamento atualizado",
        "orçamento empenhado",
        "orçamento realizado",
        "orçamento inicial",
    ]
    escolhidas = []
    for pref in preferencias:
        for c, n in norm.items():
            if pref in n and c not in escolhidas:
                escolhidas.append(c)
                break
    for c in cols_orc:
        if c not in escolhidas:
            escolhidas.append(c)
        if len(escolhidas) >= 3:
            break
    return escolhidas[:3]

def parse_brl_number_series(s: pd.Series) -> pd.Series:
    x = (
        s.astype(str)
        .str.replace("\xa0", "", regex=False)
        .str.replace("R$", "", regex=False)
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
        .str.strip()
    )
    return pd.to_numeric(x, errors="coerce")

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
# UI
# ==========================
st.title("📥 Orçamento/Despesa — Download de Dados (ZIP → CSV)")
st.caption("Painel exploratório: filtros + escolha de dimensões + gráficos atualizando automaticamente.")

with st.sidebar:
    st.header("Ano e carga dos dados")

    ano = st.number_input(
        "Ano",
        min_value=2011,
        max_value=2100,
        value=int(st.session_state.ano_carregado) if st.session_state.ano_carregado else DEFAULT_YEAR,
        step=1
    )

    fonte_url = f"{BASE_PAGE}/{int(ano)}"
    csv_name_expected = f"{int(ano)}_OrcamentoDespesa.csv"

    st.caption("Fonte (ano selecionado):")
    st.write(fonte_url)
    st.caption("CSV esperado dentro do ZIP:")
    st.code(csv_name_expected)

    c1, c2 = st.columns(2)
    with c1:
        carregar = st.button("⬇️ Carregar", use_container_width=True)
    with c2:
        limpar = st.button("🧹 Limpar", use_container_width=True)

    st.divider()
    st.subheader("Exibição do gráfico")
    mostrar_tudo = st.checkbox("Mostrar todas as categorias (pode ficar pesado)", value=False)
    limite_n = st.number_input(
        "Se não mostrar tudo, limitar para N categorias (explícito)",
        min_value=5,
        max_value=500,
        value=50,
        step=5
    )

if limpar:
    st.session_state.df = None
    st.session_state.ano_carregado = None
    st.session_state.fonte_url = None
    st.session_state.zip_files = None
    st.session_state.csv_name_used = None
    st.rerun()

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

        st.success(
            f"✅ Carregado: {len(df):,} linhas × {len(df.columns)} colunas | CSV: {chosen_name}".replace(",", ".")
        )

    except Exception as e:
        st.error("Erro ao carregar dados.")
        st.exception(e)

if st.session_state.df is None:
    st.info("Escolha o ano e clique em **Carregar**.")
    st.stop()

df = st.session_state.df

# ==========================
# INFO DO ZIP
# ==========================
with st.expander("📦 Arquivos encontrados no ZIP"):
    st.write(st.session_state.zip_files or [])

# ==========================
# FILTROS DINÂMICOS
# ==========================
st.subheader("🎛 Filtros (selecione colunas e valores)")

all_cols = list(df.columns)
suggest = [c for c in [
    "Código Órgão Superior",
    "Nome Órgão Superior",
    "Código Órgão Subordinado",
    "Nome Órgão Subordinado",
    "Código Unidade Orçamentária  ",
    "Nome Unidade Orçamentária  ",
    "Código Ação",
    "Nome Ação",
] if c in all_cols]

filter_cols = st.multiselect(
    "Quais colunas você quer usar como filtro?",
    options=all_cols,
    default=suggest[:4],
    key="filter_cols_any"
)

filtros = {}
for c in filter_cols:
    uniques = df[c].astype(str).fillna("").unique().tolist()
    uniques = [u for u in uniques if u != ""]
    if len(uniques) > 3000:
        st.warning(f"Coluna '{c}' tem muitos valores ({len(uniques)}). Filtre por outra coluna antes.")
        continue
    selecionados = st.multiselect(f"Filtro: {c}", options=sorted(uniques), key=f"ms_{c}")
    if selecionados:
        filtros[c] = selecionados

df_f = filtrar_df(df, filtros)

k1, k2 = st.columns(2)
with k1:
    st.metric("Linhas (após filtros)", f"{len(df_f):,}".replace(",", "."))
with k2:
    st.metric("Ano carregado", str(st.session_state.ano_carregado))

# ==========================
# AGRUPAMENTO (QUALQUER COLUNA)
# ==========================
st.subheader("📌 Dimensão (Agrupar por)")

orc_cols = detectar_colunas_orcamento(df_f)
pct_cols = detectar_colunas_percentuais(df_f)

# todas as colunas (exceto métricas) como dimensão
dim_options = [c for c in df_f.columns if c not in orc_cols and c not in pct_cols]
if not dim_options:
    dim_options = list(df_f.columns)

group_col = st.selectbox("Escolha a dimensão", options=dim_options, key="group_any")

# ==========================
# GRÁFICOS
# ==========================
st.subheader("📊 Gráficos (atualizam conforme filtros e dimensão)")

# ---- 3 gráficos de orçamento (sempre)
if not orc_cols:
    st.warning("Não encontrei colunas que comecem com 'Orçamento' neste arquivo.")
else:
    top3 = escolher_top3_orcamento(orc_cols)

    g1, g2, g3 = st.columns(3)

    for ax, col_val in zip([g1, g2, g3], top3):
        with ax:
            s_num = parse_brl_number_series(df_f[col_val]).fillna(0)

            tmp = df_f[[group_col]].copy()
            tmp["_valor"] = s_num

            agg = (
                tmp.groupby(group_col, dropna=False)["_valor"]
                .sum()
                .reset_index()
                .sort_values("_valor", ascending=False)
            )

            if not mostrar_tudo:
                agg = agg.head(int(limite_n))

            st.caption(f"**{str(col_val).strip()}** (soma)")
            if len(agg) == 0:
                st.info("Sem dados para o gráfico.")
            else:
                st.bar_chart(agg.set_index(group_col)["_valor"], height=320)
                st.dataframe(agg, use_container_width=True, hide_index=True)

# ---- Percentuais: todas as colunas % em um gráfico (selecionável) + tabela
st.subheader("📈 Percentuais (%)")

if not pct_cols:
    st.info("Não encontrei colunas que começam com '%'.")
else:
    # o usuário escolhe QUAL % quer ver, mas todas ficam disponíveis
    pct_col = st.selectbox("Escolha qual percentual (%) visualizar", options=pct_cols, key="pct_col")

    pct_num = parse_brl_number_series(df_f[pct_col]).fillna(0)

    tmp = df_f[[group_col]].copy()
    tmp["_pct"] = pct_num

    agg_pct = (
        tmp.groupby(group_col, dropna=False)["_pct"]
        .mean()
        .reset_index()
        .sort_values("_pct", ascending=False)
    )

    if not mostrar_tudo:
        agg_pct = agg_pct.head(int(limite_n))

    st.caption(f"**{str(pct_col).strip()}** (média por grupo)")
    if len(agg_pct) == 0:
        st.info("Sem dados para o gráfico.")
    else:
        st.bar_chart(agg_pct.set_index(group_col)["_pct"], height=320)
        st.dataframe(agg_pct, use_container_width=True, hide_index=True)

# ==========================
# TABELA + DOWNLOAD
# ==========================
st.subheader("📋 Tabela (após filtros)")
st.dataframe(df_f, use_container_width=True)

st.subheader("⬇️ Exportar")
st.download_button(
    "Baixar CSV (filtrado)",
    data=df_f.to_csv(index=False).encode("utf-8"),
    file_name=f"orcamento_despesa_{int(st.session_state.ano_carregado)}_filtrado.csv",
    mime="text/csv",
)
st.download_button(
    "Baixar Excel (filtrado)",
    data=to_excel_bytes(df_f),
    file_name=f"orcamento_despesa_{int(st.session_state.ano_carregado)}_filtrado.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

# ==========================
# RODAPÉ (FONTE)
# ==========================
st.markdown("---")
st.caption(f"Fonte dos dados: {st.session_state.fonte_url} | CSV utilizado: {st.session_state.csv_name_used}")
