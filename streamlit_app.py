import streamlit as st
import requests
import pandas as pd
from datetime import date, timedelta

# ======================================================
# CONFIGURAÇÃO GERAL
# ======================================================
st.set_page_config(
    page_title="Empenhos – UG 120052",
    layout="wide"
)

BASE_URL = "https://www.portaldatransparencia.gov.br/api-de-dados"
ENDPOINT = "despesa-empenho"
UG_PADRAO = "120052"

# ======================================================
# SECRETS
# ======================================================
if "PORTAL_TRANSPARENCIA_TOKEN" not in st.secrets:
    st.error("❌ Token do Portal da Transparência não configurado nos Secrets.")
    st.stop()

TOKEN = st.secrets["PORTAL_TRANSPARENCIA_TOKEN"]
HEADER_NAME = st.secrets.get("PORTAL_TRANSPARENCIA_HEADER", "chave-api-dados")

HEADERS = {
    HEADER_NAME: TOKEN,
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Streamlit App)",
    "Referer": "https://www.portaldatransparencia.gov.br/",
}

# ======================================================
# FUNÇÕES
# ======================================================
def consultar_empenhos(params):
    registros = []
    pagina = 1

    while True:
        params.update({
            "pagina": pagina,
            "tamanhoPagina": 500
        })

        r = requests.get(
            f"{BASE_URL}/{ENDPOINT}",
            headers=HEADERS,
            params=params,
            timeout=30
        )

        if r.status_code != 200:
            st.error(f"Erro HTTP {r.status_code}")
            st.code(r.text)
            st.stop()

        dados = r.json()
        if not dados:
            break

        registros.extend(dados)
        pagina += 1

    return registros


def normalizar_dados(lista):
    if not lista:
        return pd.DataFrame()

    df = pd.json_normalize(lista)

    renomear = {
        "dataEmissao": "Data",
        "numeroEmpenho": "Empenho",
        "valorEmpenho": "Valor",
        "acao.codigo": "Acao",
        "acao.nome": "Acao_nome",
        "favorecido.nome": "Favorecido",
        "favorecido.cpfCnpj": "CPF_CNPJ"
    }

    df = df.rename(columns=renomear)

    if "Valor" in df.columns:
        df["Valor"] = pd.to_numeric(df["Valor"], errors="coerce")

    return df


# ======================================================
# INTERFACE
# ======================================================
st.title("📌 Execução Orçamentária – Empenhos")
st.subheader("UG Executora 120052")
st.caption("Fonte: Portal da Transparência")

with st.sidebar:
    st.header("Filtros")

    ug = st.text_input("UG Executora", UG_PADRAO)

    hoje = date.today()
    data_ini = st.date_input("Data inicial", hoje - timedelta(days=30))
    data_fim = st.date_input("Data final", hoje)

    acao = st.text_input("Ação Orçamentária (opcional)")
    favorecido = st.text_input("Nome do Favorecido (opcional)")

    consultar = st.button("🔍 Consultar")

if not consultar:
    st.info("Selecione os filtros e clique em **Consultar**.")
    st.stop()

# ======================================================
# CONSULTA
# ======================================================
params = {
    "codigoUGExecutora": ug,
    "dataInicio": data_ini.isoformat(),
    "dataFim": data_fim.isoformat()
}

if acao:
    params["codigoAcao"] = acao

if favorecido:
    params["nomeFavorecido"] = favorecido

with st.spinner("Consultando dados no Portal da Transparência..."):
    dados = consultar_empenhos(params)

df = normalizar_dados(dados)

# ======================================================
# RESULTADOS
# ======================================================
st.metric("Quantidade de Empenhos", len(df))

if not df.empty:
    total = df["Valor"].sum()
    st.metric("Valor Total Empenhado (R$)", f"{total:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

st.divider()

st.subheader("📋 Detalhamento dos Empenhos")
st.dataframe(df, use_container_width=True)

# ======================================================
# GRÁFICOS
# ======================================================
if "Acao_nome" in df.columns and not df.empty:
    st.subheader("📊 Empenhos por Ação Orçamentária")

    graf = (
        df.groupby("Acao_nome", dropna=False)["Valor"]
        .sum()
        .sort_values(ascending=False)
    )

    st.bar_chart(graf)

# ======================================================
# DOWNLOAD
# ======================================================
st.subheader("⬇️ Exportar dados")
csv = df.to_csv(index=False).encode("utf-8")
st.download_button(
    "Baixar CSV",
    csv,
    "empenhos_ug_120052.csv",
    "text/csv"
)

# ======================================================
# DIAGNÓSTICO
# ======================================================
with st.expander("🛠 Diagnóstico da API"):
    st.write("Base URL:", BASE_URL)
    st.write("Header:", HEADER_NAME)
    st.write("Token length:", len(TOKEN))
    st.write("Total registros retornados:", len(dados))
