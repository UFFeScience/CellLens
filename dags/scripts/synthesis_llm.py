import os
import json
import google.generativeai as genai
from google.generativeai.types import GenerationConfig
from scripts.logger_config import get_pipeline_logger

logger = get_pipeline_logger(__name__)

def generate_cluster_justifications(base_out_dir: str):
    """
    Lê o resultado do motor analítico e utiliza o LLM para redigir uma
    justificação biológica baseada nas evidências bibliográficas.
    """
    final_json_path = os.path.join(base_out_dir, "final_cluster_annotations.json")
    
    if not os.path.exists(final_json_path):
        logger.error(f"Ficheiro de anotações não encontrado em {final_json_path}")
        return

    with open(final_json_path, 'r', encoding='utf-8') as f:
        annotations = json.load(f)

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.warning("GEMINI_API_KEY ausente. Síntese ignorada.")
        return

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        system_instruction="Você é um bioinformata sénior. Escreva resumos científicos diretos e analíticos. Não use formatação markdown excessiva."
    )

    logger.info("Iniciando geração de sínteses textuais para os clusters...")

    for anno in annotations:
        cluster_id = anno.get("cluster_id")
        pred_type = anno.get("predicted_cell_type")
        conf = anno.get("confidence_score", 0)
        
        # Filtra apenas os marcadores que têm alguma associação na literatura
        supporting = [m for m in anno.get("supporting_markers", []) if m.get("cell_type_associations")]
        
        if not supporting:
            anno["state"] = "Sem evidências literárias estruturadas suficientes para justificar a identidade do cluster."
            continue

        genes_info = ", ".join([f"{m['gene_symbol']} (logFC: {m['logFC']})" for m in supporting])
        
        prompt = (
            f"Analise o cluster de scRNA-seq denominado '{cluster_id}'. "
            f"O motor analítico previu a identidade celular como '{pred_type}' com uma confiança de {conf*100:.1f}%. "
            f"Os principais genes marcadores que suportam esta predição na literatura são: {genes_info}. "
            f"Redija um único parágrafo conciso (máximo 4 frases) justificando biologicamente esta predição celular "
            f"com base nestes marcadores."
        )

        try:
            response = model.generate_content(
                prompt,
                generation_config=GenerationConfig(temperature=0.2),
                request_options={"timeout": 120.0}
            )
            anno["state"] = response.text.strip()
        except Exception as e:
            logger.error(f"Falha ao gerar síntese para o cluster {cluster_id}: {e}")
            anno["state"] = "Erro ao gerar justificativa."

    with open(final_json_path, 'w', encoding='utf-8') as f:
        json.dump(annotations, f, ensure_ascii=False, indent=2)
        
    logger.info("Síntese textual concluída e ficheiro atualizado.")