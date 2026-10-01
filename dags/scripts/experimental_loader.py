import os
import pandas as pd
from neo4j import GraphDatabase
from scripts.logger_config import get_pipeline_logger
from scripts.neo4j_loader import ensure_graph_constraints

logger = get_pipeline_logger(__name__)

def load_experimental_data(csv_path: str):
    """
    Le os dados experimentais brutos (scRNA-seq) do CSV e carrega no Neo4j,
    estabelecendo a relacao quantitativa entre Gene e Cluster.
    """
    uri = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD")

    # Primeira carga do pipeline: garante as constraints antes de qualquer MERGE,
    # para o grafo nunca acumular nós duplicados (Gene/Cluster).
    ensure_graph_constraints()

    logger.info(f"Iniciando leitura dos dados experimentais em: {csv_path}")
    
    try:
        df = pd.read_csv(csv_path)
        df = df.rename(columns={
            'names': 'gene',
            'group': 'cluster',
            'logfoldchanges': 'logFC',
            'pvals': 'pval',
            'pvals_adj': 'pval_adj',
            'scores': 'score'
        })
    except Exception as e:
        logger.error(f"Falha ao ler o arquivo CSV bruto: {e}")
        raise

    expected_cols = ['gene', 'cluster', 'logFC', 'pval', 'pval_adj', 'score']
    for col in expected_cols:
        if col not in df.columns:
            logger.warning(f"Coluna esperada '{col}' ausente no CSV. Propriedades nulas serao geradas.")
            df[col] = None

    records = df.to_dict(orient='records')

    cypher_query = """
    UNWIND $batch AS record
    
    
    MERGE (g:Gene {name: toUpper(record.gene)})
    MERGE (c:Cluster {name: toUpper(record.cluster)})
    
    MERGE (g)-[r:DIFFERENTIALLY_EXPRESSED_IN]->(c)
    ON CREATE SET 
        r.logFC = toFloat(record.logFC),
        r.pval = toFloat(record.pval),
        r.pval_adj = toFloat(record.pval_adj),
        r.score = toFloat(record.score)
    ON MATCH SET 
        r.logFC = toFloat(record.logFC),
        r.pval = toFloat(record.pval),
        r.pval_adj = toFloat(record.pval_adj),
        r.score = toFloat(record.score)
    """

    try:
        with GraphDatabase.driver(uri, auth=(user, password)) as driver:
            with driver.session() as session:
                session.run(cypher_query, batch=records)
        logger.info(f"Carga experimental concluida. {len(records)} arestas processadas.")
    except Exception as e:
        logger.error(f"Falha na transacao Cypher ao carregar dados experimentais: {e}")
        raise