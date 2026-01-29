import streamlit as st
import requests

st.title("Diagnóstico Portal da Transparência (A)")

token = st.secrets.get("PORTAL_TRANSPARENCIA_TOKEN", "").strip()
st.write("Token configurado?", "SIM" if token else "NÃO", "| len:", len(token))

def call(endpoint, params=None, header_name="chave-api-dados"):
    url = f"https://api.portaldatransparencia.gov.br/api-de-dados/{endpoint}"
    headers = {
        header_name: token,
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (StreamlitCloud diagnostic)",
    }
    r = requests.get(url, headers=headers, params=params or {}, timeout=30)
    st.subheader(endpoint)
    st.write("Status:", r.status_code)
    st.write("URL:", r.url)
    st.code(r.text[:800] or "<corpo vazio>")

call("orgaos-superiores")  # endpoint leve, normalmente responde
call(
    "despesa-empenho",
    {
        "codigoUGExecutora": "120052",
        "dataInicio": "2026-01-01",
        "dataFim": "2026-01-10",
        "pagina": 1,
        "tamanhoPagina": 1,
    },
)

st.stop()


token = "01cd6bbdc54cdc17056661f10f368bab"
url = "https://api.portaldatransparencia.gov.br/api-de-dados/despesa-empenho"
params = {
    "codigoUGExecutora": "120052",
    "dataInicio": "2026-01-01",
    "dataFim": "2026-01-29",
    "pagina": 1,
    "tamanhoPagina": 10,
}
r = requests.get(url, headers={"chave-api-dados": token}, params=params, timeout=30)
print(r.status_code)
print(r.text[:500])
