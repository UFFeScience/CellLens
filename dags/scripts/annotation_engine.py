import os
import json
import math
import pandas as pd
from neo4j import GraphDatabase
from scripts.logger_config import get_pipeline_logger
from scripts.utils.models import MarkerInfo, AltHypothesis, ClusterAnnotation

logger = get_pipeline_logger(__name__)

def run_annotation_engine(base_out_dir: str = "/opt/airflow/data/processed"):
    """
    Motor de inferencia analitica. Realiza a juncao quantitativa (logFC) e
    semantica (LLM literature), calcula escores de confianca e exporta os relatorios finais.
    """
    uri = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD")

    cypher_query = """
    MATCH (g:Gene)-[exp:DIFFERENTIALLY_EXPRESSED_IN]->(c_exp:Cluster)
    WITH g, exp, c_exp, COUNT { (g)-[:DIFFERENTIALLY_EXPRESSED_IN]->() } AS gene_degree
    OPTIONAL MATCH (g)-[lit]->(c_lit:Cluster)
    WHERE type(lit) IN ['EXPRESSED_IN', 'ASSOCIATED_WITH', 'MARKER_FOR', 'REGULATES']
    OPTIONAL MATCH (a:Article)-[:MENTIONS]->(g)
    WITH c_exp, g, exp, gene_degree, c_lit, count(DISTINCT a) AS article_count
    RETURN
        c_exp.name AS experimental_cluster,
        g.name AS gene_symbol,
        g.aliases AS aliases,
        g.gene_type AS gene_type,
        exp.logFC AS logFC,
        gene_degree,
        article_count,
        collect(DISTINCT c_lit.name) AS literature_cell_types
    """

    try:
        with GraphDatabase.driver(uri, auth=(user, password)) as driver:
            with driver.session() as session:
                result = session.run(cypher_query)
                records = [record.data() for record in result]
    except Exception as e:
        logger.error(f"Falha ao executar query no Motor Analitico: {e}")
        raise

    if not records:
        logger.warning("Nenhum dado retornado do Neo4j para construcao do relatorio.")
        return

    df = pd.DataFrame(records)
    
    # Calculo de Especificidade (Inversamente proporcional ao numero de clusters em que aparece)
    df['specificity_score'] = 1.0 / df['gene_degree'].clip(lower=1)
    
    # Calculo do Peso do Marcador (Integrando Expressao, Especificidade e Literatura)
    # Penaliza genes sem evidencia, mas mantem o impacto do logFC
    df['gene_weight'] = df['logFC'].fillna(0.0) * df['specificity_score'] * df['article_count'].apply(lambda x: math.log10(x + 1) + 1)

    annotations = []
    summary_data = []

    for cluster_id, group in df.groupby('experimental_cluster'):
        cell_type_scores = {}
        markers = []

        for _, row in group.iterrows():
            gw = row['gene_weight']
            lit_types = row['literature_cell_types']
            
            for ct in lit_types:
                if ct:
                    cell_type_scores[ct] = cell_type_scores.get(ct, 0) + gw

            aliases = row['aliases'] if row['aliases'] else []
            if isinstance(aliases, str):
                aliases = [aliases]

            markers.append(MarkerInfo(
                gene_symbol=row['gene_symbol'],
                aliases=aliases,
                gene_type=row['gene_type'] or "unknown",
                logFC=round(row['logFC'] or 0.0, 3),
                specificity_score=round(row['specificity_score'], 3),
                cell_type_associations=lit_types,
                citation_count_marker=row['article_count']
            ))

        
        total_cluster_weight = group['gene_weight'].sum() or 1.0 # Ordenacao dos tipos celulares previstos

        sorted_ct = sorted(cell_type_scores.items(), key=lambda x: x[1], reverse=True)

        if sorted_ct:
            best_ct, best_score = sorted_ct[0]
            # Fração da evidência total do cluster que suporta esta predição
            conf = best_score / total_cluster_weight
            conf = min(max(conf, 0.0), 1.0) 
            
            ambiguity = False
            alt_hyps = []
            
            if len(sorted_ct) > 1:
                runner_up_ct, runner_up_score = sorted_ct[1]
                if (best_score - runner_up_score) < (best_score * 0.2):
                    ambiguity = True
                
                for ct, sc in sorted_ct[1:4]:
                    alt_hyps.append(AltHypothesis(cell_type=ct, score=round(sc/total_cluster_weight, 3)))
                    
            pred_type = best_ct
        else:
            pred_type = "Unknown - Requires Manual Review"
            conf = 0.0
            ambiguity = True
            alt_hyps = []

        markers.sort(key=lambda x: x.logFC * x.specificity_score, reverse=True)

        anno = ClusterAnnotation(
            cluster_id=cluster_id,
            predicted_cell_type=pred_type,
            confidence_score=round(conf, 3),
            ambiguity_flag=ambiguity,
            supporting_markers=markers[:15], 
            alternative_hypotheses=alt_hyps
        )
        annotations.append(anno)

        summary_data.append({
            "cluster_id": cluster_id,
            "predicted_cell_type": pred_type,
            "confidence_score": round(conf, 3),
            "ambiguity_flag": ambiguity,
            "top_3_markers": ", ".join([m.gene_symbol for m in markers[:3]])
        })

    os.makedirs(base_out_dir, exist_ok=True)
    clusters_dir = os.path.join(base_out_dir, "clusters")
    os.makedirs(clusters_dir, exist_ok=True)

    final_json_path = os.path.join(base_out_dir, "final_cluster_annotations.json")
    with open(final_json_path, 'w', encoding='utf-8') as f:
        json.dump([a.model_dump() for a in annotations], f, ensure_ascii=False, indent=2)

    summary_df = pd.DataFrame(summary_data)
    summary_csv_path = os.path.join(base_out_dir, "summary_metrics.csv")
    summary_df.to_csv(summary_csv_path, index=False)

    for anno in annotations:
        safe_name = anno.cluster_id.replace(" ", "_").replace("/", "_")
        cluster_file = os.path.join(clusters_dir, f"annotation_{safe_name}.json")
        with open(cluster_file, 'w', encoding='utf-8') as f:
            json.dump(anno.model_dump(), f, ensure_ascii=False, indent=2)

    logger.info(f"Motor Analitico concluido. Artefatos gerados em {base_out_dir}")