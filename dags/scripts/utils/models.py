from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime

class ProvenanceMetadata(BaseModel):
    """
    Camada de proveniência retrospectiva exigida pela arquitetura para rastreio no Neo4j.
    """
    source_system: str = Field(description="Sistema de origem do dado (ex: PubMed, Entrez)")
    source_id: Optional[str] = Field(default=None, description="ID único na origem (ex: PMID do artigo)")
    query_used: Optional[str] = Field(default=None, description="A query exata usada para buscar o artigo")
    llm_model: Optional[str] = Field(default=None, description="Versão do modelo LLM utilizado para extração (ex: gpt-3.5-turbo)")
    extraction_date: str = Field(default_factory=lambda: datetime.utcnow().isoformat(), description="Data e hora da extração em ISO 8601")

class ArticleRaw(BaseModel):
    """
    Estrutura do artigo bruto baixado do PubMed.
    """
    gene_target: str
    cluster_target: str
    title: str
    abstract: str
    year: str
    doi: str
    citation_count_marker: int = Field(default=0, description="Total de resultados da query no PubMed")
    provenance: ProvenanceMetadata

class ExtractedRelation(BaseModel):
    """
    Estrutura da relação estruturada gerada pelo LLM pronta para o Neo4j.
    """
    gene: str
    cell_group: str
    relation_type: str = Field(description="Tipo de relação, ex: EXPRESSED_IN, ASSOCIATED_WITH")
    evidence_snippet: str = Field(description="Trecho exato do texto que comprova a relação")
    provenance: ProvenanceMetadata

class MarkerInfo(BaseModel):
    gene_symbol: str
    aliases: List[str] = []
    gene_type: str = ""
    logFC: float = 0.0
    specificity_score: float = 0.0
    cell_type_associations: List[str] = []
    citation_count_marker: int = 0
    last_cited_year: int = 2024

class AltHypothesis(BaseModel):
    cell_type: str
    score: float

class ClusterAnnotation(BaseModel):
    cluster_id: str
    predicted_cell_type: str
    confidence_score: float
    ambiguity_flag: bool
    state: str = "Unknown"
    supporting_markers: List[MarkerInfo]
    alternative_hypotheses: List[AltHypothesis]
    
class LLMRelation(BaseModel):
    gene: str = Field(description="Nome do gene identificado no texto")
    cell_group: str = Field(description="Nome do grupo celular identificado no texto")
    relation_type: str = Field(description="Tipo de relação encontrada. Use EXPRESSED_IN ou ASSOCIATED_WITH.")
    evidence_snippet: str = Field(description="Trecho exato do texto que comprova a relação estruturada")

class LLMOutput(BaseModel):
    relations: List[LLMRelation]
