"""Contratto dati puro per l'analisi multimediale di KN5."""

from typing import List, Literal

from pydantic import BaseModel, Field


class Step(BaseModel):
    step_number: int = Field(description="Numero progressivo del passaggio, partendo da 1.")
    title: str = Field(description="Titolo sintetico del passaggio o fase.")
    description: str = Field(description="Spiegazione dettagliata del passaggio: cosa fare e perché.")
    key_actions: List[str] = Field(description="Azioni concrete, specifiche, misurabili.")


class EpistemicInsight(BaseModel):
    concept: str = Field(description="Principio teorico, regola o chiave mentale dietro il processo.")
    explanation: str = Field(description="Spiegazione di come il concetto supporta il processo.")


class ProcessMap(BaseModel):
    """Output strutturato di un singolo media o segmento."""

    title: str = Field(description="Titolo chiaro del processo o tema del contenuto.")
    content_kind: Literal[
        "tutorial", "explanation", "interview", "presentation", "narrative", "other"
    ] = Field(description="Genere funzionale del contenuto, non il formato della piattaforma.")
    textual_document: str = Field(
        description=(
            "Equivalente testuale completo in Markdown, adattato al genere del contenuto. "
            "Deve permettere di ottenere lo stesso fine informativo o operativo senza "
            "guardare il media: conserva sequenza, istruzioni, esempi, avvertenze, formule, "
            "argomenti e informazioni visive rilevanti. Non è una semplice sintesi e non "
            "deve contenere frontmatter, trascrizione verbatim o informazioni inventate."
        )
    )
    executive_summary: str = Field(
        description="Sintesi 2-3 frasi del valore e obiettivo del contenuto."
    )
    steps: List[Step] = Field(
        description="Sequenza ordinata delle fasi. Vuota se il contenuto non è un processo."
    )
    epistemic_insights: List[EpistemicInsight] = Field(
        description="Concetti teorici, principi e idee chiave estratti dal contenuto."
    )
    transcript: str = Field(
        description=(
            "Trascrizione VERBATIM dell'audio, parola per parola, con timestamp [MM:SS]. "
            "Per immagini senza audio: stringa vuota."
        )
    )
    ocr_text: str = Field(
        description=(
            "Testo on-screen letterale, organizzato per slide o timestamp. "
            "Stringa vuota se nessun testo è visibile."
        )
    )
    visual_description: str = Field(
        description="Descrizione sintetica delle informazioni visive rilevanti."
    )
    language: str = Field(description="Lingua principale, come codice ISO.")
    tags: List[str] = Field(description="Da 3 a 7 tag tematici lowercase e kebab-case.")
