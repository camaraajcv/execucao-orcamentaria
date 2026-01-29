import streamlit as st
import requests

st.title("Diagnóstico Portal da Transparência (B)")

token = st.secrets.get("PORTAL_TRANSPARENCIA_TOKEN", "").strip()

def call(endpoint, header_name):
    url = f"https://api.portaldatransparencia.gov.br/api-de-dados/{endpoint}"
    headers = {
        header_name: token,
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0",
    }
    r = requests.get(url, headers=headers, timeout=30)
    st.write(f"Endpoint: {endpoint} | Header: {header_name} | Status: {r.status_code}")
    st.write("URL:", r.url)
    st.code(r.text[:300] or "<corpo vazio>")

for h in ["chave-api-dados", "chave-api"]:
    call("orgaos-superiores", h)

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
