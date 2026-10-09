# Telemetria ANA a cada 15 min — rio Xingu

Script em Python que baixa, pelo **HidroWebService da ANA**, as leituras de 15 minutos (cota, chuva e vazão) das estações
**Altamira, Pedra do Ó, Boa Esperança e Boa Sorte** (bacia do rio Xingu, PA) e gera:

- um **CSV por estação** (`DATA;HORA;CHUVA_MM;NIVEL_CM;VAZAO_M3_S`, da leitura mais recente para a mais antiga);
- um **gráfico** com a evolução da cota das estações (`exemplo_grafico.png`).

## Como usar

```bash
pip install -r requirements.txt
python telemetria_ana_15min.py                                      # últimos 7 dias, até ontem
python telemetria_ana_15min.py --inicio 2026-10-01 --fim 2026-10-07
```

**Credenciais:** é preciso ter cadastro no HidroWebService da ANA (identificador e senha). Elas **não** ficam no código:
use as variáveis de ambiente `ANA_IDENTIFICADOR` e `ANA_SENHA`, ou copie `credenciais_ana.exemplo.json` para
`credenciais_ana.json` e preencha, ou deixe o script perguntar no terminal.

**Outras estações:** edite a lista `ESTACOES` no topo do script (código ANA, nome).

## Observações

- O serviço devolve 30 dias por consulta; o script divide o período em blocos e renova o token se ele expirar.
- São dados de telemetria (cota adotada), sujeitos a revisão pela ANA. Cite a fonte: ANA / Rede Hidrometeorológica Nacional.
- Os dados a cada 15 min mostram também a oscilação de maré em estações próximas à foz; este script não aplica filtro algum.
