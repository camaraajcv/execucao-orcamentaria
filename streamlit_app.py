import streamlit as st
import requests
import pandas as pd
import time
import random
import io

from datetime import date, timedelta
from requests.exceptions import ReadTimeout, ConnectTimeout, ConnectionError, HTTPError

# ======================================================
# CONFIGURAÇÃO GERAL
# ======================================================
st.set_page_config(
    page_title="Empenhos – UG 120052",
    layout="wide"
)

BASE_URL = "https://api.portaldatransparencia.gov.br/api-de-dados"
ENDPOINT = "despesas/documentos"

UG_PADRAO = "120052"
GESTAO_PADRAO = "0001"
FASE_EMPENHO = 1

DEFAULT_TIMEOUT = 120  # segundos

# ======================================================
# SECRETS
# ======================================================
if "PORTAL_TRANSPARENCIA_TOKEN" not in st.secrets:
    st.error("❌ Configure PORTAL_TRANSPARENCIA_TOKEN em Settings → Secrets")
    st.stop()

TOKEN = str(st.secrets["PORTAL_TRANSPARENCIA_TOKEN"]).strip()
HEADER_NAME = str(
    st.secrets.get("PORTAL_TRANSPARENCIA_HEADER", "chave-api-dados")
).strip()

HEADERS = {
    HEADER_NAME: TOKEN,
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (StreamlitCloud)",
    "Referer": "https://www.portaldatransparencia.gov.br/",
}

# ======================================================
# FUNÇÕES DE REDE (ROBUSTAS)
# ======================================================
def request_with_retry(url, params, max_retries=6, base_sleep=1.0):
    last_err = None

    for attempt in range(1, max_retries + 1):
        try:
            r = requests.get(
                url,
                headers=HEADERS,
                params=params,
                timeout=DEFAULT_TIMEOUT
            )

            if r.status_code in (429, 500, 502, 503, 504):
                raise HTTPError(f"HTTP {r.status_code}", response=r)

            r.raise_for_status()
            return r

        except (ReadTimeout, ConnectTimeout, ConnectionError, HTTPError) as e:
            last_err = e
            sleep_s = base_sleep * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
            time.sleep(min(sleep_s, 12))

    raise RuntimeError(f"Falha após retries. Último erro: {last_err}")


def fetch_day(url, unidade_gestora, gestao, day, page_size=200, max_pages=20):
    day_br = day.strftime("%d/%m/%Y")
    items = []

    for page in range(1, max_pages + 1):
        params = {
            "unidadeGestora": unidade_gestora,
            "gestao": gestao,
            "dataEmissao": day_br,
            "fase": FASE_EMPENHO,
            "pagina": page,
            "tamanhoPagina": page_size,
        }

        r = request_with_retry(url, params)
        data = r.json()

        if not data:
            break

        items.extend(data)

        if len(data) < page_size:
            break

    return items

# ======================================================
# UTILIDADES
# ======================================================
def normalize(items):
    if not items:
        return pd.DataFrame()

    df = pd.json_normalize(items)

    if "valor" in df.columns:
        df["valor"] = pd.to_numeric(df["valor"], errors="coerce")

    if "data" in df.columns:
        df["data"] = pd.to_datetime(df["data"], errors="coerce")

    return df


def to_excel_bytes(dfs: dict) -> bytes:
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        for name, df in dfs.items():
            df.to_excel(writer, index=False, sheet_name=name[:31])
    return bio.getvalue()


def agg_top(df, col, top_n):
    if df.empty or col not in df.columns:
        return pd.DataFrame()

    return (
        df.groupby(col, dropna=False)["valor"]
        .sum()
        .reset_index()
        .sort_values("valor", ascending=False)
        .head(top_n)
    )

# ======================================================
# UI
# ======================================================
st.title("📌 Empenhos – Execução Orçamentária")
st.caption("Portal da Transparência · Despesas / Documentos · Fase 1 (Empenho)")

with st.sidebar:
    st.header("Parâmetros da Consulta")

    unidade_gestora = st.text_input("Unidade Gestora", UG_PADRAO)
    gestao = st.text_input("Gestão", GESTAO_PADRAO)

    ano = st.number_input(
        "Ano",
        min_value=2011,
        max_value=2100,
        value=date.today().year
    )

    d_ini = st.date_input("Data inicial", date(int(ano), 1, 1))
    d_fim = st.date_input("Data final", min(date.today(), date(int(ano), 12, 31)))

    st.divider()
    st.header("Performance")

    page_size = st.selectbox("tamanhoPagina", [100, 200, 500], index=1)
    max_pages = st.slider("Máx. páginas por dia", 1, 100, 20)

    st.divider()
    st.header("Filtros")

    acao = st.text_input("Ação (SIAFI)")
    elemento = st.text_input("Elemento (SIAFI)")
    favorecido = st.text_input("Favorecido contém")

    top_n = st.slider("Top N gráficos", 5, 30, 10)

    run = st.button("🔎 Buscar Empenhos", use_container_width=True)

if not run:
    st.info("Configure os parâmetros e clique em **Buscar Empenhos**.")
    st.stop()

if d_ini > d_fim:
    st.error("Data inicial maior que a final.")
    st.stop()

# ======================================================
# EXECUÇÃO
# ======================================================
url = f"{BASE_URL}/{ENDPOINT}"

total_days = (d_fim - d_ini).days + 1
progress = st.progress(0, text="Iniciando consulta...")

all_items = []
cur = d_ini
i = 0

with st.spinner("Consultando API dia a dia..."):
    while cur <= d_fim:
        i += 1
        progress.progress(
            i / total_days,
            text=f"{cur.strftime('%d/%m/%Y')} ({i}/{total_days})"
        )

        items = fetch_day(
            url,
            unidade_gestora.strip(),
            gestao.strip(),
            cur,
            page_size=page_size,
            max_pages=max_pages,
        )

        all_items.extend(items)
        cur += timedelta(days=1)

progress.progress(1.0)

df = normalize(all_items)

# ======================================================
# FILTROS PÓS-COLETA
# ======================================================
if acao and "acao" in df.columns:
    df = df[df["acao"].astype(str) == acao]

if elemento and "elemento" in df.columns:
    df = df[df["elemento"].astype(str) == elemento]

if favorecido:
    needle = favorecido.lower()
    cols = [c for c in ["nomeFavorecido", "favorecido"] if c in df.columns]
    if cols:
        mask = False
        for c in cols:
            mask |= df[c].fillna("").str.lower().str.contains(needle)
        df = df[mask]

# ======================================================
# KPIs
# ======================================================
c1, c2, c3 = st.columns(3)

with c1:
    st.metric("Registros", f"{len(df):,}".replace(",", "."))

with c2:
    total = df["valor"].sum() if "valor" in df.columns else 0
    st.metric(
        "Total Empenhado (R$)",
        f"{total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    )

with c3:
    st.metric("Período", f"{d_ini} → {d_fim}")

# ======================================================
# GRÁFICOS
# ======================================================
st.divider()
left, right = st.columns(2)

with left:
    st.subheader("📊 Top por Ação")
    top_acao = agg_top(df, "acao", top_n)
    st.bar_chart(top_acao.set_index("acao")["valor"]) if not top_acao.empty else st.info("Sem dados")

with right:
    st.subheader("📊 Top por Favorecido")
    fav_col = "nomeFavorecido" if "nomeFavorecido" in df.columns else "favorecido"
    top_fav = agg_top(df, fav_col, top_n) if fav_col in df.columns else pd.DataFrame()
    st.bar_chart(top_fav.set_index(fav_col)["valor"]) if not top_fav.empty else st.info("Sem dados")

# ======================================================
# TABELA + EXPORTAÇÃO
# ======================================================
st.divider()
st.subheader("📋 Detalhamento")
st.dataframe(df, use_container_width=True)

st.subheader("⬇️ Exportar")
st.download_button(
    "Baixar CSV",
    data=df.to_csv(index=False).encode("utf-8"),
    file_name="empenhos_ug120052.csv",
    mime="text/csv",
)

xlsx = to_excel_bytes({
    "empenhos": df,
    "top_acao": top_acao if not top_acao.empty else pd.DataFrame(),
})

st.download_button(
    "Baixar Excel",
    data=xlsx,
    file_name="empenhos_ug120052.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
