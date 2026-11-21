# Autonomia nello sviluppo di KN5

## Obiettivo misurabile

In questo progetto, essere uno sviluppatore autonomo non significa ricordare
tutto o lavorare senza AI. Significa saper portare una modifica di KN5
dall'intento al rilascio e, se necessario, al rollback, assumendosi le decisioni
e interpretando personalmente le prove.

L'obiettivo è circoscritto a **sviluppare e mantenere KN5 v1**. Non certifica
una competenza universale su ogni linguaggio o infrastruttura.

## Livelli

| Livello | Significato |
| --- | --- |
| 0 — non osservato | Non esiste ancora una prova diretta. Non significa “incapace”. |
| 1 — assistito | Completa il lavoro seguendo indicazioni, mentre diagnosi o decisioni decisive arrivano da altri. |
| 2 — ripetibile | Esegue e spiega un flusso già documentato, riconoscendo gli errori previsti. |
| 3 — autonomo | Davanti a un caso nuovo, formula ipotesi, sceglie un trade-off, verifica e recupera senza delegare i passaggi decisivi. |

Una capacità presente nel repository non costituisce da sola una prova sulla
persona. Codice, test e documentazione prodotti dall'AI dimostrano soltanto che
KN5 dispone di quegli strumenti.

## Fotografia attuale

Questa fotografia usa soltanto evidenze osservabili nel progetto e nelle
decisioni prese durante il suo sviluppo.

| Competenza | Evidenza attuale | Livello provvisorio | Cosa manca per il livello 3 |
| --- | --- | ---: | --- |
| Definizione del prodotto | Hai corretto l'obiettivo da “riassunto” a equivalente testuale funzionale e separato documento, trascrizione e fonte visiva. | 2 | Tradurre da solo un nuovo caso ambiguo in criteri verificabili e scarti espliciti. |
| Architettura e confini | KN5 separa Sites, API, job asincroni, acquisizione media, Gemini e persistenza. Gran parte dell'implementazione è però assistita. | 1 | Ricostruire il flusso da zero e motivare confini, costi e failure mode. |
| Modifica del codice | Esiste una pipeline funzionante e modulare, ma non è ancora documentata una modifica completata personalmente dall'inizio alla fine. | 1 | Realizzare una piccola modifica nuova, spiegando ogni riga e alternativa scartata. |
| Test e criteri di qualità | Il repository contiene test offline, gate e benchmark; non c'è ancora una prova che tu abbia scritto e interpretato personalmente un test nuovo. | 1 | Far fallire un test pertinente, correggere la causa e spiegare perché la prova copre il requisito. |
| Debugging | Retry, checkpoint e diagnostica esistono nel codice. Manca un incidente risolto con un tuo diario di ipotesi. | 0 | Diagnosticare un guasto nuovo dal sintomo alla prova, senza ricevere la causa dall'AI. |
| Git e gestione delle modifiche | I due repository sono distinti, ma entrambi hanno modifiche non committate; quattro file Sites alternativi attendono una decisione. | 0 | Selezionare, dividere e committare modifiche coerenti, quindi mostrare un rollback sicuro. |
| Valutazione del prodotto | Esistono casi e rubrica; i nuovi artefatti non sono ancora stati confrontati personalmente con fonti reali. | 1 | Valutare un output completo, motivare il punteggio e trasformare un difetto in un test. |
| Operazioni | Deploy, retry e budget sono predisposti, ma non c'è una prova personale di deploy, diagnosi e recupero. | 0 | Eseguire in staging un rilascio e un recupero controllato, leggendo i segnali corretti. |
| Comunicazione tecnica | L'intento di prodotto è chiaro; il runbook esistente è in larga parte assistito. | 1 | Scrivere una spiegazione utilizzabile da un'altra persona e verificarla con una prova a freddo. |

**Conclusione attuale:** sei già proprietario dell'intento di prodotto, ma non è
ancora dimostrata l'autonomia sull'intero ciclo tecnico. Il divario principale
non è “scrivere più codice”: è produrre prove personali di diagnosi, controllo
Git, verifica del risultato e recupero dagli errori.

## Prove obbligatorie

Ogni prova vale solo se il registro indica chiaramente cosa hai fatto tu e cosa
ha fatto l'AI. L'AI può chiarire concetti e fare review dopo il tentativo; non
può formulare l'ipotesi iniziale, scegliere il trade-off o eseguire i passaggi
decisivi al tuo posto.

### A. Spiegazione dell'architettura

Senza aprire il repository per i primi dieci minuti, disegna il percorso:
URL → acquisizione → job → analisi → artefatti → persistenza → Sites. Per ogni
confine indica almeno un errore, il segnale che lo rivela e il recupero.

Superata se, confrontando poi il disegno col codice, non confondi frontend e
backend, sai dove vivono stato e segreti e correggi autonomamente le omissioni.

### B. Controllo Git dello stato corrente

Decidi il destino dei quattro file `kn5-site/app/* 2.*`, quindi dividi le
modifiche correnti nei gruppi descritti in `CONTRIBUTING.md`. Non usare
`git add .`. Prima di ciascun commit, ispeziona il diff staged e scrivi in una
frase come annullare soltanto quel cambiamento.

Il dossier fattuale sui quattro file, preparato senza prendere la decisione al
posto tuo, è in [`autonomy/prova-b-dossier.md`](autonomy/prova-b-dossier.md).

Superata se ogni commit contiene una decisione coesa, nessun segreto o output
locale, entrambi i repository risultano comprensibili e `make verify-all`
passa sullo stato scelto.

### C. Test scritto personalmente

Scegli un requisito non ancora coperto. Scrivi prima il test, verifica che
fallisca per il motivo previsto, applica la correzione minima e verifica che
passi insieme all'intera suite.

Superata se sai spiegare perché il test falliva, quale falso positivo evita e
quale comportamento resta intenzionalmente fuori dal suo perimetro.

### D. Diagnosi di un guasto

In un ambiente locale controllato, introduci o fatti consegnare un guasto senza
conoscerne la causa. Registra sintomo, almeno due ipotesi ordinate, una prova che
possa smentire ciascuna ipotesi, causa, correzione e regressione automatica.

Superata se la causa deriva dalle prove e non da tentativi casuali o dalla
soluzione rivelata dall'AI.

### E. Valutazione di un contenuto reale

Genera il pacchetto di un contenuto reale consentito, guarda la fonte completa
e compila la rubrica in `evals/README.md`. Individua almeno una perdita o
dimostra con riferimenti verificabili perché il risultato è sufficiente.

Superata se il giudizio cita evidenze della fonte, distingue fedeltà da qualità
stilistica e trasforma un difetto ripetibile in un caso di test.

### F. Rilascio e recupero controllati

Dopo autorizzazione esplicita e in un ambiente non produttivo, esegui deploy,
smoke test e osservazione dei log. Simula un errore recuperabile e applica il
rollback o la correzione prevista dal runbook.

Superata se identifichi versione, impatto, segnale operativo, costo potenziale e
procedura di recupero senza improvvisare sul sistema di produzione.

### G. Piccola modifica indipendente

Scegli una modifica reale ma limitata che non sia già descritta passo per passo.
Portala attraverso criterio di accettazione, implementazione, test, review del
diff, commit e nota di rilascio.

Superata se tutte le condizioni della Definition of Done in `CONTRIBUTING.md`
sono soddisfatte e puoi ricostruire o rimuovere la modifica senza consultare la
conversazione con l'AI.

## Criterio finale

L'autonomia su KN5 v1 è dimostrata quando:

- tutte le prove A–G sono superate con evidenze nel registro;
- le prove C, D e G riguardano tre problemi diversi;
- almeno una prova include un tentativo fallito interpretato correttamente;
- `make verify-all` passa sullo stato finale;
- una seconda persona, oppure una revisione differita di almeno sette giorni,
  riesce a seguire il runbook senza dipendere dalla conversazione originale.

Il modello da copiare per ogni prova è in
[`autonomy/evidence-template.md`](autonomy/evidence-template.md).

## Prossimo passo

Inizia dalla **prova B**. È il collo di bottiglia più concreto: finché lo stato
Git non è deliberato e reversibile, ogni nuova funzionalità aumenta il rischio e
non aggiunge evidenza di autonomia.
