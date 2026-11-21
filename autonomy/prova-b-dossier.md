# Dossier per la prova B — file Sites alternativi

Questo documento contiene fatti verificati, non la decisione da prendere. La
decisione e la sua motivazione devono essere scritte personalmente nel registro
della prova B.

## Stato osservato

I quattro file sono non tracciati nel repository `kn5-site`; i rispettivi file
senza ` 2` sono invece tracciati. Una ricerca sull'app non trova import o
riferimenti ai nomi alternativi.

| File alternativo | Confronto con il file attivo | Evidenza rilevante |
| --- | --- | --- |
| `app/globals 2.css` | 26 righe contro il foglio attivo completo | Contiene lo stile iniziale Tailwind/Geist, non il design KN5 attuale. |
| `app/layout 2.tsx` | 33 righe contro il layout attivo | Usa titolo `Untitled site`, lingua inglese e font dello starter. |
| `app/page 2.tsx` | 107 righe contro la pagina attiva | Mostra lo scheletro “Your site is taking shape”; non contiene il flusso job KN5. |
| `app/visual 2.css` | Un foglio minificato diverso da quello attivo | Contiene lo stile della precedente vista audiovisiva; la cronologia Git conserva già quella fase nei commit precedenti. |

Le date locali indicano che i quattro alternativi precedono i corrispondenti
file attivi. Le date sono un indizio, non una prova sufficiente da sole; la
prova più forte è data da contenuto, riferimenti e cronologia Git.

## Opzioni ammissibili

1. **Rimuovere i file alternativi dal progetto.** È coerente se non contengono
   lavoro unico da recuperare. La cronologia Git conserva le versioni precedenti
   dei file attivi.
2. **Archiviarli fuori dal repository.** È coerente se vuoi conservarli come
   materiale personale temporaneo, senza farli diventare parte del prodotto.
3. **Trasformare intenzionalmente una variante in codice di prodotto.** È
   coerente soltanto se esiste un requisito attuale, il file riceve un nome
   semantico, viene importato e coperto dalle verifiche appropriate.

Ignorarli in `.gitignore` senza chiarirne lo scopo non risolve il problema: li
rende semplicemente invisibili al normale controllo dello stato.

## Decisione da compilare personalmente

- Opzione scelta:
- Ragione collegata al prodotto:
- Informazione unica che rischierei di perdere:
- Come ho verificato che tale informazione esista oppure no:
- Come recupererei la scelta se fosse sbagliata:

## Controlli dopo la decisione

Nel repository `kn5-site`:

```sh
git status --short
git diff
git diff --cached
npm run build
```

Dal repository root:

```sh
make verify-all
```

L'esito va riportato in una copia di `autonomy/evidence-template.md`; non basta
che il comando termini senza errori, bisogna spiegare che cosa ha controllato e
che cosa non può dimostrare.
