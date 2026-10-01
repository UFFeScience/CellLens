import os
import json
from neo4j import GraphDatabase
from typing import List, Dict, Any

from scripts.logger_config import get_pipeline_logger

logger = get_pipeline_logger(__name__)

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

ALLOWED_RELATIONS = {"EXPRESSED_IN", "ASSOCIATED_WITH", "MARKER_FOR", "REGULATES"}

def ensure_graph_constraints() -> None:
    """Garante as constraints de unicidade (idempotente).

    Sem elas, o MERGE do Neo4j NÃO garante unicidade sob transações
    concorrentes ou re-execuções — e o grafo acumula nós duplicados
    (Gene/Cluster) com o mesmo nome. Deve rodar antes de qualquer carga.
    """
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        with driver.session() as session:
            session.run("CREATE CONSTRAINT gene_name IF NOT EXISTS FOR (g:Gene) REQUIRE g.name IS UNIQUE")
            session.run("CREATE CONSTRAINT cluster_name IF NOT EXISTS FOR (c:Cluster) REQUIRE c.name IS UNIQUE")
            session.run("CREATE CONSTRAINT article_pmid IF NOT EXISTS FOR (a:Article) REQUIRE a.pmid IS UNIQUE")
        logger.info("Constraints de unicidade garantidas (Gene.name, Cluster.name, Article.pmid).")
    finally:
        driver.close()

def load_processed_files_to_neo4j(processed_files: List[str]) -> None:
    """
    Lê uma lista de caminhos de arquivos JSON contendo as extrações do LLM
    e as carrega no Neo4j utilizando UNWIND para inserção em lote (bulk).
    """
    if not processed_files:
        logger.info("Nenhum arquivo processado recebido para carregar no Neo4j.")
        return

    ensure_graph_constraints()

    all_relations = []
    for file_path in processed_files:
        if not os.path.exists(file_path):
            logger.warning(f"Arquivo não encontrado para carregamento: {file_path}")
            continue
            
        with open(file_path, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
                all_relations.extend(data)
            except json.JSONDecodeError as e:
                logger.error(f"Erro ao ler JSON {file_path}: {e}")

    if not all_relations:
        logger.info("Nenhum dado válido extraído para carregar.")
        return

    batches_by_type: Dict[str, List[Dict[str, Any]]] = {}
    
    for rel in all_relations:
        rel_type = rel.get("relation_type", "").upper()
        
        if rel_type not in ALLOWED_RELATIONS:
            logger.warning(f"Tipo de relação ignorada (não permitida): {rel_type}")
            rel_type = "ASSOCIATED_WITH" 
            
        if rel_type not in batches_by_type:
            batches_by_type[rel_type] = []
            
        batches_by_type[rel_type].append(rel)

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    
    try:
        with driver.session() as session:
            for rel_type, batch in batches_by_type.items():
                logger.info(f"Carregando {len(batch)} relações do tipo {rel_type} no Neo4j...")
                
                # A query utiliza UNWIND para processar a lista inteira de uma vez.
                # O MERGE garante a idempotência (não duplica nós ou arestas se o DAG rodar novamente).
                cypher_query = f"""
                UNWIND $batch AS record
                
                // Criação/Garantia dos Nós
                MERGE (g:Gene {{name: toUpper(record.gene)}})
                MERGE (c:Cluster {{name: toUpper(record.cell_group)}})
                MERGE (a:Article {{pmid: record.provenance.source_id}})
                ON CREATE SET 
                    a.source_system = record.provenance.source_system,
                    a.doi = record.provenance.doi,
                    a.year = toInteger(record.provenance.year)
                
                // Relacionamento extraído
                MERGE (g)-[r:{rel_type}]->(c)
                ON CREATE SET 
                    r.evidence = record.evidence_snippet,
                    r.llm_model = record.provenance.llm_model,
                    r.extraction_date = record.provenance.extraction_date,
                    r.pmid_source = record.provenance.source_id,
                    r.pm_link = 'https://pubmed.ncbi.nlm.nih.gov/' + record.provenance.source_id + '/'
                    
                // Rastreabilidade estrutural do artigo
                MERGE (a)-[:MENTIONS]->(g)
                MERGE (a)-[:MENTIONS]->(c)
                """
                
                session.run(cypher_query, batch=batch)
                
        logger.info("Carregamento no Neo4j concluído com sucesso.")
    except Exception as e:
        logger.error(f"Falha crítica ao executar transação no Neo4j: {e}")
        raise
    finally:
        driver.close()
        
def load_raw_articles_to_neo4j(raw_files: List[str]) -> None:
    """
    Lê os JSONs brutos do PubMed e cria os nós de Artigo e as arestas MENTIONS
    garantindo que o citation_count_marker seja computado corretamente.
    """
    if not raw_files:
        return

    ensure_graph_constraints()

    all_raw = []
    for file_path in raw_files:
        if not os.path.exists(file_path):
            continue
        with open(file_path, 'r', encoding='utf-8') as f:
            try:
                all_raw.extend(json.load(f))
            except json.JSONDecodeError:
                pass

    if not all_raw:
        return

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        with driver.session() as session:
            logger.info(f"Carregando {len(all_raw)} artigos brutos no Neo4j (Geração de [:MENTIONS])...")
            
            cypher_query = """
            UNWIND $batch AS record
            
            MERGE (g:Gene {name: toUpper(record.gene_target)})
            MERGE (c:Cluster {name: toUpper(record.cluster_target)})
            
            MERGE (a:Article {pmid: record.provenance.source_id})
            ON CREATE SET
                a.title = record.title,
                a.year = toInteger(record.year),
                a.doi = record.doi,
                a.source_system = record.provenance.source_system
            
            MERGE (a)-[:MENTIONS]->(g)
            MERGE (a)-[:MENTIONS]->(c)
            """
            session.run(cypher_query, batch=all_raw)
            logger.info("Carga de artigos brutos concluída.")
    except Exception as e:
        logger.error(f"Falha na transação de artigos brutos: {e}")
        raise
    finally:
        driver.close()