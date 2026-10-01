import os
from sqlalchemy import create_engine, text

class DbManager():
    def __init__(self):
        
        DB_CONN_URI = os.getenv("AIRFLOW__DATABASE__SQL_ALCHEMY_CONN", "postgresql+psycopg2://airflow:airflow@postgres/airflow")
        self.engine = create_engine(DB_CONN_URI)
        self.text = text

    def init_cache_db(self, table: str = 'api_cache'):
        """
        Garante a existência do schema isolado e da tabela de cache no PostgreSQL.
        Usar JSONB para otimizar a leitura de dados não-estruturados no PostgreSQL.

        Os nomes das colunas de chave/texto variam por tabela para casar com o
        consumidor:
          - api_responses -> query_hash / query_text   (pubmed_client)
          - llm_responses -> prompt_hash / prompt_text  (llm_processor)
        """
        key_col, text_col = (
            ("query_hash", "query_text")
            if table == "api_responses"
            else ("prompt_hash", "prompt_text")
        )
        with self.engine.begin() as conn:
            conn.execute(text("SELECT pg_advisory_xact_lock(915623);"))
            conn.execute(text("CREATE SCHEMA IF NOT EXISTS pubmed_cache;"))
            conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS pubmed_cache.{table} (
                    {key_col} TEXT PRIMARY KEY,
                    {text_col} TEXT,
                    response_json JSONB,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """))
