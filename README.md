# CellLens+

`CellLens+` is a workflow for automated, interpretable cell-type annotation
in single-cell RNA sequencing (scRNA-seq) data. It combines graph-based
community detection, a deterministic quantitative scoring engine, and a
post-hoc literature-based semantic interpretation layer (LLM) that explains
— but never decides — the predicted cell identities. See
[`Arquitetura.md`](Arquitetura.md) for the full pipeline architecture and
[`Prompts/`](Prompts/) for every prompt sent to the LLM, with real worked
examples.

### Pré-requisitos de Execução

Antes de iniciar os contêineres, é estritamente necessário garantir que o ambiente local possua os artefatos de entrada e as credenciais configuradas corretamente, sob pena de falha imediata das tarefas.

1. **Arquivo de Entrada**: Certifique-se de que o arquivo `genes_marcadores_mestrado.csv` está presente no diretório `data/raw/`. A ausência deste arquivo causará falha na primeira tarefa (`read_and_batch_data`).
2. **Variáveis de Ambiente**: Copie `.env.example` para `.env` e preencha os valores, com especial atenção para `GEMINI_API_KEY` e `NEO4J_PASSWORD`.

### Fase 1: Inicialização da Infraestrutura (Docker)

A infraestrutura utiliza o Docker Compose. A inicialização deve ser feita em duas etapas para garantir que o banco de metadados do Airflow (PostgreSQL) seja populado corretamente antes de os serviços subirem.

Abra o terminal na raiz do projeto (`CellLens/`) e execute:

**Passo 1: Inicialização do Banco do Airflow**

```bash
docker compose up airflow-init

```

*Análise de comportamento esperado:* Este comando executará as migrações do banco de dados do Airflow e criará o usuário administrador. Aguarde até que o terminal exiba a mensagem indicando que o contêiner `airflow-init` foi concluído com sucesso (exit code 0). Se retornar erro, verifique se a porta 5432 não está ocupada no seu *host*.

**Passo 2: Subida dos Serviços**

```bash
docker compose up -d

```

*Análise de comportamento esperado:* Os serviços `postgres`, `neo4j`, `airflow-webserver` e `airflow-scheduler` serão iniciados em segundo plano.

### Fase 2: Execução e Monitoramento no Airflow

1. Acesse a interface web do Airflow no seu navegador: `http://localhost:8080`.
2. Autentique-se com as credenciais padrão definidas no `docker-compose.yml`:
* **Usuário:** `admin`
* **Senha:** `admin`


3. Na página inicial (DAGs), localize `pubmed_knowledge_graph_pipeline`.
4. Remova a pausa do DAG clicando no botão de alternância (toggle) ao lado do nome, ativando-o.
5. Clique no botão de "Play" (Trigger DAG) no canto superior direito da linha correspondente para iniciar a execução.

**Como testar e monitorar (Troubleshooting):**

* Clique no nome do DAG e acesse a aba **Graph** ou **Grid**. Você verá as tarefas mudando de estado (cinza para verde claro, depois verde escuro se sucesso, ou vermelho se falha).
* Devido ao Mapeamento Dinâmico, as tarefas `fetch_pubmed_articles` e `extract_knowledge_llm` exibirão colchetes `[ ]` indicando múltiplas instâncias rodando em paralelo para os lotes.
* Se alguma tarefa falhar, clique no quadrado correspondente e selecione a aba **Logs**. O logger padronizado que implementamos (`logger_config.py`) indicará exatamente a causa (ex: falha de rede, timeout da API do NCBI, ou problema de chave do Gemini).

### Fase 3: Validação do Banco de Grafos (Neo4j)

A prova definitiva de que o pipeline funcionou corretamente é a presença dos dados estruturados no banco de grafos.

1. Acesse a interface web do Neo4j no navegador: `http://localhost:7474`.
2. Autentique-se:
* **Connect URL:** `neo4j://localhost:7687` (ou deixe o padrão)
* **Username:** `neo4j`
* **Password:** A senha que você definiu na variável `NEO4J_PASSWORD` no arquivo `.env`.


3. No prompt de comando Cypher (na parte superior da interface), execute as seguintes queries de teste analítico:

**Teste A: Verificação de Volume e Criação Básica**

```cypher
MATCH (n) RETURN labels(n) AS Tipo, count(n) AS Quantidade;

```

*Objetivo:* Validar se os nós `Gene`, `Cluster` e `Article` foram criados e qual a volumetria extraída.

**Teste B: Inspeção de Relacionamentos e Proveniência**

```cypher
MATCH (g:Gene)-[r]->(c:Cluster)
RETURN g.name AS Gene, type(r) AS Relacao, c.name AS Celula, r.evidence AS Evidencia, r.llm_model AS Modelo_LLM
LIMIT 20;

```

*Objetivo:* Validar se as arestas foram criadas utilizando a Allowlist, se o *snippet* de evidência foi extraído pelo LLM e se o metadado de proveniência (modelo) foi injetado corretamente na propriedade da aresta.

### Fase 4: Exploração dos Resultados (Frontend Streamlit)

Após a conclusão do pipeline, os artefatos finais (`final_cluster_annotations.json`) podem ser explorados visualmente pela interface `BioQuest`, em `frontend/app.py`.

```bash
pip install -r requirements.txt
streamlit run frontend/app.py
```

Informe, no campo de texto da interface, o caminho para o `final_cluster_annotations.json` gerado (por exemplo, `data/genes_marcadores_mestrado/result/final_cluster_annotations.json`, ou um dos artefatos de exemplo já incluídos em `Result_genes_marcadores_mestrado_PBMC/` e `Result_Cancer/result/`). A interface oferece duas visões complementares: **Cluster-Cêntrica** (identidade prevista, confiança, marcadores de suporte e hipóteses alternativas) e **Gene-Cêntrica** (distribuição de um gene marcador entre os clusters).

### Ponto de Atenção para o Primeiro Teste

Do ponto de vista analítico de performance, se o seu arquivo CSV original possuir milhares de registros, a primeira execução completa poderá levar várias horas devido ao controle estrito de 3 requisições por segundo exigido pela API do PubMed (NCBI).

**Sugestão arquitetural para o teste inicial:** Para validar o fluxo de ponta a ponta de forma ágil, reduza o `genes_marcadores_mestrado.csv` temporariamente para apenas 5 ou 10 linhas. Após confirmar que os dados chegam ao Neo4j corretamente, retorne o arquivo original completo; o banco de cache em PostgreSQL garantirá que as consultas já testadas não consumam rede ou tempo adicional na reexecução.