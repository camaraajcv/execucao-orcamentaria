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

def detect_param_names(url: str):
    """
    Tenta descobrir os nomes dos parâmetros de data e UG aceitos pela API,
    sem travar o app. Faz chamadas leves com pagina=1/tamanhoPagina=1.
    """
    # candidatos comuns
    date_keys = ["dataEmissao", "data", "dataDocumento", "dataReferencia", "dataEmissaoDocumento"]
    ug_keys = ["codigoUg", "codigoUG", "codigoUGExecutora", "codigoUgExecutora", "ug"]

    # tenta achar um date_key que não retorne 404 e não seja rejeitado por "faltando data"
    # e tenta achar ug_key que seja aceito (se existir filtro por UG).
    # Observação: se o endpoint NÃO aceitar filtro por UG, filtramos no pandas depois.
    test_day = date.today().isoformat()

    chosen_date_key = None
    chosen_ug_key = None

    # 1) detectar date_key (normalmente obrigatório)
    for dk in date_keys:
        params = {dk: test_day, "pagina": 1, "tamanhoPagina": 1}
        r = _request(url, params)
        if r.status_code in (200, 204):
            chosen_date_key = dk
            break
        # se der 400 mas não for "data faltando", ainda pode ser o key certo (ex.: formato)
        if r.status_code == 400 and ("data" in (r.text or "").lower() or "emissao" in (r.text or "").lower()):
            chosen_date_key = dk
            break

    if chosen_date_key is None:
        # fallback: usa o mais provável
        chosen_date_key = "dataEmissao"

    # 2) detectar ug_key (opcional)
    for uk in ug_keys:
        params = {chosen_date_key: test_day, uk: DEFAULT_UG, "pagina": 1, "tamanhoPagina": 1}
        r = _request(url, params)
        if r.status_code in (200, 204):
            chosen_ug_key = uk
            break
        # se der 400 e mencionar UG, pode ser aceito mas com outra regra; ainda assim guardamos
        if r.status_code == 400 and ("ug" in (r.text or "").lower()):
            chosen_ug_key = uk
            break

    # se não achou ug_key, a API pode não filtrar por UG — filtraremos localmente
    return chosen_date_key, chosen_ug_key

def fetch_day(url: str, base_params: dict, page_size=500, max_pages=50, sleep_s=0.03):
    all_items = []
    for page in range(1, max_pages + 1):
        params = dict(base_params)
        params["pagina"] = page
        params["tamanhoPagina"] = page_size

        r = _request(url, params)
        if r.status_code != 200:
            # mostra um erro bem útil
            raise RuntimeError(
                f"HTTP {r.status_code}\nURL: {r.url}\nResposta: {(r.text or '')[:1200]}"
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
    year: int,
    start_day_iso: str,
    end_day_iso: str,
    page_size: int,
    max_pages_per_day: int,
):
    """
    Baixa dia a dia (data de emissão) dentro do intervalo e concatena.
    Usa cache para evitar reprocessar.
    """
    url = f"{BASE_URL}/{ENDPOINT}"
    date_key, ug_key = detect_param_names(url)

    start_day = datetime.fromisoformat(start_day_iso).date()
    end_day = datetime.fromisoformat(end_day_iso).date()

    items = []
    cur = start_day
    while cur <= end_day:
        day_iso = cur.isoformat()

        params = {date_key: day_iso}
        # se o endpoint aceitar filtro por UG, aplicamos; senão, filtramos no pandas depois
        if ug_key:
            params[ug_key] = ug

        day_items = fetch_day(
            url,
            params,
            page_size=page_size,
            max_pages=max_pages_per_day,
            sleep_s=0.02
        )
        items.extend(day_items)
        cur += timedelta(days=1)

    # retorna também quais parâmetros foram usados (pra diagnóstico)
    return items, date_key, ug_key

def normalize(items: list) -> pd.DataFrame:
    if not items:
        return pd.DataFrame()
    df = pd.json_normalize(items)

    # tenta converter "valor"
    if "valor" in df.columns:
        df["valor"] = pd.to_numeric(df["valor"], errors="coerce")

    # tenta parsear data
    if "data" in df.columns:
        # algumas vezes vem com hora; pandas lida
        df["data"] = pd.to_datetime(df["data"], errors="coerce")

    return df

def apply_filters(df: pd.DataFrame, ug: str, fase: str, acao: str, elemento: str, favorecido: str):
    if df.empty:
        return df

    # filtro UG local (porque pode não existir filtro na API)
    for ug_col in ["codigoUg", "codigoUG", "codigoUgExecutora", "codigoUGExecutora"]:
        if ug_col in df.columns:
            df = df[df[ug_col].astype(str) == str(ug)]
            break

    if fase and "fase" in df.columns:
        df = df[df["fase"].astype(str).str.upper() == fase.upper()]

    if acao and "acao" in df.columns:
        df = df[df["acao"].astype(str) == acao]

    if elemento and "elemento" in df.columns:
        df = df[df["elemento"].astype(str) == elemento]

    if favorecido:
        # tenta em nomeFavorecido e favorecido
        cols = [c for c in ["nomeFavorecido", "favorecido"] if c in df.columns]
        if cols:
            needle = favorecido.lower().strip()
            mask = False
            for c in cols:
                mask = mask | df[c].fillna("").astype(str).str.lower().str.contains(needle, na=False)
            df = df[mask]

    return df

def agg_top(df: pd.DataFrame, value_col="valor", group_col="acao", top_n=15):
    if df.empty or value_col not in df.columns or group_col not in df.columns:
        return pd.DataFrame()
    out = (
        df.groupby(group_col, dropna=False)[value_col]
        .sum()
        .reset_index()
        .sort_values(value_col, ascending=False)
        .head(top_n)
    )
    return out

# =========================
# UI
# =========================
st.title("📌 Despesas — Documentos (Portal da Transparência)")
st.caption("Endpoint: /api-de-dados/despesas/documentos")

with st.sidebar:
    st.header("Consulta")
    ug = st.text_input("UG executora", value=DEFAULT_UG)

    ano = st.number_input("Ano (a partir de 01/jan)", min_value=2011, max_value=2100, value=date.today().year, step=1)

    # intervalo dentro do ano, padrão: 01/jan até hoje (ou 31/dez se ano passado)
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

    run = st.button("🔎 Buscar desde 01/jan (ou intervalo)", use_container_width=True)

if d_ini > d_fim:
    st.error("Data inicial não pode ser maior que a final.")
    st.stop()

if not run:
    st.info("Configure os filtros e clique em **Buscar**.")
    st.stop()

# =========================
# FETCH
# =========================
with st.spinner("Consultando dia a dia (data de emissão) + paginação..."):
    items, date_key_used, ug_key_used = fetch_range_cached(
        ug=str(ug).strip(),
        year=int(ano),
        start_day_iso=d_ini.isoformat(),
        end_day_iso=d_fim.isoformat(),
        page_size=int(page_size),
        max_pages_per_day=int(max_pages_per_day),
    )

df = normalize(items)
df = apply_filters(df, ug=str(ug).strip(), fase=fase, acao=acao.strip(), elemento=elemento.strip(), favorecido=favorecido.strip())

# =========================
# KPIs
# =========================
c1, c2, c3 = st.columns(3)
with c1:
    st.metric("Registros", f"{len(df):,}".replace(",", "."))
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
    top_acao = agg_top(df, group_col="acao", top_n=top_n)
    if not top_acao.empty:
        st.bar_chart(top_acao.set_index("acao")["valor"])
        st.dataframe(top_acao, use_container_width=True, hide_index=True)
    else:
        st.info("Sem dados/coluna 'acao' para agregar.")

with right:
    st.subheader("📊 Top por Favorecido")
    # tenta usar nomeFavorecido; se não existir, usa favorecido
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

xlsx = to_excel_bytes({"documentos": df, "top_acao": top_acao if 'top_acao' in locals() else pd.DataFrame()})
st.download_button(
    "Baixar Excel",
    data=xlsx,
    file_name="despesas_documentos.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

with st.expander("🛠 Diagnóstico"):
    st.write("Endpoint:", f"{BASE_URL}/{ENDPOINT}")
    st.write("Header usado:", HEADER_NAME)
    st.write("Token length:", len(TOKEN))
    st.write("Parâmetro de data detectado:", date_key_used)
    st.write("Parâmetro de UG detectado:", ug_key_used or "(não aceito → filtrando localmente)")
    st.write("Colunas:", list(df.columns))
    if items:
        st.write("Exemplo de item bruto:")
        st.json(items[0])
