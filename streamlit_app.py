import requests

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
