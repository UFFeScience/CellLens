import streamlit as st
import pandas as pd
import json
import os

# Configuracao global da pagina
st.set_page_config(page_title="BioQuest - Anotação scRNA-seq", layout="wide")

@st.cache_data
def load_annotation_data(filepath: str):
    """Carrega e realiza o cache do artefato ouro JSON gerado pelo Motor Analitico."""
    if not os.path.exists(filepath):
        return None
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)

def render_cluster_centric_view(data):
    """Renderiza a aba principal focada na identidade celular dos clusters."""
    st.header("Visão Cluster-Cêntrica")
    cluster_dict = {d["cluster_id"]: d for d in data}
    
    selected_cluster = st.selectbox("Selecione o Cluster Experimental:", list(cluster_dict.keys()))
    
    if selected_cluster:
        cluster_data = cluster_dict[selected_cluster]
        
        # Metricas Principais
        col1, col2, col3 = st.columns(3)
        col1.metric("Tipo Celular Previsto", cluster_data.get("predicted_cell_type", "N/A"))
        col2.metric("Grau de Confiança", f"{cluster_data.get('confidence_score', 0.0) * 100:.1f}%")
        col3.metric("Requer Revisão Manual (Ambiguidade)", "Sim" if cluster_data.get("ambiguity_flag", True) else "Não")
        
        # Sintese Textual
        st.subheader("Justificativa Biológica")
        st.info(cluster_data.get("state", "Síntese textual não gerada ou indisponível."))
        
        # Tabela de Marcadores
        st.subheader("Marcadores de Suporte")
        markers = cluster_data.get("supporting_markers", [])
        if markers:
            df_markers = pd.DataFrame(markers)
            df_display = df_markers[["gene_symbol", "gene_type", "logFC", "specificity_score", "citation_count_marker"]].copy()
            df_display["cell_type_associations"] = [", ".join(m.get("cell_type_associations", [])) for m in markers]
            
            # Renomear colunas para exibicao
            df_display.columns = ["Gene", "Tipo", "LogFC", "Especificidade", "Artigos (PubMed)", "Associações (LLM)"]
            st.dataframe(df_display, use_container_width=True)
        else:
            st.warning("Nenhum marcador de suporte encontrado para este cluster.")
            
        # Hipoteses Alternativas
        alt_hyps = cluster_data.get("alternative_hypotheses", [])
        if alt_hyps:
            st.subheader("Hipóteses Alternativas Consideradas")
            df_alt = pd.DataFrame(alt_hyps)
            df_alt.columns = ["Tipo Celular", "Score Fracionado"]
            st.dataframe(df_alt, use_container_width=True)

def render_gene_centric_view(data):
    """Renderiza a aba investigativa focada na pleiotropia e distribuicao dos genes."""
    st.header("Visão Gene-Cêntrica")
    
    # Consolidar genes unicos de todo o dataset
    all_genes = set()
    gene_metadata = {}
    for cluster in data:
        for marker in cluster.get("supporting_markers", []):
            sym = marker["gene_symbol"]
            all_genes.add(sym)
            if sym not in gene_metadata:
                gene_metadata[sym] = {
                    "aliases": marker.get("aliases", []),
                    "gene_type": marker.get("gene_type", "unknown")
                }
                
    selected_gene = st.selectbox("Selecione o Gene Marcador:", sorted(list(all_genes)))
    
    if selected_gene:
        meta = gene_metadata[selected_gene]
        st.write(f"**Classificação Genómica:** {meta['gene_type']}")
        
        aliases_text = ", ".join(meta['aliases']) if meta['aliases'] else "Nenhum alias registrado"
        st.write(f"**Sinônimos (Aliases):** {aliases_text}")
        
        st.subheader("Mapa de Expressão em Clusters")
        gene_occurrences = []
        for cluster in data:
            for marker in cluster.get("supporting_markers", []):
                if marker["gene_symbol"] == selected_gene:
                    gene_occurrences.append({
                        "Cluster": cluster["cluster_id"],
                        "LogFC": marker["logFC"],
                        "Especificidade": marker["specificity_score"],
                        "Artigos (PubMed)": marker["citation_count_marker"],
                        "Validação na Literatura": ", ".join(marker.get("cell_type_associations", []))
                    })
        
        if gene_occurrences:
            st.dataframe(pd.DataFrame(gene_occurrences), use_container_width=True)

def main():
    st.title("BioQuest: Interface Analítica de Anotação Celular")
    st.markdown("Plataforma de anotação de clusters scRNA-seq fundamentada em Knowledge Graph e processamento LLM.")
    
    # Caminho padrao apontando para a montagem de volume padrao ou ambiente de execucao local
    default_target_path = 'data/genes_marcadores_mestrado/result/final_cluster_annotations.json'
    
    file_path = st.text_input("Caminho absoluto ou relativo do artefato JSON (final_cluster_annotations.json):", default_target_path)
    
    data = load_annotation_data(file_path)
    
    if data is None:
        st.error(f"Falha ao localizar o artefato em: {file_path}. Verifique se o caminho esta correto ou se o pipeline Airflow concluiu a execucao com sucesso.")
        return
        
    tab_cluster, tab_gene = st.tabs(["Navegação por Cluster", "Navegação por Gene"])
    
    with tab_cluster:
        render_cluster_centric_view(data)
        
    with tab_gene:
        render_gene_centric_view(data)

if __name__ == "__main__":
    main()