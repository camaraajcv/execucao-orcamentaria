import requests
import streamlit as st
import requests

st.title("Diagnóstico Portal da Transparência")

token = st.secrets.get("PORTAL_TRANSPARENCIA_TOKEN", "").strip()
header_name = st.secrets.get("PORTAL_TRANSPARENCIA_HEADER", "chave-api-dados").strip()

st.write("Header usado:", header_name)
st.write("Token configurado?", "SIM" if token else "NÃO")
st.write("Tamanho do token:", len(token))

url = "https://api.portaldatransparencia.gov.br/api-de-dados/despesa-empenho"
params = {
    "codigoUGExecutora": "120052",
    "dataInicio": "2026-01-01",
    "dataFim": "2026-01-10",
    "pagina": 1,
    "tamanhoPagina": 1,
}

try:
    r = requests.get(url, headers={header_name: token}, params=params, timeout=30)
    st.write("Status code:", r.status_code)
    st.write("URL final:", r.url)
    st.text("Resposta (até 800 chars):")
    st.code(r.text[:800])
except Exception as e:
    st.exception(e)

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
