import os
import requests
from neo4j import GraphDatabase
from scripts.logger_config import get_pipeline_logger

logger = get_pipeline_logger(__name__)

def enrich_gene_metadata():
    """
    Consulta os genes unicos no Neo4j e utiliza a API MyGene.info
    para popular os atributos 'aliases' e 'gene_type'.
    """
    uri = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD")

    fetch_query = "MATCH (g:Gene) RETURN g.name AS gene"
    genes = []
    
    try:
        with GraphDatabase.driver(uri, auth=(user, password)) as driver:
            with driver.session() as session:
                result = session.run(fetch_query)
                genes = [record["gene"] for record in result]
    except Exception as e:
        logger.error(f"Erro ao recuperar genes do grafo: {e}")
        raise

    if not genes:
        logger.warning("Nenhum gene encontrado para enriquecimento. Abortando tarefa.")
        return

    logger.info(f"Iniciando enriquecimento para {len(genes)} genes.")

    url = "https://mygene.info/v3/query"
    batch_size = 1000 
    enriched_data = []

    for i in range(0, len(genes), batch_size):
        batch_genes = genes[i:i + batch_size]
        payload = {
            "q": ",".join(batch_genes),
            "scopes": "symbol",
            "fields": "alias,type_of_gene",
            "species": "human"
        }
        
        try:
            response = requests.post(url, data=payload)
            response.raise_for_status()
            data = response.json()
            
            for item in data:
                if "query" in item:
                    gene_symbol = item["query"]
                    
                    aliases = item.get("alias", [])
                    if isinstance(aliases, str):
                        aliases = [aliases]
                        
                    gene_type = item.get("type_of_gene", "unknown")
                    
                    enriched_data.append({
                        "gene": gene_symbol.upper(),
                        "aliases": aliases,
                        "gene_type": gene_type
                    })
                    
        except requests.exceptions.RequestException as e:
            logger.error(f"Erro de rede ao consultar MyGene.info no lote {i}: {e}")
        except Exception as e:
            logger.error(f"Erro inesperado no parse do MyGene.info: {e}")

    if not enriched_data:
        logger.error("Nenhum metadado biológico retornado pela API. O grafo não será atualizado.")
        return

    update_query = """
    UNWIND $batch AS record
    MATCH (g:Gene {name: record.gene})
    SET g.aliases = record.aliases,
        g.gene_type = record.gene_type
    """

    try:
        with GraphDatabase.driver(uri, auth=(user, password)) as driver:
            with driver.session() as session:
                session.run(update_query, batch=enriched_data)
        logger.info("Enriquecimento de metadados aplicado com sucesso aos nós Gene.")
    except Exception as e:
        logger.error(f"Erro ao persistir atributos de enriquecimento no Neo4j: {e}")
        raise