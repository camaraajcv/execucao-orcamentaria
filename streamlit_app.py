import streamlit as st
import requests
import pandas as pd
from datetime import date, datetime, timedelta
import time
import io

st.set_page_config(page_title="Empenhos — UG 120052", layout="wide")

BASE_URL = "https://api.portaldatransparencia.gov.br/api-de-dados"
ENDPOINT = "despesas/documentos"

UG_PADRAO = "120052"
GESTAO_PADRAO = "0001"
FASE_EMPENHO = 1

if "PORTAL_TRANSPARENCIA_TOKEN" not in st.secrets:
    st.error("Configure PORTAL_TRANSPARENCIA_TOKEN em Settings → Secrets no Streamlit Cloud.")
    st.stop()

TOKEN = str(st.secrets["PORTAL_TRANSPARENCIA_TOKEN"]).strip()
HEADER_NAME = str(st.secrets.get("PORTAL_TRANSPARENCIA_HEADER", "chave-api-dados")).strip()

HEADERS = {
    HEADER_NAME: TOKEN,
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (StreamlitCloud)",
    "Referer": "https://portaldatransparencia.gov.br/",
}

def to_excel_bytes(dfs: dict) -> bytes:
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        for name, df in dfs.items():
            df.to_excel(writer, index=False, sheet_name=name[:31])
    return bio.getvalue()

def fetch_day(url: str, unidade_gestora: str, gestao: str, day: date,
              page_size=500, max_pages=50, sleep_s=0.02):
    """Busca todas as páginas de um dia (dataEmissao) para fase=1 (Empenho)."""
    day_br = day.strftime("%d/%m/%Y")
    all_items = []

    for page in range(1, max_pages + 1):
        params = {
            "unidadeGestora": unidade_gestora,
            "gestao": gestao,
            "dataEmissao": day_br,
            "fase": FASE_EMPENHO,
            "pagina": page,
            "tamanhoPagina": page_size,
        }
        r = requests.get(url, headers=HEADERS, params=params, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(
                f"HTTP {r.status_code}\nURL:\n{r.url}\n\nResposta:\n{(r.text or '')[:1500]}"
            )

        data = r.json()
        if not data:
            break

        all_items.extend(data)
        if len(data) < page_size:
            break

        time.sleep(sleep_s)

    return all_items

def normalize(items: list) -> pd.DataFrame:
    if not items:
        return pd.DataFrame()
    df = pd.json_normalize(items)
    if "valor" in df.columns:
        df["valor"] = pd.to_numeric(df["valor"], errors="coerce")
    if "data" in df.columns:
        df["data"] = pd.to_datetime(df["data"], errors="coerce")
    return df

def agg_top(df: pd.DataFrame, group_col: str, top_n=15):
    if df.empty or "valor" not in df.columns or group_col not in df.columns:
        return pd.DataFrame()
    return (
        df.groupby(group_col, dropna=False)["valor"]
        .sum()
        .reset_index()
        .sort_values("valor", ascending=False)
        .head(top_n)
    )

st.title("📌 Empenhos (fase=1) — Despesas/Documentos")
st.caption("Parâmetros obrigatórios: unidadeGestora, gestao, dataEmissao (DD/MM/AAAA), fase, pagina")

with st.sidebar:
    st.header("Consulta")
    unidade_gestora = st.text_input("Unidade Gestora", value=UG_PADRAO)
    gestao = st.text_input("Gestão", value=GESTAO_PADRAO)

    ano = st.number_input("Ano", min_value=2011, max_value=2100, value=date.today().year, step=1)
    d_ini = st.date_input("Data inicial", value=date(int(ano), 1, 1))
    d_fim = st.date_input("Data final", value=min(date.today(), date(int(ano), 12, 31)))

    st.divider()
    st.header("Limites")
    page_size = st.selectbox("tamanhoPagina", options=[100, 200, 500], index=2)
    max_pages_per_day = st.slider("máx. páginas por dia", 1, 200, 50)

    st.divider()
    st.header("Filtros (pós-coleta)")
    acao = st.text_input("Ação (SIAFI)", value="")
    elemento = st.text_input("Elemento (SIAFI)", value="")
    favorecido = st.text_input("Favorecido contém", value="")
    top_n = st.slider("Top N gráficos", 5, 50, 15)

    run = st.button("🔎 Buscar Empenhos", use_container_width=True)

if d_ini > d_fim:
    st.error("Data inicial não pode ser maior que a final.")
    st.stop()

if not run:
    st.info("Configure os filtros e clique em **Buscar Empenhos**.")
    st.stop()

url = f"{BASE_URL}/{ENDPOINT}"

total_days = (d_fim - d_ini).days + 1
prog = st.progress(0, text="Iniciando...")

all_items = []
cur = d_ini
i = 0

with st.spinner("Consultando dia a dia + paginação..."):
    while cur <= d_fim:
        i += 1
        prog.progress(i / total_days, text=f"Dia {i}/{total_days} — {cur.strftime('%d/%m/%Y')}")
        items_day = fetch_day(
            url=url,
            unidade_gestora=unidade_gestora.strip(),
            gestao=gestao.strip(),
            day=cur,
            page_size=int(page_size),
            max_pages=int(max_pages_per_day),
            sleep_s=0.02,
        )
        all_items.extend(items_day)
        cur += timedelta(days=1)

prog.progress(1.0, text=f"Concluído — registros brutos: {len(all_items)}")

df = normalize(all_items)

# filtros pós-coleta
if acao.strip() and "acao" in df.columns:
    df = df[df["acao"].astype(str) == acao.strip()]

if elemento.strip() and "elemento" in df.columns:
    df = df[df["elemento"].astype(str) == elemento.strip()]

if favorecido.strip():
    needle = favorecido.lower().strip()
    cols = [c for c in ["nomeFavorecido", "favorecido"] if c in df.columns]
    if cols:
        mask = False
        for c in cols:
            mask = mask | df[c].fillna("").astype(str).str.lower().str.contains(needle, na=False)
        df = df[mask]

# KPIs
c1, c2, c3 = st.columns(3)
with c1:
    st.metric("Registros", f"{len(df):,}".replace(",", "."))
with c2:
    total = float(df["valor"].sum()) if (not df.empty and "valor" in df.columns) else 0.0
    st.metric("Total Empenhado (R$)", f"{total:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
with c3:
    st.metric("Período", f"{d_ini.isoformat()} → {d_fim.isoformat()}")

st.divider()

# Gráficos
left, right = st.columns(2)

with left:
    st.subheader("📊 Top por Ação")
    top_acao = agg_top(df, "acao", top_n=top_n)
    if not top_acao.empty:
        st.bar_chart(top_acao.set_index("acao")["valor"])
        st.dataframe(top_acao, use_container_width=True, hide_index=True)
    else:
        st.info("Sem coluna 'acao' ou sem dados para agregar.")

with right:
    st.subheader("📊 Top por Favorecido")
    fav_col = "nomeFavorecido" if "nomeFavorecido" in df.columns else ("favorecido" if "favorecido" in df.columns else None)
    if fav_col and "valor" in df.columns and not df.empty:
        top_fav = (
            df.groupby(fav_col, dropna=False)["valor"]
            .sum()
            .reset_index()
            .sort_values("valor", ascending=False)
            .head(top_n)
        )
        st.bar_chart(top_fav.set_index(fav_col)["valor"])
        st.dataframe(top_fav, use_container_width=True, hide_index=True)
    else:
        st.info("Sem coluna de favorecido ou sem dados para agregar.")

st.divider()

st.subheader("📋 Detalhamento")
st.dataframe(df, use_container_width=True)

st.subheader("⬇️ Exportar")
csv_bytes = df.to_csv(index=False).encode("utf-8")
st.download_button("Baixar CSV", data=csv_bytes, file_name="empenhos_ug120052.csv", mime="text/csv")

xlsx = to_excel_bytes({
    "empenhos": df,
    "top_acao": top_acao if 'top_acao' in locals() else pd.DataFrame(),
})
st.download_button(
    "Baixar Excel",
    data=xlsx,
    file_name="empenhos_ug120052.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

with st.expander("🛠 Diagnóstico"):
    st.write("URL base:", url)
    st.write("Exemplo de URL (primeiro dia):")
    ex = {
        "unidadeGestora": unidade_gestora.strip(),
        "gestao": gestao.strip(),
        "dataEmissao": d_ini.strftime("%d/%m/%Y"),
        "fase": 1,
        "pagina": 1,
        "tamanhoPagina": int(page_size),
    }
    r = requests.get(url, headers=HEADERS, params=ex, timeout=30)
    st.write(r.url)
    st.write("Status:", r.status_code)
    st.code((r.text or "")[:800])
