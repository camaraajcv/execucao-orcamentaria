import random
from requests.exceptions import ReadTimeout, ConnectTimeout, ConnectionError, HTTPError

DEFAULT_TIMEOUT = 120  # << aumenta aqui

def request_with_retry(url, params, max_retries=6, base_sleep=1.0, timeout=DEFAULT_TIMEOUT):
    """
    Retry com backoff exponencial + jitter para timeouts/erros transitórios.
    """
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=timeout)
            # alguns erros são transitórios; vamos tratar
            if r.status_code in (429, 500, 502, 503, 504):
                raise HTTPError(f"HTTP {r.status_code}", response=r)
            r.raise_for_status()
            return r
        except (ReadTimeout, ConnectTimeout, ConnectionError, HTTPError) as e:
            last_err = e
            # backoff exponencial com jitter
            sleep_s = base_sleep * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
            time.sleep(min(sleep_s, 12))  # trava o sleep máximo
    raise RuntimeError(f"Falha após retries. Último erro: {last_err}")

def fetch_day(url: str, unidade_gestora: str, gestao: str, day: date,
              page_size=200, max_pages=20):
    """
    Busca todas as páginas de um dia (dataEmissao) para fase=1 (Empenho),
    com timeout maior + retry.
    """
    day_br = day.strftime("%d/%m/%Y")
    all_items = []

    for page in range(1, max_pages + 1):
        params = {
            "unidadeGestora": unidade_gestora,
            "gestao": gestao,
            "dataEmissao": day_br,
            "fase": 1,
            "pagina": page,
            "tamanhoPagina": page_size,
        }

        r = request_with_retry(url, params=params, timeout=DEFAULT_TIMEOUT)
        data = r.json()

        if not data:
            break

        all_items.extend(data)

        # se veio menos que page_size, acabou
        if len(data) < page_size:
            break

    return all_items
