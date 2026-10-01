# Prompt 1 — Literature Relation Extraction

**Source file:** [`dags/scripts/llm_processor.py`](../dags/scripts/llm_processor.py)
(function `call_gemini_structured`, called from `process_llm_extraction`)

**Role in the workflow:** builds the literature-derived knowledge graph
(gene → cell-type relations, with provenance) that is later consumed, purely
deterministically, by the scoring function `S(c)` in
`dags/scripts/annotation_engine.py`. This step never sees cluster scores or
predictions — only raw PubMed/PMC abstracts.

## Model configuration

| Parameter | Value |
|---|---|
| Model | `gemini-2.5-flash` (Google Gemini API, `google.generativeai` SDK) |
| Temperature | `0.0` (deterministic) |
| Output mode | Forced structured JSON (`response_mime_type="application/json"`, `response_schema=LLMOutput`) |
| Retries | 3 attempts, exponential backoff (2–10s) |
| Timeout | 120s |
| Caching | Response cached by MD5 hash of the prompt text (`pubmed_cache.llm_responses`), so identical prompts are never resent to the model |
| Fallback | If the structured call fails, a second, schema-free call (`call_gemini_freeform`, below) is attempted before the article is skipped |

## System instruction

```
Você é um extrator especialista de dados bioinformáticos. Responda apenas com dados factuais contidos no texto.
```

*(English: "You are an expert bioinformatics data extractor. Respond only with factual data contained in the text.")*

## User prompt template

```
Analise o seguinte resumo científico e extraia relações diretas entre o gene '{gene_target}' e a célula '{cluster_target}'.
REGRA DE ONTOLOGIA ESTRITA: Se você identificar a relação, você DEVE retornar o campo 'cell_group' EXATAMENTE como '{cluster_target}', ignorando como está escrito no texto (ex: se o texto diz 'Naïve T cells' ou 'T lymphocyte', você deve retornar '{cluster_target}').
Resumo: {abstract}
```

*(English translation: "Analyze the following scientific abstract and extract direct relations between gene '{gene_target}' and cell '{cluster_target}'. STRICT ONTOLOGY RULE: If you identify the relation, you MUST return the 'cell_group' field EXACTLY as '{cluster_target}', regardless of how it is written in the text (e.g., if the text says 'Naïve T cells' or 'T lymphocyte', you must return '{cluster_target}'). Abstract: {abstract}")*

Placeholders `{gene_target}`, `{cluster_target}`, and `{abstract}` are filled,
verbatim and without additional instructions, from the record retrieved by
the literature-search activity (`dags/scripts/pubmed_client.py`). `gene_target`
and `cluster_target` are the standardized symbols produced upstream by
`dags/scripts/gene_enricher.py` (HGNC/Ensembl nomenclature, via MyGene.info) —
the "ontology rule" exists precisely so that the LLM output stays anchored to
that controlled vocabulary instead of copying whatever synonym happens to
appear in the abstract text.

## Structured output schema (enforced by `response_schema`)

```python
class LLMRelation(BaseModel):
    gene: str
    cell_group: str
    relation_type: str        # "EXPRESSED_IN" or "ASSOCIATED_WITH"
    evidence_snippet: str     # exact text span supporting the relation

class LLMOutput(BaseModel):
    relations: List[LLMRelation]
```

Each returned relation is subsequently wrapped with a `ProvenanceMetadata`
object (source system, PMID, query used, LLM model, extraction timestamp)
before being persisted to the Neo4j knowledge graph, as required by the
project's retrospective-provenance requirement.

## Fallback prompt (schema-free)

**Source:** `call_gemini_freeform`, same file. Triggered only when the structured
call above raises an exception (observed in production for certain long
abstracts that caused the server-side constrained decoding to time out, e.g.
PMID 34371177). The user prompt template is identical to the one above;
only the system instruction and decoding mode change — JSON is requested via
`response_mime_type` but without a hard `response_schema`, and the response is
parsed tolerantly (a bare JSON list is coerced into `{"relations": [...]}`).

| Parameter | Value |
|---|---|
| Temperature | `0.1` |
| Timeout | 90s |
| Retries | 2 attempts, exponential backoff (2–8s) |

System instruction:

```
Você é um extrator de dados bioinformáticos. Responda APENAS com um objeto JSON no formato {"relations": [{"gene": "", "cell_group": "", "relation_type": "", "evidence_snippet": ""}]}. Sem markdown e sem texto fora do JSON.
```

*(English: "You are a bioinformatics data extractor. Respond ONLY with a JSON object in the format {...}. No markdown and no text outside the JSON.")*

If both the structured and the fallback call fail for a given abstract, that
single article is skipped (logged as `artigos_ignorados`) rather than failing
the whole batch; if every article in a batch that required an LLM call fails
both paths, the batch itself is aborted rather than silently written as empty.

## Worked example

The abstract below is a real record retrieved by the pipeline during the
breast-cancer run (PMID 41977392, raw-cache batch `raw_pubmed_batch_006.json`
— the raw PubMed cache is a reproducible pipeline artifact and is not
committed to this repository), for `gene_target="DCN"`,
`cluster_target="cancer associated fibroblast"`
— the same gene/cell-type pair behind the CAF example in
[`cluster_semantic_synthesis.md`](cluster_semantic_synthesis.md) and Figure 4b
of the paper.

**Input** (exact prompt sent to the model):

```
Analise o seguinte resumo científico e extraia relações diretas entre o gene 'DCN' e a célula 'cancer associated fibroblast'.
REGRA DE ONTOLOGIA ESTRITA: Se você identificar a relação, você DEVE retornar o campo 'cell_group' EXATAMENTE como 'cancer associated fibroblast', ignorando como está escrito no texto (ex: se o texto diz 'Naïve T cells' ou 'T lymphocyte', você deve retornar 'cancer associated fibroblast').
Resumo: Digestive cancers exhibit high heterogeneity and poor prognosis, yet whether their tumor microenvironments share conserved stromal-immune interactions remains unclear. Here, we performed an integrative multi-omics analysis across seven digestive cancer types and identified a conserved four-gene signature-DCN, COL10A1, CTHRC1, and TREM2-that is consistently enriched in matrix cancer-associated fibroblasts (mCAFs) and myeloid cells. Single-cell RNA sequencing revealed that DCN, COL10A1, and CTHRC1 are predominantly expressed in mCAFs, while TREM2 is enriched in myeloid cells and, to a lesser extent, in antigen-presenting CAFs(apCAFs). Cell-cell communication analysis consistently identified a fibroblast-to-myeloid signaling network centered on ECM-CD44 interactions across all examined cancer types, providing a candidate framework for intercellular crosstalk. Multi-omics profiling further characterized the genomic, epigenetic, and immune correlates of this signature. Collectively, these findings identify a conserved stromal-myeloid gene signature across digestive cancers and provide a candidate gene set for future diagnostic and therapeutic exploration.
```

**Expected output** (structured JSON, `temperature=0`):

> This output was not captured from a live call — the `GEMINI_API_KEY`
> available in this environment returned `403 PERMISSION_DENIED` when this
> document was written, so no network call could be made. It is shown here as
> the expected, schema-conformant extraction given the abstract's explicit
> statement that "DCN ... [is] predominantly expressed in mCAFs," reproduced
> verbatim as the `evidence_snippet`. The genuine response for this exact
> prompt is recoverable from the pipeline's own cache
> (`pubmed_cache.llm_responses`, keyed by the MD5 hash of the prompt text
> above) once a valid API key is restored.

```json
{
  "relations": [
    {
      "gene": "DCN",
      "cell_group": "cancer associated fibroblast",
      "relation_type": "EXPRESSED_IN",
      "evidence_snippet": "DCN, COL10A1, and CTHRC1 are predominantly expressed in mCAFs"
    }
  ]
}
```
