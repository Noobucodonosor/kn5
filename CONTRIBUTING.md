# Sviluppare KN5 in autonomia

KN5 contiene due repository indipendenti:

- root: backend Python, job, deploy, test ed eval;
- `kn5-site/`: interfaccia e proxy API pubblicati con Sites.

Esegui i comandi Git nel repository che possiede realmente i file. Non usare
`git add .` dal root per tentare di includere il frontend.

## Ciclo di lavoro

1. Scrivi in una frase il risultato osservabile dell'utente.
2. Definisci almeno un caso felice, un errore atteso e la prova di accettazione.
3. Riproduci il comportamento corrente prima di modificare il codice.
4. Cambia il minimo insieme coeso di file.
5. Aggiungi o aggiorna prima la prova automatica appropriata.
6. Esegui `make verify`; se cambia Sites, esegui `make verify-all`.
7. Controlla `git diff`, poi `git diff --cached` prima del commit.
8. Scrivi un commit che descriva una sola decisione reversibile.
9. Per provider reali, esegui il controllo live separato e annota data/esito.

## Definition of Done

Una modifica è conclusa soltanto quando:

- il criterio utente è esplicito e verificato al livello corretto;
- test offline e controlli di sintassi passano;
- gli errori previsti sono diagnosticabili e recuperabili;
- compatibilità, costi e persistenza sono stati considerati;
- README/config/deploy descrivono il comportamento effettivo;
- nessun segreto, dato personale o output generato è candidato al commit;
- il diff staged contiene soltanto la decisione dichiarata dal messaggio;
- esiste un modo chiaro per tornare alla versione precedente.

Per cambiamenti alla qualità dei documenti, i test unitari non bastano: serve
anche `make eval PACKAGES=...` e la revisione umana descritta in `evals/README.md`.

## Uso dell'AI senza perdere autonomia

Prima di chiedere una correzione, annota sintomo, ipotesi e prova che potrebbe
smentirla. Dopo una proposta dell'AI devi poter:

- spiegare ogni file modificato;
- prevedere almeno un modo in cui la soluzione può fallire;
- scegliere personalmente criteri e trade-off;
- eseguire le verifiche senza delegarne l'interpretazione;
- ricostruire o rimuovere la modifica senza la conversazione originale.

Se non puoi fare queste cinque cose, la modifica è assistita ma non ancora tua.

## Piano di commit per lo stato corrente

Non eseguire questa sequenza meccanicamente: rivedi ogni diff e usa staging
interattivo quando un file contiene decisioni diverse.

### Repository backend

1. `chore(repo)`: confini Git, ignore, configurazione di esempio e `repo-check`.
2. `feat(media)`: acquisizione YouTube/Instagram, metadata e rimozione del vecchio vision processor.
3. `feat(content)`: schema adattivo, ricomposizione, verifica e pacchetto di artefatti.
4. `feat(jobs)`: job asincroni, checkpoint, retry, budget idempotente e API.
5. `test(eval)`: suite offline, casi golden, valutatore e rubrica umana.
6. `feat(operations)`: bot, bulk, dashboard, container e deploy Cloud Run.
7. `docs`: README, knowledge base e strumenti locali.

### Repository Sites

1. `feat(artifacts)`: proxy e interfaccia per document, transcript, visual e manifest.
2. `chore(types)`: esclusione degli artefatti generati dal type-check.

Prima dei commit Sites va presa una decisione esplicita sui quattro file
`app/* 2.*`: sono versioni alternative non tracciate, non output del build.
