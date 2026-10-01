import os, sys
from datetime import datetime, timedelta
from airflow import DAG
from airflow.decorators import task
from scripts.data_processor import process_and_batch_data
from scripts.pubmed_client import fetch_articles_for_batch
from scripts.logger_config import get_pipeline_logger
from scripts.llm_processor import process_llm_extraction
from scripts.neo4j_loader import load_processed_files_to_neo4j,load_raw_articles_to_neo4j
from scripts.experimental_loader import load_experimental_data
from scripts.gene_enricher import enrich_gene_metadata
from scripts.annotation_engine import run_annotation_engine
from scripts.synthesis_llm import generate_cluster_justifications

logger = get_pipeline_logger(__name__)
sys.path.insert(0, '/opt/airflow')

default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'retries': 3,
    'retry_delay': timedelta(minutes=2),
}
project = 'genes_marcadores_mestrado'

with DAG(
    dag_id='pubmed_knowledge_graph_pipeline',
    default_args=default_args,
    description='Pipeline ETL para extração de literatura do PubMed e geração de Knowledge Graph',
    start_date=datetime(2026, 3, 1),
    catchup=False,
    tags=['bioinformatics', 'pubmed', 'llm', 'neo4j'],
) as dag:
    

    @task
    def read_and_batch_data(
            project: str,
            input_csv: str = '' ,
            output_dir: str = '' ,
            batch_size: int = 50
        ) -> list[str]:
        """ _Ingestao dos dados_
        
        - Lê o arquivo CSV bruto, remove duplicatas e particiona os dados em lotes.
        
        Esta tarefa lê os dados experimentais de um caminho fixo, divide os 
        registros em arquivos JSON menores para paralelização e salva no 
        diretório intermediário.
        
        Args:
            project (str): 
            input_csv (str): 
            output_dir (str): 
            batch_size (int): 
        
        Returns:
            batch_ids (list[str]): Lista contendo os identificadores (batch_ids) dos lotes gerados.
            
        """
        input_csv = f"/opt/airflow/data/raw/{project}.csv"
        output_dir = f"/opt/airflow/data/{project}/batches"
        
        batch_ids = process_and_batch_data(
            input_csv_path=input_csv,
            output_dir=output_dir,
            batch_size=50
        )
        return batch_ids

    @task(pool='pubmed_api_pool')
    def fetch_pubmed_articles(batch_id: str) -> str:
        """
        Busca artigos no PubMed associados aos genes e clusters de um lote específico.

        Args:
            batch_id (str): Identificador do lote gerado na etapa de ingestão.

        Returns:
            str: Caminho absoluto do arquivo JSON contendo os artigos brutos extraídos.
        """
        input_dir = f"/opt/airflow/data/{project}/batches"
        output_dir = f"/opt/airflow/data/{project}/raw_pubmed"
        
        output_file_path = fetch_articles_for_batch(
            batch_id=batch_id,
            input_dir=input_dir,
            output_dir=output_dir
        )
        return output_file_path

    @task(pool='gemini_api_pool')
    def extract_knowledge_llm(raw_file_path: str) -> str:

        output_dir = f"/opt/airflow/data/{project}/processed"
        output_file_path = process_llm_extraction(
            raw_file_path=raw_file_path,
            output_dir=output_dir,
            model_version="gemini-2.5-flash" 
        )
        return output_file_path

    @task
    def load_to_graph_db(processed_files: list[str]):
        
        
        logger = get_pipeline_logger("load_to_graph_db_task")
        
        valid_files = [f for f in processed_files if f is not None]
        
        logger.info(f"Iniciando consolidação de {len(valid_files)} lotes no Neo4j.")
        
        load_processed_files_to_neo4j(valid_files)

    @task
    def load_experimental_graph_task():
        csv_path = f"/opt/airflow/data/raw/{project}.csv"
        load_experimental_data(csv_path)

    @task
    def enrich_genes_task():
        enrich_gene_metadata()
    
    @task
    def run_analytical_engine_task():
        output_dir = f"/opt/airflow/data/{project}/result"
        run_annotation_engine(base_out_dir=output_dir)
    
    @task
    def load_raw_to_graph_db(raw_files_list: list[str]):
        valid_files = [f for f in raw_files_list if f is not None]
        load_raw_articles_to_neo4j(valid_files)
    
    @task
    def generate_synthesis_task():
        output_dir = f"/opt/airflow/data/{project}/result"
        generate_cluster_justifications(base_out_dir=output_dir)
        
   
    
    # 1 e 2. Carrega a fundação experimental e enriquece metadados
    experimental_task = load_experimental_graph_task()
    enrichment_task = enrich_genes_task()
    
    # 3. Gera a lista de lotes
    batches = read_and_batch_data(project=project)
    
    # Dependências: O batch só roda após o grafo experimental estar pronto e enriquecido
    experimental_task >> enrichment_task >> batches
    
    # 4. Expande dinamicamente a tarefa de busca para cada lote
    raw_files = fetch_pubmed_articles.expand(batch_id=batches)
    
    # 5. Expande dinamicamente a tarefa de extração
    processed_jsons = extract_knowledge_llm.expand(raw_file_path=raw_files)
    
    # 6. Consolida todos os resultados
    raw_graph_load_task = load_raw_to_graph_db(raw_files)
    graph_load_task = load_to_graph_db(processed_jsons)
    
    analytical_task = run_analytical_engine_task()
    
    synthesis_task = generate_synthesis_task()
    
    # O Motor Analítico só roda após o grafo estar populado com os textos e com as relações semânticas
    [raw_graph_load_task, graph_load_task] >> analytical_task >> synthesis_task