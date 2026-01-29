# streamlit_app.py
from __future__ import annotations

import io
import time
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
import streamlit as st


# =========================
# Config
# =========================
DEFAULT_UG = "120052"
BASE_URL = "https://api.portaldatransparencia.gov.br/api-de-dados"
ENDPOINT_EMPENHOS = "despesa-empenho"  # Portal da Transparência (Empenhos)

# Colunas "preferidas" (se existirem após normalização)
PREFERRED_COLS = [
    "data_emissao",
    "numero_empenho",
    "valor_empenho",
    "acao_codigo",
    "acao_nome",
    "ndd",
    "nd_codigo",
    "nd_descricao",
    "favorecido_nome",
    "favorecido_cpf_cnpj",
    "ug_codigo",
    "ug_nome",
]

st.set_page_config(
    page_title="Empenhos — UG 120052",
    layout="wide",
)


# =========================
# Helpers: API client
# =========================
def _get_api_token_and_header() -> Tuple[str, str]:
    """
    Lê token do st.secrets e define header.
    Header mais comum: "chave-api-dados"
    """
    if "PORTAL_TRANSPARENCIA_TOKEN" not in st.secrets:
        raise RuntimeError(
            "Faltou configurar o segredo PORTAL_TRANSPARENCIA_TOKEN no Streamlit Cloud "
            "(Settings → Secrets)."
        )
    token = str(st.secrets["PORTAL_TRANSPARENCIA_TOKEN"]).strip()

    header_name = str(st.secrets.get("PORTAL_TRANSPARENCIA_HEADER", "chave-api-dados")).strip()
    return token, header_name


def _headers() -> Dict[str, str]:
    token, header_name = _get_api_token_and_header()
    return {
        header_name: token,
        "Accept": "application/json",
        "User-Agent": "streamlit-app (execucao-orcamentaria)",
    }


def api_get(endpoint: str, params: Dict[str, Any], timeout: Tuple[int, int] = (10, 60)) -> List[Dict[str, Any]]:
    url = f"{BASE_URL}/{endpoint.lstrip('/')}"
    r = requests.get(url, headers=_headers(), params=params, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, list):
        return data
    return [data]


def api_get_paged(
    endpoint: str,
    params: Dict[str, Any],
    *,
    page_param: str = "pagina",
    page_size_param: str = "tamanhoPagina",
    page_size: int = 500,
    max_pages: int = 300,
    sleep_between: float = 0.05,
    progress_cb=None,
) -> List[Dict[str, Any]]:
    all_items: List[Dict[str, Any]] = []

    for page in range(1, max_pages + 1):
        p = dict(params)
        p[page_param] = page
        p[page_size_param] = page_size

        items = api_get(endpoint, p)
        if not items:
            break

        all_items.extend(items)

        if progress_cb:
            progress_cb(page, len(all_items), len(items))

        if len(items) < page_size:
            break

        if sleep_between:
            time.sleep(sleep_between)

    return all_items


# =========================
# Helpers: Data normalization
# =========================
def normalize_empenhos(items: List[Dict[str, Any]]) -> pd.DataFrame:
    if not items:
        return pd.DataFrame()

    df = pd.json_normalize(items)

    # Mapa de renomeação (ajusta conforme vier da API)
    rename_map = {
        "dataEmissao": "data_emissao",
        "numeroEmpenho": "numero_empenho",
        "valorEmpenho": "valor_empenho",
        "favorecido.nome": "favorecido_nome",
        "favorecido.cpfCnpj": "favorecido_cpf_cnpj",
        "acao.codigo": "acao_codigo",
        "acao.nome": "acao_nome",
        "unidadeGestora.codigo": "ug_codigo",
        "unidadeGestora.nome": "ug_nome",
        "naturezaDespesa.codigo": "nd_codigo",
        "naturezaDespesa.descricao": "nd_descricao",
        # alguns retornos trazem NDD como "ndd" ou "numeroNDD"
        "ndd": "ndd",
        "numeroNDD": "ndd",
    }
    for src, dst in rename_map.items():
        if src in df.columns and dst not in df.columns:
            df = df.rename(columns={src: dst})

    # Tipos
    if "valor_empenho" in df.columns:
        df["valor_empenho"] = pd.to_numeric(df["valor_empenho"], errors="coerce")

    if "data_emissao" in df.columns:
        df["data_emissao"] = pd.to_datetime(df["data_emissao"], errors="coerce").dt.date

    # Ordena colunas
    cols = [c for c in PREFERRED_COLS if c in df.columns] + [c for c in df.columns if c not in PREFERRED_COLS]
    df = df[cols]
    return df


def build_aggregations(df: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    out: Dict[str, pd.DataFrame] = {}

    if df.empty or "valor_empenho" not in df.columns:
        return out

    if "acao_codigo" in df.columns or "acao_nome" in df.columns:
        group_cols = [c for c in ["acao_codigo", "acao_nome"] if c in df.columns]
        out["por_acao"] = (
            df.groupby(group_cols, dropna=False)["valor_empenho"]
            .sum()
            .reset_index()
            .sort_values("valor_empenho", ascending=False)
        )

    if "favorecido_nome" in df.columns:
        group_cols = [c for c in ["favorecido_nome", "favorecido_cpf_cnpj"] if c in df.columns]
        out["por_favorecido"] = (
            df.groupby(group_cols, dropna=False)["valor_empenho"]
            .sum()
            .reset_index()
            .sort_values("valor_empenho", ascending=False)
        )

    if "ndd" in df.columns:
        out["por_ndd"] = (
            df.groupby(["ndd"], dropna=False)["valor_empenho"]
            .sum()
            .reset_index()
            .sort_values("valor_empenho", ascending=False)
        )

    return out


def to_excel_bytes(dfs: Dict[str, pd.DataFrame]) -> bytes:
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        for name, df in dfs.items():
            safe = name[:31]  # limite Excel
            df.to_excel(writer, index=False, sheet_name=safe)
    return bio.getvalue()


# =========================
# Cached fetch
# =========================
@st.cache_data(ttl=60 * 30, show_spinner=False)  # 30 min
def fetch_empenhos_cached(
    ug: str,
    data_inicio: str,
    data_fim: str,
    acao: Optional[str],
    ndd: Optional[str],
    cpf_cnpj: Optional[str],
    nome_fav: Optional[str],
) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {
        "codigoUGExecutora": ug,
        "dataInicio": data_inicio,
        "dataFim": data_fim,
    }
    # filtros opcionais
    if acao:
        params["codigoAcao"] = acao
    if ndd:
        params["numeroNDD"] = ndd
    if cpf_cnpj:
        params["cpfCnpjFavorecido"] = cpf_cnpj
    if nome_fav:
        params["nomeFavorecido"] = nome_fav

    # Como é cacheado, aqui não usamos progress
    return api_get_paged(ENDPOINT_EMPENHOS, params=params, page_size=500, max_pages=300)


# =========================
# UI
# =========================
st.title("📌 Controle de Empenhos — UG Executora 120052")
st.caption("Fonte: API do Portal da Transparência (Empenhos).")

with st.sidebar:
    st.header("Filtros")

    ug = st.text_input("UG executora", value=DEFAULT_UG, help="Ex.: 120052")

    # Período padrão: últimos 30 dias
    today = date.today()
    d0_default = today - timedelta(days=30)
    d_ini = st.date_input("Data início", value=d0_default)
    d_fim = st.date_input("Data fim", value=today)

    st.divider()

    acao = st.text_input("Ação orçamentária (código)", value="", placeholder="ex: 21A0")
    ndd = st.text_input("NDD", value="", placeholder="ex: 123456")
    cpf_cnpj = st.text_input("CPF/CNPJ Favorecido", value="", placeholder="somente números (ou como a API aceitar)")
    nome_fav = st.text_input("Nome Favorecido (contém)", value="", placeholder="ex: EMPRESA XYZ")

    st.divider()
    texto_livre = st.text_input("Filtro local (texto livre na tabela)", value="", placeholder="filtra depois da coleta")

    st.divider()
    top_n = st.slider("Top N nos gráficos", 5, 50, 15)

    consultar = st.button("🔎 Consultar empenhos", use_container_width=True)

# Validação simples
if d_ini > d_fim:
    st.error("A Data início não pode ser maior que a Data fim.")
    st.stop()

# Só consulta quando clicar (pra não bater na API a cada mexida)
if not consultar:
    st.info("Ajuste os filtros na barra lateral e clique em **Consultar empenhos**.")
    st.stop()

# Busca com progress visual (fora do cache) se quiser ver progresso.
# Para manter simples e confiável, primeiro tenta cache; se der erro, mostra.
try:
    with st.spinner("Consultando API (cacheado quando possível)..."):
        items = fetch_empenhos_cached(
            ug=ug.strip(),
            data_inicio=d_ini.isoformat(),
            data_fim=d_fim.isoformat(),
            acao=acao.strip() or None,
            ndd=ndd.strip() or None,
            cpf_cnpj=cpf_cnpj.strip() or None,
            nome_fav=nome_fav.strip() or None,
        )
except Exception as e:
    st.error("Falha ao consultar a API. Detalhes abaixo:")
    st.exception(e)
    st.stop()

df = normalize_empenhos(items)

# Filtro local de texto livre (opcional)
if texto_livre.strip() and not df.empty:
    needle = texto_livre.strip().lower()
    mask = pd.Series(False, index=df.index)
    for col in df.columns:
        if df[col].dtype == object:
            mask = mask | df[col].fillna("").astype(str).str.lower().str.contains(needle, na=False)
    df = df[mask].copy()

# KPIs
c1, c2, c3 = st.columns(3)
with c1:
    st.metric("Registros", f"{len(df):,}".replace(",", "."))
with c2:
    total = float(df["valor_empenho"].sum()) if ("valor_empenho" in df.columns and not df.empty) else 0.0
    st.metric("Total (R$)", f"{total:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
with c3:
    st.metric("Período", f"{d_ini.isoformat()} → {d_fim.isoformat()}")

st.divider()

# Agregações
aggs = build_aggregations(df)

# Layout: gráficos
g1, g2 = st.columns(2)

with g1:
    st.subheader("📊 Empenhos por Ação Orçamentária")
    if "por_acao" in aggs and not aggs["por_acao"].empty:
        chart_df = aggs["por_acao"].head(top_n).copy()
        # Cria rótulo
        if "acao_nome" in chart_df.columns and "acao_codigo" in chart_df.columns:
            chart_df["acao"] = chart_df["acao_codigo"].astype(str) + " — " + chart_df["acao_nome"].astype(str)
        elif "acao_codigo" in chart_df.columns:
            chart_df["acao"] = chart_df["acao_codigo"].astype(str)
        else:
            chart_df["acao"] = chart_df["acao_nome"].astype(str)

        st.bar_chart(chart_df.set_index("acao")["valor_empenho"])
        st.dataframe(chart_df, use_container_width=True, hide_index=True)
    else:
        st.warning("Não foi possível montar o gráfico por Ação (colunas não retornaram ou não há dados).")

with g2:
    st.subheader("🏷️ Empenhos por Favorecido")
    if "por_favorecido" in aggs and not aggs["por_favorecido"].empty:
        chart_df = aggs["por_favorecido"].head(top_n).copy()
        if "favorecido_cpf_cnpj" in chart_df.columns:
            chart_df["favorecido"] = (
                chart_df["favorecido_nome"].astype(str) + " (" + chart_df["favorecido_cpf_cnpj"].astype(str) + ")"
            )
        else:
            chart_df["favorecido"] = chart_df["favorecido_nome"].astype(str)

        st.bar_chart(chart_df.set_index("favorecido")["valor_empenho"])
        st.dataframe(chart_df, use_container_width=True, hide_index=True)
    else:
        st.warning("Não foi possível montar o gráfico por Favorecido (colunas não retornaram ou não há dados).")

st.divider()

# Tabela detalhada
st.subheader("📋 Detalhamento (Empenhos)")
st.dataframe(df, use_container_width=True)

# Downloads
st.subheader("⬇️ Exportar")
export_dfs = {"empenhos_detalhe": df}
for k, v in aggs.items():
    export_dfs[k] = v

xlsx = to_excel_bytes(export_dfs)
st.download_button(
    "Baixar Excel (detalhe + agregações)",
    data=xlsx,
    file_name=f"empenhos_ug_{ug}_{d_ini.isoformat()}_{d_fim.isoformat()}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

# Diagnóstico rápido (útil no Cloud)
with st.expander("🔧 Diagnóstico (debug)"):
    st.write("Colunas retornadas:", list(df.columns))
    st.write("Exemplo de 1 item bruto (se existir):")
    st.json(items[0] if items else {})
