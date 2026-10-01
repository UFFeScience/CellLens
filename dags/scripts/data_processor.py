import os
import pandas as pd
import json
import math
from scripts.logger_config import get_pipeline_logger

logger = get_pipeline_logger(__name__)

def process_and_batch_data(input_csv_path: str, output_dir: str, batch_size: int = 50) -> list[str]:
    """
    Lê o CSV de genes marcadores, remove duplicatas e gera lotes menores.
    Retorna uma lista de identificadores (IDs) dos lotes gerados.
    """
    if not os.path.exists(input_csv_path):
        logger.error(f"Arquivo não encontrado na origem: {input_csv_path}")
        raise FileNotFoundError(f"Arquivo não encontrado na origem: {input_csv_path}")

    os.makedirs(output_dir, exist_ok=True)

    df = pd.read_csv(input_csv_path)

    if df.empty:
        logger.error("O arquivo CSV de entrada está vazio.")
        raise ValueError("O arquivo CSV de entrada está vazio.")
    
    colunas_obrigatorias = ['group','names','scores','logfoldchanges','pvals','pvals_adj']
    colunas_faltantes = [col for col in colunas_obrigatorias if col not in df.columns]

    if colunas_faltantes:
        erro_msg = f"Falha crítica estrutural: Colunas obrigatórias ausentes no CSV -> {colunas_faltantes}. Colunas encontradas: {list(df.columns)}"
        logger.error(erro_msg)
        raise ValueError(erro_msg)
    
    df = df.rename(columns={
        'names': 'gene',
        'group': 'cluster',
        'logfoldchanges': 'logFC',
        'pvals': 'pval',
        'pvals_adj': 'pval_adj',
        'scores': 'score'
    })
    
    colunas_chave = ['gene', 'cluster'] 
    
    df_clean = df.drop_duplicates(subset=colunas_chave)
  
    records = df_clean.to_dict(orient='records')
    total_records = len(records)
    num_batches = math.ceil(total_records / batch_size)

    for f in os.listdir(output_dir):
        if f.startswith("batch_") and f.endswith(".json"):
            os.remove(os.path.join(output_dir, f))

    batch_ids = []

    for i in range(num_batches):
        start_idx = i * batch_size
        end_idx = start_idx + batch_size
        batch_data = records[start_idx:end_idx]

        batch_id = f"batch_{str(i+1).zfill(3)}"
        batch_file_path = os.path.join(output_dir, f"{batch_id}.json")

        with open(batch_file_path, 'w', encoding='utf-8') as f:
            json.dump(batch_data, f, ensure_ascii=False, indent=2)

        batch_ids.append(batch_id)

    logger.info(f"Processamento concluído: {total_records} registros únicos divididos em {num_batches} lotes.")
    return batch_ids