# Prompts used by CellLens+

This directory documents, verbatim, every prompt issued to a Large Language
Model (LLM) by the `CellLens+` pipeline, in response to the reviewer request
for full disclosure of the prompts underlying the *Literature-based Semantic
Interpretation* activity (Section 3 of the paper) and Figure 4.

`CellLens+` never uses an LLM to decide cell identity. Cell-type inference is
fully deterministic (Section 3, activity *v*, `dags/scripts/annotation_engine.py`)
and is computed **before** any LLM call. The LLM is invoked only in two,
strictly downstream, post-hoc steps:

| # | File | Purpose | Corresponds to |
|---|------|---------|-----------------|
| 1 | [`literature_relation_extraction.md`](literature_relation_extraction.md) | Extracts structured gene–cell-type relations from PubMed/PMC abstracts, used to build the knowledge graph that supplies the literature evidence (`article_count`, `literature_cell_types`) consumed by the deterministic scoring function `S(c)` | Literature retrieval that feeds activity *v* (*Quantitative Cell-Type Inference*) |
| 2 | [`cluster_semantic_synthesis.md`](cluster_semantic_synthesis.md) | Writes the interpretable, literature-grounded biological narrative attached to each cluster's already-computed annotation (the `state` field shown in Figure 4) | Activity *vi*, *Literature-Based Semantic Interpretation* |

## Implementation note on the model used

The current open-source implementation (`dags/scripts/llm_processor.py`,
`dags/scripts/synthesis_llm.py`) calls the **Google Gemini API**
(`gemini-2.5-flash`, via the `google.generativeai` SDK) with
`temperature = 0.0` for the deterministic extraction step and
`temperature = 0.2` for the narrative-synthesis step. The manuscript
(Section 3) refers to the interpretation layer generically as "an LLM
(GPT-5 with deterministic generation, i.e., temperature = 0)" to describe the
architectural role of the component rather than a fixed vendor; the workflow
is model-agnostic and any instruction-following LLM exposing a structured
(JSON) output mode can be substituted, provided `temperature = 0` is kept for
the extraction step so results remain reproducible. Both real prompts sent to
the model in this repository are reproduced below without alteration.

## Gene symbols, aliases and ontology sources

The `Gene` nodes referenced by both prompts (`gene_target`, `gene_symbol`, and
the `aliases` field returned to users in `MarkerInfo`, see
`dags/scripts/utils/models.py`) are populated upstream, before any LLM call,
by `dags/scripts/gene_enricher.py`, which standardizes gene symbols and
aliases against official nomenclature (HGNC/Ensembl, retrieved through the
MyGene.info API). The LLM never invents, normalizes, or resolves gene
symbols — it only reads whichever standardized symbol/alias it is given in
the prompt and is explicitly instructed (Prompt 1, "ONTOLOGY RULE") to return
it unchanged.
