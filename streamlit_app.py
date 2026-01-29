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
DEFAULT_YEAR = date.today().year

# Nome esperado dentro do zip (pelo seu exemplo)
DEFAULT_CSV_NAME = "ano_OrcamentoDespesa.csv"

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

def extrair_csv_bytes(zip_bytes: bytes, csv_name: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        # tenta achar pelo nome exato; se não existir, tenta achar qualquer .csv
        names = z.namelist()
        if csv_name not in names:
            csvs = [n for n in names if n.lower().endswith(".csv")]
            if not csvs:
                raise RuntimeError(f"Não encontrei CSV no ZIP. Arquivos: {names[:30]}")
            # pega o primeiro csv encontrado
            csv_name = csvs[0]

        with z.open(csv_name) as f:
            return f.read()

def ler_csv(csv_bytes: bytes) -> pd.DataFrame:
    # tentativas comuns (Portal costuma usar ; e latin-1, mas varia)
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

def col_valor_existente(df: pd.DataFrame) -> str | None:
    # tenta encontrar alguma coluna de valor típica
    candidatos = [
        "Orçamento Empenhado (R$)",
        "Orçamento Realizado (R$)",
        "Orçamento Atualizado (R$)",
        "Orçamento Inicial (R$)",
        "valor",
    ]
    for c in candidatos:
        if c in df.columns:
            return c
    return None

# ==========================
# UI
# ==========================
st.title("📥 Orçamento/Despesa — Download de Dados (ZIP → CSV)")
st.caption("Sem API: o app baixa o arquivo do Portal, extrai o CSV e te dá filtros e gráficos.")

with st.sidebar:
    st.header("Ano e download")
    ano = st.number_input("Ano", min_value=2011, max_value=2100, value=DEFAULT_YEAR, step=1)

    # URL da página do ano (como você passou)
    fonte_url = f"{BASE_PAGE}/{int(ano)}"

    # Nome do CSV dentro do ZIP (você disse que é esse)
    csv_name = st.text_input("Nome do CSV dentro do ZIP", value=DEFAULT_CSV_NAME)

    carregar = st.button("⬇️ Baixar e carregar dados", use_container_width=True)

if not carregar:
    st.info("Escolha o ano e clique em **Baixar e carregar dados**.")
    st.stop()

with st.spinner("Baixando ZIP do Portal…"):
    zip_bytes = baixar_zip(fonte_url)

files_in_zip = listar_arquivos_zip(zip_bytes)
st.success("ZIP baixado com sucesso.")
with st.expander("📦 Arquivos encontrados no ZIP"):
    st.write(files_in_zip)

with st.spinner("Extraindo CSV…"):
    csv_bytes = extrair_csv_bytes(zip_bytes, csv_name)

with st.spinner("Lendo CSV…"):
    df = ler_csv(csv_bytes)

st.success(f"Dados carregados: **{len(df):,}** linhas × **{len(df.columns)}** colunas".replace(",", "."))

# ==========================
# FILTROS DINÂMICOS
# ==========================
st.subheader("🎛 Filtros dinâmicos (na própria tela)")

# escolhe até 4 colunas para filtrar (simples e prático)
cols = list(df.columns)
default_filter_cols = [c for c in [
    "Código Unidade Orçamentária  ",
    "Nome Unidade Orçamentária  ",
    "Código Ação",
    "Nome Ação",
] if c in cols]

filter_cols = st.multiselect(
    "Escolha colunas para filtrar (opcional)",
    options=cols,
    default=default_filter_cols[:4]
)

filtros = {}
for c in filter_cols:
    # pega top valores para não explodir a UI; se for muita coisa, o usuário digita busca
    uniques = df[c].astype(str).fillna("").unique().tolist()
    uniques = [u for u in uniques if u != ""]
    # se for gigante, limita e avisa
    if len(uniques) > 2000:
        st.warning(f"Coluna '{c}' tem muitos valores ({len(uniques)}). Use busca no DataFrame ou selecione outra coluna.")
        continue

    selecionados = st.multiselect(f"Filtro: {c}", options=sorted(uniques)[:2000])
    if selecionados:
        filtros[c] = selecionados

df_f = filtrar_df(df, filtros)

st.write(f"Linhas após filtros: **{len(df_f):,}**".replace(",", "."))

# ==========================
# GRÁFICOS
# ==========================
st.subheader("📊 Gráficos")

col_val = col_valor_existente(df_f)

# escolha de agrupamento
group_col = st.selectbox(
    "Agrupar por (para o gráfico)",
    options=[c for c in [
        "Código Ação", "Nome Ação",
        "Código Unidade Orçamentária  ", "Nome Unidade Orçamentária  ",
        "Nome Órgão Superior", "Nome Órgão Subordinado",
        "Nome Função", "Nome Subfunção",
        "Nome Grupo de Despesa", "Nome Elemento de Despesa"
    ] if c in df_f.columns] or list(df_f.columns)[:1]
)

if col_val and group_col in df_f.columns:
    # tenta converter valores com vírgula/ponto
    s = df_f[col_val].astype(str).str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    df_f["_valor_num"] = pd.to_numeric(s, errors="coerce").fillna(0)

    top_n = st.slider("Top N no gráfico", 5, 50, 15)
    agg = (
        df_f.groupby(group_col, dropna=False)["_valor_num"]
        .sum()
        .reset_index()
        .sort_values("_valor_num", ascending=False)
        .head(top_n)
    )
    st.bar_chart(agg.set_index(group_col)["_valor_num"])
    st.dataframe(agg, use_container_width=True, hide_index=True)
else:
    st.info("Não encontrei uma coluna de valor padrão para somar. Você ainda pode explorar a tabela abaixo.")

# ==========================
# TABELA + DOWNLOAD
# ==========================
st.subheader("📋 Tabela")
st.dataframe(df_f, use_container_width=True)

st.subheader("⬇️ Exportar")
st.download_button(
    "Baixar CSV (filtrado)",
    data=df_f.to_csv(index=False).encode("utf-8"),
    file_name=f"orcamento_despesa_{int(ano)}_filtrado.csv",
    mime="text/csv",
)

# ==========================
# RODAPÉ (FONTE)
# ==========================
st.markdown("---")
st.caption(f"Fonte dos dados: {fonte_url}")
