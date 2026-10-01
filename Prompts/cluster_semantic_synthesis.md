# Prompt 2 — Cluster Semantic Synthesis (Literature-Based Semantic Interpretation)

**Source file:** [`dags/scripts/synthesis_llm.py`](../dags/scripts/synthesis_llm.py)
(function `generate_cluster_justifications`)

**Role in the workflow:** implements activity *(vi) Literature-Based Semantic
Interpretation* described in Section 3 of the paper. It runs **after**
cell-type inference is finalized (`final_cluster_annotations.json`, produced
deterministically by `annotation_engine.py`) and only adds a human-readable
biological narrative to each already-computed annotation — it never alters
`predicted_cell_type`, `confidence_score`, `ambiguity_flag`, or the marker
list. This is the step that populates the free-text justification underlying
the `state` field shown in the Figure 4 JSON examples.

## Model configuration

| Parameter | Value |
|---|---|
| Model | `gemini-2.5-flash` (Google Gemini API, `google.generativeai` SDK) |
| Temperature | `0.2` |
| Timeout | 120s |
| Input | Per cluster: `cluster_id`, `predicted_cell_type`, `confidence_score`, and the subset of `supporting_markers` that already carry a `cell_type_associations` literature match (genes with no literature evidence are skipped and receive a fixed fallback string instead of a model call) |

## System instruction

```
Você é um bioinformata sénior. Escreva resumos científicos diretos e analíticos. Não use formatação markdown excessiva.
```

*(English: "You are a senior bioinformatician. Write direct, analytical scientific summaries. Do not use excessive markdown formatting.")*

## User prompt template

```
Analise o cluster de scRNA-seq denominado '{cluster_id}'. O motor analítico previu a identidade celular como '{predicted_cell_type}' com uma confiança de {confidence_score*100:.1f}%. Os principais genes marcadores que suportam esta predição na literatura são: {gene_symbol_1} (logFC: {logFC_1}), {gene_symbol_2} (logFC: {logFC_2}), ... Redija um único parágrafo conciso (máximo 4 frases) justificando biologicamente esta predição celular com base nestes marcadores.
```

*(English translation: "Analyze the scRNA-seq cluster named '{cluster_id}'. The analytical engine predicted the cellular identity as '{predicted_cell_type}' with a confidence of {confidence_score*100:.1f}%. The main marker genes from the literature supporting this prediction are: {gene list with logFC}. Write a single, concise paragraph (maximum 4 sentences) biologically justifying this cell-type prediction based on these markers.")*

All quantitative values injected into the prompt (`{predicted_cell_type}`,
`{confidence_score}`, `{gene_symbol}`, `{logFC}`) come directly, unmodified,
from the deterministic scoring engine — the LLM is only asked to explain
evidence it is handed, never to re-score or re-rank it.

## Fallback (no free-text generation call)

If a cluster has no supporting marker with a `cell_type_associations` entry,
`synthesis_llm.py` skips the LLM call entirely and sets:

```
"state": "Sem evidências literárias estruturadas suficientes para justificar a identidade do cluster."
```

*(English: "Insufficient structured literature evidence to justify the cluster identity.")*

## Worked example

The input below is built from the real, committed pipeline artifact
[`Result_Cancer/result/clusters/annotation_CANCER_ASSOCIATED_FIBROBLAST.json`](../Result_Cancer/result/clusters/annotation_CANCER_ASSOCIATED_FIBROBLAST.json)
— the same cluster shown in Figure 4b and discussed in Section 4.3 of the
paper. Note that the real prompt includes **all 15** supporting markers that
carry a `cell_type_associations` match (not just the three highlighted in the
figure for brevity):

**Input** (exact prompt the pipeline would send for this cluster):

```
Analise o cluster de scRNA-seq denominado 'CANCER ASSOCIATED FIBROBLAST'. O motor analítico previu a identidade celular como 'CANCER ASSOCIATED FIBROBLAST' com uma confiança de 96.5%. Os principais genes marcadores que suportam esta predição na literatura são: DCN (logFC: 8.805), LUM (logFC: 8.675), COL1A2 (logFC: 8.118), ACTA2 (logFC: 7.57), TAGLN (logFC: 7.563), THY1 (logFC: 6.836), BGN (logFC: 6.729), AEBP1 (logFC: 6.682), COL1A1 (logFC: 6.56), RARRES2 (logFC: 6.2), MYL9 (logFC: 5.801), SPARCL1 (logFC: 5.571), COL3A1 (logFC: 5.496), CALD1 (logFC: 5.38), COL6A2 (logFC: 5.359). Redija um único parágrafo conciso (máximo 4 frases) justificando biologicamente esta predição celular com base nestes marcadores.
```

**Illustrative output** (`temperature=0.2`, free-text paragraph that would be
stored in `state`):

> The committed artifact's `state` field currently reads `"Unknown"` — the
> synthesis step was not executed for this particular pipeline run (the
> `GEMINI_API_KEY` available in this environment also returned
> `403 PERMISSION_DENIED` when this document was written, so a fresh capture
> was not possible either). The paragraph below is an illustrative example
> consistent with the markers above and with the CAF description in Section
> 4.3 of the paper, not a captured model response. Re-running
> `generate_cluster_justifications` with a valid key against this exact
> artifact will populate `state` with the genuine output.

```
A predição de fibroblasto associado ao câncer (CAF) para este cluster é fortemente
sustentada pelo perfil de marcadores de matriz extracelular observado. A elevada
expressão de DCN, LUM e COL1A2 reflete a intensa atividade de deposição de colágeno e
proteoglicanos, característica de fibroblastos ativados no microambiente tumoral.
A coexpressão de ACTA2, TAGLN e THY1, marcadores de diferenciação miofibroblástica,
corrobora o estado ativado descrito na literatura para CAFs. Em conjunto, essas
evidências sustentam a alta confiança (96.5%) atribuída pelo motor analítico a esta
identidade celular.
```

