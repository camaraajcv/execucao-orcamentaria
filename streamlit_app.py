import streamlit as st
import requests
import pandas as pd
from datetime import date, datetime, timedelta
import time
import io

# =========================
# CONFIG
# =========================
st.set_page_config(page_title="Despesas — Documentos (UG 120052)", layout="wide")

BASE_URL = "https://api.portaldatransparencia.gov.br/api-de-dados"
ENDPOINT = "despesas/documentos"

DEFAULT_UG = "120052"

# =========================
# SECRETS
# =========================
if "PORTAL_TRANSPARENCIA_TOKEN" not in st.secrets:
    st.error("❌ Configure PORTAL_TRANSPARENCIA_TOKEN em Settings → Secrets no Streamlit Cloud.")
    st.stop()

TOKEN = str(st.secrets["PORTAL_TRANSPARENCIA_TOKEN"]).strip()
HEADER_NAME = str(st.secrets.get("PORTAL_TRANSPARENCIA_HEADER", "chave-api-dados")).strip()

HEADERS = {
    HEADER_NAME: TOKEN,
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (StreamlitCloud)",
    "Referer": "https://portaldatransparencia.gov.br/",
}

# =========================
# HELPERS
# =========================
def to_excel_bytes(dfs: dict) -> bytes:
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        for name, df in dfs.items():
            df.to_excel(writer, index=False, sheet_name=name[:31])
    return bio.getvalue()

def _request(url, params, timeout=30):
    return requests.get(url, headers=HEADERS, params=params, timeout=timeout)

def fetch_day(url: str, day_iso: str, extra_params: dict, page_size=500, max_pages=50, sleep_s=0.02):
    """
    Busca todas as páginas de UM DIA (dataEmissao) e retorna lista de itens.
    """
    all_items = []
    for page in range(1, max_pages + 1):
        params = dict(extra_params)
        params.update({
            "dataEmissao": day_iso,  # <-- obrigatório
            "pagina": page,
            "tamanhoPagina": page_size
        })

        r = _request(url, params)

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

        if sleep_s:
            time.sleep(sleep_s)

    return all_items

@st.cache_data(show_spinner=False, ttl=60*60*6)  # 6h
def fetch_range_cached(
    ug: str,
    start_day_iso: str,
    end_day_iso: str,
    page_size: int,
    max_pages_per_day: int,
):
    """
    Baixa dia a dia (dataEmissao) + paginação.
    Observação: nem sempre a API filtra por UG via parâmetro.
    Aqui vamos buscar “bruto” e filtrar UG no pandas depois (garante funcionar).
    """
    url = f"{BASE_URL}/{ENDPOINT}"

    start_day = datetime.fromisoformat(start_day_iso).date()
    end_day = datetime.fromisoformat(end_day_iso).date()

    items = []
    cur = start_day
    while cur <= end_day:
        day_iso = cur.isoformat()

        # Se a API aceitar filtro por UG, você pode colocar aqui depois.
        # Por segurança (e pra não dar 400), deixamos sem filtro de UG no request.
        extra_params = {}

        day_items = fetch_day(
            url,
            day_iso=day_iso,
            extra_params=extra_params,
            page_size=page_size,
            max_pages=max_pages_per_day,
            sleep_s=0.02
        )
        items.extend(day_items)
        cur += timedelta(days=1)

    return items

def normalize(items: list) -> pd.DataFrame:
    if not items:
        return pd.DataFrame()
    df = pd.json_normalize(items)

    # valor
    if "valor" in df.columns:
        df["valor"] = pd.to_numeric(df["valor"], errors="coerce")

    # data
    if "data" in df.columns:
        df["data"] = pd.to_datetime(df["data"], errors="coerce")

    return df

def apply_filters(df: pd.DataFrame, ug: str, fase: str, acao: str, elemento: str, favorecido: str):
    if df.empty:
        return df

    # filtro UG local (porque no retorno vem codigoUg)
    if "codigoUg" in df.columns:
        df = df[df["codigoUg"].astype(str) == str(ug)]

    if fase and "fase" in df.columns:
        df = df[df["fase"].astype(str).str.upper() == fase.upper()]

    if acao and "acao" in df.columns:
        df = df[df["acao"].astype(str) == acao]

    if elemento and "elemento" in df.columns:
        df = df[df["elemento"].astype(str) == elemento]

    if favorecido:
        needle = favorecido.lower().strip()
        cols = [c for c in ["nomeFavorecido", "favorecido"] if c in df.columns]
        if cols:
            mask = False
            for c in cols:
                mask = mask | df[c].fillna("").astype(str).str.lower().str.contains(needle, na=False)
            df = df[mask]

    return df

def agg_top(df: pd.DataFrame, group_col: str, top_n=15):
    if df.empty or "valor" not in df.columns or group_col not in df.columns:
        return pd.DataFrame()
    out = (
        df.groupby(group_col, dropna=False)["valor"]
        .sum()
        .reset_index()
        .sort_values("valor", ascending=False)
        .head(top_n)
    )
    return out

# =========================
# UI
# =========================
st.title("📌 Despesas — Documentos (com data de emissão)")
st.caption("Endpoint: /api-de-dados/despesas/documentos (parâmetro obrigatório: dataEmissao)")

with st.sidebar:
    st.header("Consulta")
    ug = st.text_input("UG executora (filtro local)", value=DEFAULT_UG)

    ano = st.number_input("Ano (a partir de 01/jan)", min_value=2011, max_value=2100, value=date.today().year, step=1)

    start_default = date(int(ano), 1, 1)
    end_default = min(date.today(), date(int(ano), 12, 31))

    d_ini = st.date_input("Data inicial", value=start_default)
    d_fim = st.date_input("Data final", value=end_default)

    st.divider()
    st.header("Performance / limites")
    page_size = st.selectbox("tamanhoPagina", options=[100, 200, 500], index=2)
    max_pages_per_day = st.slider("máx. páginas por dia", 1, 200, 50)

    st.divider()
    st.header("Filtros (pós-coleta)")
    fase = st.selectbox("Fase", options=["", "EMPENHO", "LIQUIDACAO", "PAGAMENTO"], index=0)
    acao = st.text_input("Ação (código SIAFI)", value="")
    elemento = st.text_input("Elemento (código SIAFI)", value="")
    favorecido = st.text_input("Favorecido (contém)", value="")

    top_n = st.slider("Top N nos gráficos", 5, 50, 15)

    run = st.button("🔎 Buscar", use_container_width=True)

if d_ini > d_fim:
    st.error("Data inicial não pode ser maior que a final.")
    st.stop()

if not run:
    st.info("Configure os filtros e clique em **Buscar**.")
    st.stop()

# =========================
# FETCH
# =========================
url = f"{BASE_URL}/{ENDPOINT}"

# barra de progresso por dias
total_days = (d_fim - d_ini).days + 1
prog = st.progress(0, text="Iniciando...")

all_items = []
cur = d_ini
day_i = 0

with st.spinner("Consultando dia a dia (dataEmissao) + paginação..."):
    while cur <= d_fim:
        day_i += 1
        prog.progress(min(day_i / total_days, 1.0), text=f"Dia {day_i}/{total_days} — {cur.isoformat()}")

        # baixa 1 dia (com cache por range seria mais eficiente, mas aqui fica mais controlável)
        try:
            items_day = fetch_day(
                url=url,
                day_iso=cur.isoformat(),
                extra_params={},
                page_size=int(page_size),
                max_pages=int(max_pages_per_day),
                sleep_s=0.02
            )
            all_items.extend(items_day)
        except Exception as e:
            st.exception(e)
            st.stop()

        cur += timedelta(days=1)

prog.progress(1.0, text=f"Concluído — registros brutos: {len(all_items)}")

df = normalize(all_items)

# =========================
# FILTERS
# =========================
df = apply_filters(
    df,
    ug=str(ug).strip(),
    fase=fase.strip(),
    acao=acao.strip(),
    elemento=elemento.strip(),
    favorecido=favorecido.strip()
)

# =========================
# KPIs
# =========================
c1, c2, c3 = st.columns(3)
with c1:
    st.metric("Registros (após filtros)", f"{len(df):,}".replace(",", "."))
with c2:
    total = float(df["valor"].sum()) if (not df.empty and "valor" in df.columns) else 0.0
    st.metric("Total (R$)", f"{total:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
with c3:
    st.metric("Período", f"{d_ini.isoformat()} → {d_fim.isoformat()}")

st.divider()

# =========================
# CHARTS
# =========================
left, right = st.columns(2)

with left:
    st.subheader("📊 Top por Ação")
    top_acao = agg_top(df, "acao", top_n=top_n)
    if not top_acao.empty:
        st.bar_chart(top_acao.set_index("acao")["valor"])
        st.dataframe(top_acao, use_container_width=True, hide_index=True)
    else:
        st.info("Sem dados/coluna 'acao' para agregar.")

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
        st.info("Sem dados/coluna de favorecido para agregar.")

st.divider()

# =========================
# TABLE + EXPORT
# =========================
st.subheader("📋 Detalhamento")
st.dataframe(df, use_container_width=True)

st.subheader("⬇️ Exportar")
csv_bytes = df.to_csv(index=False).encode("utf-8")
st.download_button("Baixar CSV", data=csv_bytes, file_name="despesas_documentos.csv", mime="text/csv")

xlsx = to_excel_bytes({
    "documentos": df,
    "top_acao": top_acao if 'top_acao' in locals() else pd.DataFrame(),
})
st.download_button(
    "Baixar Excel",
    data=xlsx,
    file_name="despesas_documentos.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

with st.expander("🛠 Diagnóstico"):
    st.write("URL:", url)
    st.write("Header:", HEADER_NAME)
    st.write("Token length:", len(TOKEN))
    st.write("Parâmetro obrigatório usado: dataEmissao")
    st.write("Registros brutos:", len(all_items))
    st.write("Colunas:", list(df.columns))
    if all_items:
        st.write("Exemplo de item bruto:")
        st.json(all_items[0])
