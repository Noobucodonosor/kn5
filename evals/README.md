# Benchmark di equivalenza funzionale KN5

I casi in `cases.json` rappresentano generi e sorgenti reali già incontrati da
KN5. I criteri automatici verificano contratto, concetti obbligatori, ordine,
lunghezza minima ed evidenze separate. Sono un filtro contro omissioni evidenti,
non una dimostrazione di equivalenza semantica.

Le aspettative iniziali sono state ricavate dai vecchi output della libreria e
non ancora confrontate manualmente con i media originali. Ogni caso mantiene
quindi `reference_status: derived_from_legacy_output_not_source_verified` finché
una persona non guarda la fonte e approva la scheda.

## Esecuzione

Genera un pacchetto per ogni caso dentro una cartella con lo stesso `id`, poi:

```bash
make eval PACKAGES=/percorso/ai/pacchetti
```

Il comando termina con codice diverso da zero se manca un pacchetto o fallisce
un criterio. Il caso intervista è esplicitamente disabilitato finché non esiste
una fonte adeguata.

## Revisione umana obbligatoria

Assegna 0, 1 o 2 punti a ciascuna dimensione:

1. **Fedeltà:** nessun fatto o istruzione inventati.
2. **Completezza funzionale:** il fine del contenuto è ottenibile dal solo documento.
3. **Ordine e causalità:** sequenza, dipendenze e conclusioni sono preservate.
4. **Genere:** la forma scritta è appropriata al contenuto.
5. **Uso delle fonti:** informazioni audio e visive rilevanti sono integrate.
6. **Auditabilità:** le affermazioni importanti sono rintracciabili negli artefatti sorgente.

Un caso è accettato con almeno **10/12**, nessuna dimensione a zero e tutti i
controlli automatici superati. Nome del revisore, data, punteggi e osservazioni
vanno registrati copiando `review-template.json`; senza questa revisione
l'equivalenza resta `requires_human_review`.
