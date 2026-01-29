import streamlit as st
import requests
import zipfile
import io
import pandas as pd
from datetime import date

# ==========================
# CONFIG
# ==========================
st.set_page_config(page_title="Orçamento/Despesa — Download de Dados", layout="wide")

BASE_PAGE = "https://portaldatransparencia.gov.br/download-de-dados/orcamento-despesa"
DEFAULT_YEAR = 2026  # você pode trocar para date.today().year se quiser

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
    """
    Extrai o CSV pelo nome exato; se não achar, usa o primeiro .csv.
    Retorna (csv_bytes, nome_usado).
    """
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

def detectar_col_valor(df: pd.DataFrame) -> str | None:
    candidatos = [
        "Orçamento Empenhado (R$)",
        "Orçamento Realizado (R$)",
        "Orçamento Atualizado (R$)",
        "Orçamento Inicial (R$)",
    ]
    for c in candidatos:
        if c in df.columns:
            return c
    return None

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
st.caption("Sem API: o app baixa o ZIP do Portal, extrai o CSV do ano e permite filtros e gráficos na tela.")

with st.sidebar:
    st.header("Ano e carga dos dados")

    # Mantém o ano “lembrado” na UI
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

    col1, col2 = st.columns(2)
    with col1:
        carregar = st.button("⬇️ Carregar", use_container_width=True)
    with col2:
        limpar = st.button("🧹 Limpar", use_container_width=True)

    st.divider()
    st.caption("Dica: depois de carregar uma vez, você pode mexer nos filtros à vontade sem recarregar.")

if limpar:
    st.session_state.df = None
    st.session_state.ano_carregado = None
    st.session_state.fonte_url = None
    st.session_state.zip_files = None
    st.session_state.csv_name_used = None
    st.rerun()

# Carrega se clicou OU se ainda não tem df (primeira vez) — opcional
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

# Se ainda não carregou nada, não trava a UI — só orienta
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
st.subheader("🎛 Filtros dinâmicos")

cols = list(df.columns)

# Sugestão de colunas comuns (se existirem)
suggest = [c for c in [
    "Código Unidade Orçamentária  ",
    "Nome Unidade Orçamentária  ",
    "Código Ação",
    "Nome Ação",
    "Nome Órgão Superior",
    "Nome Órgão Subordinado",
] if c in cols]

filter_cols = st.multiselect(
    "Escolha colunas para filtrar (opcional)",
    options=cols,
    default=suggest[:4],
    key="filter_cols"
)

filtros = {}
for c in filter_cols:
    uniques = df[c].astype(str).fillna("").unique().tolist()
    uniques = [u for u in uniques if u != ""]

    # evita travar UI com milhões de valores
    if len(uniques) > 3000:
        st.warning(f"Coluna '{c}' tem muitos valores ({len(uniques)}). Selecione outra coluna para filtro.")
        continue

    selecionados = st.multiselect(f"Filtro: {c}", options=sorted(uniques), key=f"ms_{c}")
    if selecionados:
        filtros[c] = selecionados

df_f = filtrar_df(df, filtros)
st.write(f"Linhas após filtros: **{len(df_f):,}**".replace(",", "."))

# ==========================
# GRÁFICOS
# ==========================
st.subheader("📊 Gráfico por agrupamento")

col_val = detectar_col_valor(df_f)

group_options = [c for c in [
    "Código Ação", "Nome Ação",
    "Código Unidade Orçamentária  ", "Nome Unidade Orçamentária  ",
    "Nome Órgão Superior", "Nome Órgão Subordinado",
    "Nome Função", "Nome Subfunção",
    "Nome Grupo de Despesa", "Nome Elemento de Despesa"
] if c in df_f.columns]

if not group_options:
    group_options = list(df_f.columns)[:1]

group_col = st.selectbox("Agrupar por", options=group_options, key="group_col")

if col_val and group_col in df_f.columns:
    # converte o valor para número (tolerante a formatação BR)
    s = df_f[col_val].astype(str).str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    df_plot = df_f.copy()
    df_plot["_valor_num"] = pd.to_numeric(s, errors="coerce").fillna(0)

    top_n = st.slider("Top N", 5, 50, 15, key="top_n")
    agg = (
        df_plot.groupby(group_col, dropna=False)["_valor_num"]
        .sum()
        .reset_index()
        .sort_values("_valor_num", ascending=False)
        .head(top_n)
    )
    st.bar_chart(agg.set_index(group_col)["_valor_num"])
    st.dataframe(agg, use_container_width=True, hide_index=True)
else:
    st.info("Não encontrei coluna de valor padrão para somar. Você ainda pode explorar a tabela abaixo.")

# ==========================
# TABELA + DOWNLOAD
# ==========================
st.subheader("📋 Tabela")
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
