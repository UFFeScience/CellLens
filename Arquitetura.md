### Arquitetura do Sistema: Pipeline ETL Biomarcadores (Knowledge Graph)

**Autor:** João Vitor Mendonça de Moraes

**Objetivo:** Construir um pipeline automatizado, reprodutível e resiliente para extração de literatura do PubMed, inferência estruturada via LLM e consolidação em um Grafo de Conhecimento (Neo4j).

#### 1. Decisões de Engenharia e Componentes (Visão Docker)

O sistema foi modularizado para garantir isolamento de processos e tolerância a falhas.

* **Apache Airflow (Orquestrador):** Atua estritamente como coordenador (Control Plane). Utiliza a TaskFlow API com Mapeamento Dinâmico (Dynamic Task Mapping) para paralelizar tarefas em lotes sem trafegar dados massivos em sua infraestrutura nativa (XCom).
* **PostgreSQL (Metadata & Cache):** Dividido logicamente em *schemas* distintos para evitar corrupção:
* `airflow`: Uso exclusivo do orquestrador.
* `pubmed_cache`: Tabelas baseadas em JSONB (`api_responses` e `llm_responses`) que funcionam como cache transacional e idempotente, evitando redundância de requisições de rede e custos de API.


* **Neo4j (Grafo de Conhecimento):** Banco de destino. Recebe inserções em lote utilizando o comando `UNWIND` do Cypher, garantindo alta performance e prevenção contra injeção de código via listas de permissões (Allowlist) de relacionamentos.
* **Google Gemini (Extração Estruturada):** O LLM foi definido para o `gemini-1.5-flash` via Google AI Studio, aproveitando a cota gratuita programática. Utiliza a funcionalidade *Structured Outputs* para garantir que as respostas sejam devolvidas em JSON estrito, sem necessidade de *parsers* complexos.

#### 2. Modelagem de Dados e Proveniência (Data Contracts)

A arquitetura abandona dicionários soltos em Python e implementa Contratos de Dados rigorosos utilizando `pydantic` (`scripts/models.py`). Toda informação gerada carrega rastreabilidade retrospectiva obrigatória (Proveniência):

* Sistema de origem (ex: PubMed Entrez).
* ID único de origem (PMID).
* Modelo lógico de extração (ex: gemini-1.5-flash).
* Timestamp da extração.

#### 3. Fluxo do Pipeline (DAG: pubmed_knowledge_graph_pipeline)

O DAG é composto por 4 estágios principais:

1. **`read_and_batch_data`**: Lê os dados brutos de entrada (`genes_marcadores_mestrado.csv`), realiza a desduplicação lógica considerando a tupla (Gene, Cluster) e os fraciona em lotes menores (arquivos JSON temporários), retornando apenas os identificadores (IDs) dos lotes para o Airflow.
2. **`fetch_pubmed_articles`**: Executa o mapeamento dinâmico sobre os lotes. Realiza consultas otimizadas ao NCBI (PubMed) via Biopython. Implementa controle interno de taxa (Rate Limit de ~3 req/s) e retentativas exponenciais (`tenacity`), além de consultar o cache no PostgreSQL antes de qualquer requisição externa.
3. **`extract_knowledge_llm`**: Lê os resumos (abstracts) brutos do disco, consulta o banco de cache do LLM e, em caso de *miss*, constrói os *prompts* injetando os esquemas Pydantic para o modelo Gemini. Salva as relações semânticas em disco.
4. **`load_to_graph_db`**: Consolida os JSONs finais e executa transações de `MERGE` em lote no Neo4j, gerando nós (`Gene`, `Cluster`, `Article`) e suas respectivas arestas biológicas com a linhagem de dados anexada como propriedades.

#### 4. Estrutura de Diretórios Atualizada

```text
Biomarcadores/
├── dags/
│   ├── pubmed_kg_pipeline.py      # DAG principal do Airflow
│   └── scripts/
│       ├── data_processor.py      # Lógica de lotes e desduplicação
│       ├── pubmed_client.py       # Integração NCBI, Tenacity e Cache SQL
│       ├── llm_processor.py       # Integração Gemini, Structured Outputs e Cache SQL
│       ├── neo4j_loader.py        # Consolidação Cypher (UNWIND)
│       ├── models.py              # Contratos Pydantic e Proveniência
│       └── logger_config.py       # Padronização de logs para o stdout
├── data/
│   ├── raw/
│   │   └── genes_marcadores_mestrado.csv
│   └── intermediate/
│       ├── batches/               # Lotes gerados pelo data_processor
│       ├── raw_pubmed/            # Textos brutos gerados pelo pubmed_client
│       └── processed/             # JSONs estruturados pelo llm_processor
├── .env                           # Chaves (GEMINI_API_KEY, NEO4J_PASSWORD, etc)
├── .gitignore
├── docker-compose.yml             # Declaração dos containers (Airflow, Postgres, Neo4j)
├── Dockerfile                     # Imagem do Airflow com instalação de pacotes via uv
├── requirements.txt               # pandas, neo4j, biopython, pydantic, tenacity, google-generativeai, sqlalchemy
└── README.md

```
