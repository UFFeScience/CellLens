import os
import json
import hashlib
import google.generativeai as genai
from google.generativeai.types import GenerationConfig
from tenacity import retry, stop_after_attempt, wait_exponential

from scripts.logger_config import get_pipeline_logger
from scripts.utils.models import ExtractedRelation, ProvenanceMetadata, LLMOutput
from scripts.utils.database import DbManager

logger = get_pipeline_logger(__name__)

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    logger.warning("GEMINI_API_KEY ausente. Certifique-se de que está definida no .env.")
else:
    genai.configure(api_key=api_key)

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def call_gemini_structured(prompt: str, model_version: str = "gemini-2.5-flash") -> dict:
    """Executa a chamada ao Gemini forçando a resposta no formato do Pydantic LLMOutput."""
    
    model = genai.GenerativeModel(
        model_name=model_version,
        system_instruction="Você é um extrator especialista de dados bioinformáticos. Responda apenas com dados factuais contidos no texto."
    )
    
    response = model.generate_content(
        prompt,
        generation_config=GenerationConfig(
            response_mime_type="application/json",
            response_schema=LLMOutput,
            temperature=0.0
        ),
        request_options={"timeout": 120.0}
    )
    return json.loads(response.text)

@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=8))
def call_gemini_freeform(prompt: str, model_version: str = "gemini-2.5-flash") -> dict:
    """Fallback SEM response_schema estrito.

    A decodificação restrita ao schema (constrained decoding) é o que faz o
    servidor do Gemini estourar o próprio timeout (504) em certos abstracts
    (ex.: PMID 34371177) — e aí nenhum aumento de timeout do cliente resolve.
    Sem o schema, a geração costuma completar; pedimos JSON pelo mime_type e
    parseamos de forma tolerante.
    """
    model = genai.GenerativeModel(
        model_name=model_version,
        system_instruction=(
            "Você é um extrator de dados bioinformáticos. Responda APENAS com um "
            "objeto JSON no formato "
            '{"relations": [{"gene": "", "cell_group": "", "relation_type": "", "evidence_snippet": ""}]}. '
            "Sem markdown e sem texto fora do JSON."
        )
    )
    response = model.generate_content(
        prompt,
        generation_config=GenerationConfig(
            response_mime_type="application/json",
            temperature=0.1
        ),
        request_options={"timeout": 90.0}
    )
    data = json.loads(response.text)
    if isinstance(data, list):
        data = {"relations": data}
    return data if isinstance(data, dict) else {"relations": []}

def process_llm_extraction(raw_file_path: str, output_dir: str, model_version: str = "gemini-2.5-flash") -> str:
    """
    Lê os artigos brutos, envia ao LLM com cache e consolida a proveniência.
    """
    if not os.path.exists(raw_file_path):
        raise FileNotFoundError(f"Arquivo bruto não encontrado: {raw_file_path}")

    os.makedirs(output_dir, exist_ok=True)
    file_name = os.path.basename(raw_file_path).replace("raw_pubmed_", "processed_kg_")
    output_file = os.path.join(output_dir, file_name)

    with open(raw_file_path, 'r', encoding='utf-8') as f:
        articles = json.load(f)

    try:
        db = DbManager()
        db.init_cache_db(table='llm_responses')
    except Exception as e:
        logger.error(f"Falha ao inicializar banco de cache do LLM: {e}")
        raise

    processed_relations = []
    artigos_via_api = 0      # artigos que exigiram chamada ao LLM (cache MISS)
    artigos_ignorados = 0    # artigos pulados por falha total (structured + fallback)

    with db.engine.connect() as db_conn:
        for article in articles:
            abstract = article.get("abstract")
            gene_target = article.get("gene_target")
            cluster_target = article.get("cluster_target")
            pmid = article.get("provenance", {}).get("source_id", "UNKNOWN")

            if not abstract:
                continue

            prompt = (
                f"Analise o seguinte resumo científico e extraia relações diretas entre o gene '{gene_target}' e a célula '{cluster_target}'.\n"
                f"REGRA DE ONTOLOGIA ESTRITA: Se você identificar a relação, você DEVE retornar o campo 'cell_group' EXATAMENTE como '{cluster_target}', "
                f"ignorando como está escrito no texto (ex: se o texto diz 'Naïve T cells' ou 'T lymphocyte', você deve retornar '{cluster_target}').\n"
                f"Resumo: {abstract}"
            )
            prompt_hash = hashlib.md5(prompt.encode('utf-8')).hexdigest()
            
            llm_data = None

            try:
                cache_result = db_conn.execute(
                    db.text("SELECT response_json FROM pubmed_cache.llm_responses WHERE prompt_hash = :p_hash"),
                    {"p_hash": prompt_hash}
                ).fetchone()

                if cache_result:
                    logger.info(f"Cache HIT LLM para PMID: {pmid}")
                    llm_data = cache_result[0]
                else:
                    logger.info(f"Cache MISS LLM para PMID: {pmid}. Processando via Gemini...")
                    artigos_via_api += 1
                    # 1) Tenta o structured output. 
                    # 2) Se falhar (ex.: 504 do
                    # servidor no schema estrito), cai para o fallback sem schema.
                    # 3) Se ambos falharem, ignora SÓ este artigo em vez de derrubar
                    # o lote inteiro (a falha é determinística e ficaria em loop).
                    try:
                        llm_data = call_gemini_structured(prompt, model_version)
                    except Exception as e_struct:
                        logger.warning(f"Structured output falhou para PMID {pmid} ({type(e_struct).__name__}); tentando fallback sem schema...")
                        try:
                            llm_data = call_gemini_freeform(prompt, model_version)
                            logger.info(f"Fallback sem schema OK para PMID {pmid}.")
                        except Exception as e_free:
                            artigos_ignorados += 1
                            logger.error(f"Structured e fallback falharam para PMID {pmid} ({type(e_free).__name__}). Artigo ignorado.")
                            continue

                    db_conn.execute(
                        db.text("""
                            INSERT INTO pubmed_cache.llm_responses (prompt_hash, prompt_text, response_json)
                            VALUES (:p_hash, :p_text, :r_json)
                            ON CONFLICT (prompt_hash) DO NOTHING;
                        """),
                        {"p_hash": prompt_hash, "p_text": prompt, "r_json": json.dumps(llm_data)}
                    )

                # Construção do modelo  com a proveniência
                _campos_obrigatorios = ("gene", "cell_group", "relation_type", "evidence_snippet")
                for rel in llm_data.get("relations", []):
                    if not isinstance(rel, dict) or not all(rel.get(k) for k in _campos_obrigatorios):
                        logger.warning(f"Relação incompleta ignorada (PMID {pmid}): {rel}")
                        continue

                    prov = ProvenanceMetadata(
                        source_system=article.get("provenance", {}).get("source_system", "PubMed Entrez"),
                        source_id=pmid,
                        query_used=article.get("provenance", {}).get("query_used", ""),
                        llm_model=model_version
                    )

                    final_relation = ExtractedRelation(
                        gene=rel["gene"],
                        cell_group=rel["cell_group"],
                        relation_type=rel["relation_type"],
                        evidence_snippet=rel["evidence_snippet"],
                        provenance=prov
                    )

                    dumped_model = final_relation.model_dump() if hasattr(final_relation, 'model_dump') else final_relation.dict()
                    processed_relations.append(dumped_model)

            except Exception as e:
                logger.error(f"Falha ao processar extração LLM para PMID {pmid}: {e}")
                raise e

    if artigos_via_api > 0 and artigos_ignorados == artigos_via_api:
        raise RuntimeError(
            f"Todos os {artigos_via_api} artigos que exigiram chamada ao LLM falharam "
            f"(structured + fallback). Abortando para não gravar lote vazio como sucesso."
        )

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(processed_relations, f, ensure_ascii=False, indent=2)

    if artigos_ignorados:
        logger.warning(f"{artigos_ignorados} artigo(s) ignorado(s) por falha persistente do LLM.")
    logger.info(f"Extração concluída. {len(processed_relations)} relações salvas em {output_file}.")
    return output_file