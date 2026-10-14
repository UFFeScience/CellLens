import os
import json
import time
from typing import List
from Bio import Entrez
from tenacity import retry, stop_after_attempt, wait_exponential
from scripts.logger_config import get_pipeline_logger
from scripts.utils.models import ArticleRaw, ProvenanceMetadata
from scripts.utils.database import DbManager
import hashlib

logger = get_pipeline_logger(__name__)

Entrez.email = os.getenv("NCBI_EMAIL", "pipeline_bioinfo@domain.com")
ncbi_api_key = os.getenv("NCBI_API_KEY")
if ncbi_api_key:
    Entrez.api_key = ncbi_api_key

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def search_pubmed(query: str, max_results: int = 5) -> List[dict]:
    """
    Busca no PubMed utilizando Biopython com controle de taxa interno e retentativas.
    """
    time.sleep(0.1) 
    
    logger.info(f"Executando ESearch para: '{query}'")
    handle = Entrez.esearch(db="pubmed", term=query, retmax=max_results)
    record = Entrez.read(handle)
    handle.close()

    id_list = record.get("IdList", [])
    citation_count = int(record.get("Count", 0))
    if not id_list:
        return [], citation_count

    time.sleep(0.1)
    
    logger.info(f"Executando EFetch para {len(id_list)} artigos...")
    fetch_handle = Entrez.efetch(db="pubmed", id=",".join(id_list), retmode="xml")
    articles = Entrez.read(fetch_handle)
    fetch_handle.close()

    results = []
    for article in articles.get("PubmedArticle", []):
        try:
            medline = article["MedlineCitation"]
            pmid = str(medline["PMID"])
            article_data = medline["Article"]
            title = article_data.get("ArticleTitle", "")
            
            pub_date = article_data.get("Journal", {}).get("JournalIssue", {}).get("PubDate", {})
            pub_year = pub_date.get("Year", "")
            
            doi = ""
            article_id_list = article.get("PubmedData", {}).get("ArticleIdList", [])
            for article_id in article_id_list:
                if article_id.attributes.get("IdType") == "doi":
                    doi = str(article_id)
                    break
            
            abstract_texts = article_data.get("Abstract", {}).get("AbstractText", [])
            abstract = " ".join(str(text) for text in abstract_texts) if abstract_texts else ""
            
            if abstract: 
                results.append({
                    "pmid": pmid,
                    "title": title,
                    "abstract": abstract,
                    "year": pub_year,
                    "doi": doi       
                })
        except Exception as e:
            logger.warning(f"Falha ao extrair dados do artigo XML: {e}")
            
    return results, citation_count

def fetch_articles_for_batch(batch_id: str, input_dir: str, output_dir: str) -> str:
    """
    Lê um lote de marcadores, busca literatura associada e salva em JSON.
    """
    batch_file = os.path.join(input_dir, f"{batch_id}.json")
    output_file = os.path.join(output_dir, f"raw_pubmed_{batch_id}.json")

    if not os.path.exists(batch_file):
        raise FileNotFoundError(f"Arquivo de lote não encontrado: {batch_file}")

    os.makedirs(output_dir, exist_ok=True)

    with open(batch_file, 'r', encoding='utf-8') as f:
        records = json.load(f)

    all_raw_articles = []
    queries_tentadas = 0
    queries_falhas = 0

    try:
        db = DbManager()
        db.init_cache_db(table='api_responses')
    except Exception as e:
        logger.error(f"Falha crítica ao inicializar o banco de cache: {e}")
        raise
    
    with db.engine.connect() as db_conn:
        for record in records:
            gene = record.get("gene")
            cluster = record.get("cluster")
            
            if not gene or not cluster:
                logger.warning(f"Registro inválido, ignorado: {record}")
                continue

            cluster_tokens = " AND ".join(cluster.split())
            query = f'("{gene}"[Gene] OR "{gene}") AND ({cluster_tokens})'
            query_hash = hashlib.md5(query.encode('utf-8')).hexdigest()
            fetched_data = None
            queries_tentadas += 1

            try:
                cache_result = db_conn.execute(
                    db.text("SELECT response_json FROM pubmed_cache.api_responses WHERE query_hash = :q_hash"),
                    {"q_hash": query_hash}
                ).fetchone()

                if cache_result:
                    logger.info(f"Cache HIT para query: '{query}'")
                    raw_cache = cache_result[0]
                    
                    cache_payload = json.loads(raw_cache) if isinstance(raw_cache, str) else raw_cache
                    
                    if isinstance(cache_payload, list):
                        fetched_data = cache_payload
                        citation_count = 0 
                    elif isinstance(cache_payload, dict):
                        fetched_data = cache_payload.get("data", [])
                        citation_count = cache_payload.get("count", 0)
                    else:
                        fetched_data = []
                        citation_count = 0
                else:
                    logger.info(f"Cache MISS para query: '{query}'. Consultando API...")
                    fetched_data, citation_count = search_pubmed(query)
                    
                    cache_payload = {"data": fetched_data, "count": citation_count}
                    db_conn.execute(
                        db.text("""
                            INSERT INTO pubmed_cache.api_responses (query_hash, query_text, response_json)
                            VALUES (:q_hash, :q_text, :r_json)
                            ON CONFLICT (query_hash) DO NOTHING;
                        """),
                        {"q_hash": query_hash, "q_text": query, "r_json": json.dumps(cache_payload)}
                    )
                    db_conn.commit()

                if not isinstance(fetched_data, list):
                    fetched_data = [fetched_data] if fetched_data else []

                for item in fetched_data:
                    if isinstance(item, list) and len(item) > 0:
                        item = item[0]
                        
                    if not isinstance(item, dict):
                        logger.warning(f"Estrutura inesperada encontrada no cache: {type(item)}")
                        continue

                    prov = ProvenanceMetadata(
                        source_system="PubMed Entrez",
                        source_id=item.get("pmid", "UNKNOWN"),
                        query_used=query
                    )
                    
                    raw_article = ArticleRaw(
                        gene_target=gene,
                        cluster_target=cluster,
                        title=item["title"],
                        abstract=item["abstract"],
                        provenance=prov,
                        year=item["year"],
                        doi=item["doi"],
                        citation_count_marker=citation_count
                    )
                    
                    dumped_model = raw_article.model_dump() if hasattr(raw_article, 'model_dump') else raw_article.dict()
                    all_raw_articles.append(dumped_model)
                    
            except Exception as e:
                queries_falhas += 1
                logger.error(f"Falha total ao processar query '{query}': {e}")

    if queries_tentadas > 0 and queries_falhas == queries_tentadas:
        raise RuntimeError(
            f"Lote {batch_id}: todas as {queries_tentadas} queries falharam. "
            f"Abortando a tarefa para não propagar saída vazia como sucesso."
        )

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(all_raw_articles, f, ensure_ascii=False, indent=2)

    logger.info(f"Processamento do lote {batch_id} concluído. {len(all_raw_articles)} artigos extraídos.")
    return output_file