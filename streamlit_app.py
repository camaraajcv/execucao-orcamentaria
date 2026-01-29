import streamlit as st
import requests
import zipfile
import io
import pandas as pd
from datetime import date

# ==========================
# CONFIG
# ==========================
st.set_page_config(page_title="Orçamento/Despesa 2026 — UO 52111 e 52911", layout="wide")

FONTE_URL = "https://portaldatransparencia.gov.br/download-de-dados/orcamento-despesa/2026"
UOS_ALVO = {"52111", "52911"}  # manter como string pra bater com qualquer formatação

# Coluna chave (como você descreveu)
COL_UO = "Código Unidade Orçamentária"

# ==========================
# FUNÇÕES
# ==========================
@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)  # cache 24h
def baixar_zip(url: str) -> bytes:
    """
    Baixa o ZIP do Portal (ou o arquivo que o link devolver).
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (StreamlitCloud)",
        "Accept": "*/*",
        "Referer": "https://portaldatransparencia.gov.br/",
    }
    r = requests.get(url, headers=headers, timeout=180)
    r.raise_for_status()
    return r.content

def achar_primeiro_csv_no_zip(zip_bytes: bytes) -> str:
    """
    Retorna o nome do primeiro arquivo .csv dentro do zip.
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        nomes = z.namelist()
        csvs = [n for n in nomes if n.lower().endswith(".csv")]
        if not csvs:
            raise RuntimeError(f"Não encontrei nenhum CSV dentro do ZIP. Arquivos: {nomes[:20]}")
        return csvs[0]

def ler_csv_filtrado_do_zip(zip_bytes: bytes, member_csv: str, uos_alvo: set[str], chunksize: int = 200_000) -> pd.DataFrame:
    """
    Lê o CSV dentro do ZIP em chunks e filtra pelas UOs alvo.
    """
    # Tentativas de encoding e separador comuns em dados do governo
    encodings = ["utf-8-sig", "latin-1"]
    seps = [";", ","]

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        with z.open(member_csv) as f:
            raw = f.read()  # lê pra memória (se o CSV for gigante e estourar, eu te passo versão streaming)
            bio = io.BytesIO(raw)

    last_err = None
    for enc in encodings:
        for sep in seps:
            try:
                # Reinicia o buffer a cada tentativa
                bio.seek(0)

                # Leitura em chunks para filtrar sem carregar tudo
                it = pd.read_csv(
                    bio,
                    sep=sep,
                    encoding=enc,
                    dtype=str,
                    chunksize=chunksize,
                    low_memory=False
                )

                partes = []
                for chunk in it:
                    if COL_UO not in chunk.columns:
                        raise RuntimeError(
                            f"Coluna '{COL_UO}' não encontrada. Colunas disponíveis: {list(chunk.columns)[:40]}"
                        )
                    # Normaliza UO como string sem espaços
                    uo = chunk[COL_UO].astype(str).str.strip()
                    partes.append(chunk[uo.isin(uos_alvo)])

                df = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame()
                return df

            except Exception as e:
                last_err = e

    raise RuntimeError(f"Falha ao ler o CSV. Último erro: {last_err}")

def to_excel_bytes(df: pd.DataFrame) -> bytes:
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="UO_52111_52911")
    return out.getvalue()


# ==========================
# UI
# ==========================
st.title("📥 Orçamento/Despesa 2026 — filtro por Unidade Orçamentária")
st.write(f"Filtro aplicado: **{', '.join(sorted(UOS_ALVO))}**")

with st.sidebar:
    st.header("Parâmetros")
    chunksize = st.selectbox("Tamanho do chunk (performance)", [50_000, 100_000, 200_000, 400_000], index=2)
    carregar = st.button("⬇️ Baixar ZIP e carregar dados", use_container_width=True)

    st.divider()
    st.caption("Fonte:")
    st.write(FONTE_URL)

if not carregar:
    st.info("Clique em **Baixar ZIP e carregar dados**.")
    st.stop()

with st.spinner("Baixando ZIP…"):
    zip_bytes = baixar_zip(FONTE_URL)

with st.spinner("Localizando CSV no ZIP…"):
    csv_name = achar_primeiro_csv_no_zip(zip_bytes)

st.success(f"CSV encontrado no ZIP: **{csv_name}**")

with st.spinner("Lendo CSV e filtrando por Unidade Orçamentária (em chunks)…"):
    df = ler_csv_filtrado_do_zip(zip_bytes, csv_name, UOS_ALVO, chunksize=int(chunksize))

if df.empty:
    st.warning("Nenhum registro encontrado para as Unidades Orçamentárias informadas.")
else:
    st.success(f"Registros após filtro: **{len(df):,}**".replace(",", "."))

# ==========================
# EXIBIÇÃO + DOWNLOADS
# ==========================
st.subheader("📊 Dados filtrados")
st.dataframe(df, use_container_width=True)

st.subheader("⬇️ Exportar")
st.download_button(
    "Baixar CSV filtrado",
    data=df.to_csv(index=False).encode("utf-8"),
    file_name="orcamento_despesa_2026_uo_52111_52911.csv",
    mime="text/csv",
)
st.download_button(
    "Baixar Excel filtrado",
    data=to_excel_bytes(df),
    file_name="orcamento_despesa_2026_uo_52111_52911.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

# ==========================
# DICIONÁRIO DE DADOS (resumo)
# ==========================
with st.expander("📘 Dicionário de dados (resumo)"):
    st.markdown(
        """
**Colunas principais (conforme informado):**
- Exercício
- Código/Nome Órgão Superior e Subordinado
- **Código/Nome Unidade Orçamentária**
- Código/Nome Função e Subfunção
- Código/Nome Programa Orçamentário
- Código/Nome Ação
- Categoria Econômica
- Grupo de Despesa (GND)
- Elemento de Despesa
- Orçamento Inicial (R$)
- Orçamento Atualizado (R$)
- Orçamento Empenhado (R$)
- Orçamento Realizado (R$)
- % Realizado do orçamento (Realizado/Atualizado * 100)
        """
    )

# ==========================
# RODAPÉ (FONTE)
# ==========================
st.markdown("---")
st.caption(f"Fonte dos dados: {FONTE_URL}")
