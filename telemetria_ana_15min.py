# -*- coding: utf-8 -*-
"""
TELEMETRIA ANA - dados a cada 15 minutos das estações do rio Xingu (PA)
=======================================================================
Baixa, pelo HidroWebService da ANA (série telemétrica "adotada"), as leituras de 15 minutos de cota (nível),
chuva e vazão de quatro estações da bacia do rio Xingu e gera:

  * um arquivo CSV por estação;
  * um gráfico com a evolução da cota de todas as estações.

Estações (código ANA):  18850000 Altamira | 18700000 Pedra do Ó | 18500000 Boa Esperança | 18460000 Boa Sorte
Para usar outras estações, basta editar a lista ESTACOES.

COMO USAR
  1) pip install requests pandas numpy matplotlib tqdm
  2) Informe suas credenciais do HidroWebService da ANA (nunca escreva a senha dentro do código):
       - variáveis de ambiente  ANA_IDENTIFICADOR  e  ANA_SENHA   ou
       - arquivo  credenciais_ana.json  ao lado do script:  {"identificador": "...", "senha": "..."}   ou
       - o script pergunta no terminal.
  3) python telemetria_ana_15min.py                                     (últimos 7 dias, até ontem)
     python telemetria_ana_15min.py --inicio 2026-10-01 --fim 2026-10-07

Fonte dos dados: Agência Nacional de Águas e Saneamento Básico (ANA) / Rede Hidrometeorológica Nacional.
Os dados são de telemetria e podem ser revisados pela ANA; cite a fonte ao usá-los.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import time
import unicodedata
from datetime import date, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
from matplotlib.lines import Line2D
from matplotlib.ticker import AutoMinorLocator, MaxNLocator

try:
    from tqdm.auto import tqdm
except ImportError:                                    # sem barra de progresso, o script continua funcionando
    def tqdm(it, **kw):
        return it

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================
ESTACOES = [("18850000", "Altamira"), ("18700000", "Pedra do Ó"),
            ("18500000", "Boa Esperança"), ("18460000", "Boa Sorte")]

DATA_INICIO = None                                     # "AAAA-MM-DD"; None = 7 dias antes da data final
DATA_FIM = None                                        # "AAAA-MM-DD"; None = ontem
PASTA_SAIDA = Path("saida_telemetria")                 # CSVs e gráfico são gravados aqui
ARQUIVO_CREDENCIAIS = Path(__file__).resolve().parent / "credenciais_ana.json"

BASE_URL = "https://www.ana.gov.br/hidrowebservice/EstacoesTelemetricas"
URL_AUTH = BASE_URL + "/OAUth/v1"
URL_DADOS = BASE_URL + "/HidroinfoanaSerieTelemetricaAdotada/v1"

MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
# Cores padrão do ggplot2 (escala "hue") para 1 a 6 séries
PALETAS = {1: ["#F8766D"], 2: ["#F8766D", "#00BFC4"], 3: ["#F8766D", "#00BA38", "#619CFF"],
           4: ["#F8766D", "#7CAE00", "#00BFC4", "#C77CFF"], 5: ["#F8766D", "#A3A500", "#00BF7D", "#00B0F6", "#E76BF3"],
           6: ["#F8766D", "#B79F00", "#00BA38", "#00BFC4", "#619CFF", "#F564E3"]}


# =============================================================================
# 1. AUTENTICAÇÃO E DOWNLOAD
# =============================================================================
def carregar_credenciais() -> tuple[str, str]:
    """Variáveis de ambiente -> arquivo JSON -> pergunta no terminal. Nada fica gravado no código."""
    uid, senha = os.environ.get("ANA_IDENTIFICADOR"), os.environ.get("ANA_SENHA")
    if not (uid and senha) and ARQUIVO_CREDENCIAIS.exists():
        dados = json.loads(ARQUIVO_CREDENCIAIS.read_text(encoding="utf-8"))
        uid, senha = dados.get("identificador"), dados.get("senha")
    if not (uid and senha):
        uid = input("Identificador do HidroWebService (ANA): ").strip()
        senha = getpass.getpass("Senha: ")
    return uid, senha


def autenticar(uid: str, senha: str) -> str:
    resp = requests.get(URL_AUTH, headers={"Identificador": uid, "Senha": senha, "Accept": "*/*"}, timeout=60)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Falha na autenticação (HTTP {resp.status_code}). Confira o identificador e a senha.")
    resultado = resp.json()
    token = None
    if isinstance(resultado.get("items"), dict):
        token = resultado["items"].get("tokenautenticacao") or resultado["items"].get("token")
    token = token or resultado.get("tokenautenticacao") or resultado.get("token")
    if not token:
        raise RuntimeError("Token não encontrado na resposta da ANA.")
    return token


def consultar_bloco(codigo: str, data_busca: date, token: str):
    """A API devolve 30 dias que terminam em `data_busca`. None = falhou; {'expirado': True} = renovar o token."""
    parametros = {"Código da Estação": str(codigo), "Tipo Filtro Data": "DATA_LEITURA",
                  "Data de Busca (yyyy-MM-dd)": data_busca.strftime("%Y-%m-%d"), "Range Intervalo de busca": "DIAS_30"}
    cabecalhos = {"accesstoken": token, "Authorization": "Bearer " + token, "Accept": "*/*"}
    for tentativa in range(1, 5):
        try:
            r = requests.get(URL_DADOS, params=parametros, headers=cabecalhos, timeout=120)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 401:
                return {"expirado": True}
            if r.status_code in (408, 429, 500, 502, 503, 504):
                time.sleep(2 ** tentativa)
                continue
            return None
        except requests.RequestException:
            time.sleep(2 ** tentativa)
    return None


def baixar_estacao(codigo: str, nome: str, inicio: date, fim: date, credenciais, token: list) -> pd.DataFrame:
    """Leituras de 15 min entre `inicio` e `fim` (inclusive), em blocos de 30 dias."""
    blocos, dia = [], inicio
    while dia <= fim:
        fim_bloco = min(dia + timedelta(days=29), fim)
        blocos.append(fim_bloco)
        dia = fim_bloco + timedelta(days=1)
    partes = []
    for fim_bloco in tqdm(blocos, desc=f"{codigo} {nome}", unit="bloco", leave=False):
        resposta = consultar_bloco(codigo, fim_bloco, token[0])
        if resposta and resposta.get("expirado"):                       # token vence: renova e tenta de novo
            token[0] = autenticar(*credenciais)
            resposta = consultar_bloco(codigo, fim_bloco, token[0])
        if resposta and resposta.get("items"):
            partes.append(pd.DataFrame(resposta["items"]))
        elif not resposta or resposta.get("expirado"):
            print(f"  [!] {nome}: falha ao baixar o bloco que termina em {fim_bloco}")
        time.sleep(0.3)                                                   # gentileza com o servidor da ANA
    if not partes:
        return pd.DataFrame(columns=["DataHora", "Chuva", "Cota", "Vazao"])
    bruto = pd.concat(partes, ignore_index=True)
    achar = lambda *nomes: next((c for c in bruto.columns if c.lower() in nomes), None)
    c_hora, c_chuva = achar("data_hora_medicao"), achar("chuva_adotada")
    c_cota, c_vazao = achar("cota_adotada"), achar("vazao_adotada")
    df = pd.DataFrame({
        "DataHora": pd.to_datetime(bruto[c_hora], errors="coerce"),
        "Chuva": pd.to_numeric(bruto[c_chuva], errors="coerce") if c_chuva else np.nan,
        "Cota": pd.to_numeric(bruto[c_cota], errors="coerce") if c_cota else np.nan,
        "Vazao": pd.to_numeric(bruto[c_vazao], errors="coerce") if c_vazao else np.nan,
    })
    df = df.dropna(subset=["DataHora"]).drop_duplicates("DataHora").sort_values("DataHora")
    df = df[(df["DataHora"] >= pd.Timestamp(inicio)) & (df["DataHora"] < pd.Timestamp(fim) + pd.Timedelta(days=1))]
    return df.reset_index(drop=True)


# =============================================================================
# 2. SAÍDAS: CSV POR ESTAÇÃO E GRÁFICO
# =============================================================================
def sem_acento(texto: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")


def salvar_csv(df: pd.DataFrame, codigo: str, nome: str, pasta: Path, inicio: date, fim: date) -> Path:
    """DATA;HORA;CHUVA_MM;NIVEL_CM;VAZAO_M3_S, da leitura mais recente para a mais antiga."""
    saida = pd.DataFrame({"DATA": df["DataHora"].dt.strftime("%d/%m/%Y"), "HORA": df["DataHora"].dt.strftime("%H:%M:%S"),
                          "CHUVA_MM": df["Chuva"], "NIVEL_CM": df["Cota"], "VAZAO_M3_S": df["Vazao"]})
    arquivo = pasta / f"ANA_{codigo}_{sem_acento(nome)}_15min_{inicio:%Y%m%d}_{fim:%Y%m%d}.csv"
    saida.iloc[::-1].to_csv(arquivo, sep=";", index=False, encoding="utf-8-sig")
    return arquivo


def gerar_grafico(series: dict[str, pd.Series], arquivo: Path) -> None:
    """Uma linha por estação (cota em cm), com visual limpo no estilo theme_minimal do ggplot2."""
    plt.rcParams["font.family"] = ["Arial", "DejaVu Sans"]
    nomes = sorted(series)                                               # legenda e cores em ordem alfabética
    paleta = PALETAS.get(len(nomes), plt.cm.tab10.colors)
    cores = dict(zip(nomes, paleta))
    ini = min(s.index.min() for s in series.values())
    fim = max(s.index.max() for s in series.values())
    dias = max((fim - ini).days, 1)

    fig, ax = plt.subplots(figsize=(12, 7))
    for nome in nomes:
        ax.plot(series[nome].index, series[nome].values, color=cores[nome], lw=2.1, solid_capstyle="round", zorder=3)
    ax.grid(True, which="major", color="#EBEBEB", lw=1.1)
    ax.grid(True, which="minor", color="#EBEBEB", lw=0.55)
    ax.set_axisbelow(True)
    for lado in ax.spines.values():
        lado.set_visible(False)
    ax.tick_params(which="both", length=0)
    ax.tick_params(axis="both", labelsize=11.2, labelcolor="#4D4D4D")
    ax.yaxis.set_major_locator(MaxNLocator(nbins=6, steps=[1, 2, 5, 10]))
    ax.yaxis.set_minor_locator(AutoMinorLocator(2))
    if dias <= 6:                                                        # poucos dias: rótulos com dia e hora
        ax.xaxis.set_major_locator(mdates.HourLocator(byhour=range(0, 24, 12 if dias > 2 else 6)))
        ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _p=None: f"{mdates.num2date(x):%d/%m %Hh}"))
    else:
        if dias <= 40:
            ax.xaxis.set_major_locator(mdates.DayLocator(interval=2 if dias <= 16 else 5))
            ax.xaxis.set_minor_locator(mdates.DayLocator())
        else:
            ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=9))
        ax.xaxis.set_major_formatter(plt.FuncFormatter(
            lambda x, _p=None: f"{MESES[mdates.num2date(x).month - 1]} {mdates.num2date(x).day:02d}"))
    fig.canvas.draw()
    for rotulo in ax.get_xticklabels():
        rotulo.set_rotation(45)
        rotulo.set_ha("right")
        rotulo.set_rotation_mode("anchor")
    ax.set_xlabel("Data", fontsize=14, labelpad=8)
    ax.set_ylabel("Cota (cm)", fontsize=14, labelpad=8)

    fig.text(0.057, 0.955, "Evolução da Cota a cada 15 min - Rio Xingu", fontsize=16.8, ha="left", va="center")
    fig.text(0.057, 0.915, f"Dados com data final em: {fim:%d/%m/%Y}", fontsize=14, ha="left", va="center")
    itens = [Line2D([], [], alpha=0)] + [Line2D([], [], color=cores[n], lw=2.1) for n in nomes]
    legenda = fig.legend(itens, ["Estação"] + [n.upper() for n in nomes], loc="center", bbox_to_anchor=(0.5, 0.855),
                         ncol=len(itens), frameon=False, fontsize=11.2, handlelength=2.2, columnspacing=1.2)
    legenda.get_texts()[0].set_fontsize(14)
    fig.text(0.057, 0.015, "Fonte: ANA - HidroWebService (telemetria adotada)", fontsize=9, color="#595959",
             style="italic", ha="left", va="bottom")
    fig.subplots_adjust(left=0.085, right=0.985, top=0.80, bottom=0.17)
    fig.savefig(arquivo, dpi=200, facecolor="white")
    plt.close(fig)


# =============================================================================
# 3. PRINCIPAL
# =============================================================================
def main() -> None:
    ap = argparse.ArgumentParser(description="Telemetria ANA a cada 15 min: CSV por estação + gráfico.")
    ap.add_argument("--inicio", default=DATA_INICIO, help="AAAA-MM-DD (padrão: 7 dias antes da data final)")
    ap.add_argument("--fim", default=DATA_FIM, help="AAAA-MM-DD (padrão: ontem)")
    ap.add_argument("--saida", default=str(PASTA_SAIDA), help="pasta de saída")
    ap.add_argument("--sem-grafico", action="store_true", help="só os CSVs")
    args = ap.parse_args()

    fim = date.fromisoformat(args.fim) if args.fim else date.today() - timedelta(days=1)
    inicio = date.fromisoformat(args.inicio) if args.inicio else fim - timedelta(days=7)
    if fim < inicio:
        raise SystemExit("A data final não pode ser anterior à data inicial.")
    pasta = Path(args.saida)
    pasta.mkdir(parents=True, exist_ok=True)
    print(f"Período: {inicio:%d/%m/%Y} a {fim:%d/%m/%Y} | {len(ESTACOES)} estações | saída: {pasta.resolve()}\n")

    credenciais = carregar_credenciais()
    token = [autenticar(*credenciais)]
    print("Autenticação realizada com sucesso.\n")

    series: dict[str, pd.Series] = {}
    for codigo, nome in ESTACOES:
        df = baixar_estacao(codigo, nome, inicio, fim, credenciais, token)
        if df.empty:
            print(f"  [!] {codigo} {nome}: nenhum dado retornado no período.")
            continue
        arquivo = salvar_csv(df, codigo, nome, pasta, inicio, fim)
        series[nome] = df.set_index("DataHora")["Cota"].dropna()
        print(f"  {codigo} {nome:<14} {len(df):>6} leituras -> {arquivo.name}")
    if not series:
        raise SystemExit("Nenhum dado retornado pela ANA para o período informado.")
    if not args.sem_grafico:
        grafico = pasta / f"grafico_cota_15min_{inicio:%Y%m%d}_{fim:%Y%m%d}.png"
        gerar_grafico(series, grafico)
        print(f"\nGráfico: {grafico.name}")
    print("\nConcluído.")


if __name__ == "__main__":
    main()
