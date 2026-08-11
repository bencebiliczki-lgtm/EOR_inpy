# Szálkezelés és pumpakommunikáció

## Tulajdonosi modell

A KÖP és a BES két teljesen független `PollingPump` példány. Mindkettő saját
worker szálat, `Condition` objektumot, korlátos prioritásos parancssort,
telemetria-cache-t, `IscoPump`/`DasnetClient` példányt és külön COM-portot birtokol.
Nincs közös pumpa- vagy DASNET-lock. Egy pumpa blokkoló soros tranzakciója ezért
nem tarthatja fel a másik pumpát, ugyanazon a porton viszont egyszerre legfeljebb
egy tranzakció futhat.

A port teljes életciklusa a tulajdonos workerben marad: `connect`, az azonosítás,
minden olvasás és írás, végül pontosan egy `disconnect`. A hívó szál csak indítja,
jelzi és véges timeouttal várja a workert. Ha egy soros művelet nem fejeződik be a
shutdown-határig, a portot más szál nem zárja be, és az újracsatlakozás tiltott,
amíg a worker be nem fejeződik és a külön cleanup le nem fut.

## Queue és prioritás

A pumpaparancssor alapértelmezett kapacitása pumpánként 256. A sorrend:

1. biztonsági/emergency `STOP`;
2. előkészítési és kezelői parancsok;
3. telemetria: `PRESS`, külön `STATUS`, majd `FLOW → VOLA`.

A parancs érkezése a `Condition` segítségével azonnal ébreszti a workert. A worker
minden egyes tranzakció után újra ellenőrzi a queue-t. Telített queue esetén normál
parancs explicit hibát kap; emergency STOP a legrosszabb prioritású normál queued
elemet megszakíthatja. A STOP-deduplikáció és -retesz megakadályozza a többszörös
safe-state STOP beadását.

A polling fix monotonic határidőrácsot használ. A tranzakció miatt lekésett
időpontokat átugorja, nem indít felzárkózó burstöt, és nem halmozza a pollingot a
parancsqueue-ban. A worker snapshot mutatja a queue aktuális és maximális méretét,
a tranzakciószámot, a deadline miss-eket, valamint az utolsó és maximális késést.

## Aszinkron diagnosztikai napló

A pumpaworkerek a diagnosztikai eseményt csak egy memóriapufferbe és egy korlátos
prioritásos íróqueue-ba adják. A HTML-fájl megnyitását, kötegelt írását, flush-át
és rotációját egy külön `eor-diagnostic-writer` végzi. A hardver TX/RX keretek így
nem tartják a pumpa soros lockját lemezművelet közben, és a két pumpa nem kerül
közös fájl-lock mögé.

A kritikus biztonsági eseményeknek fenntartott queue-rész van. Terhelésnél az
ismétlődő normál/debug események összevonhatók és számlált összegző esemény készül;
kritikus esemény elvesztése helyett explicit `DiagnosticQueueFullError` keletkezik.
A nyers TX/RX naplózás a Naplózási beállításokban kikapcsolható, de a warning,
error és critical keretek ekkor is megmaradnak. A queue kapacitása, maximuma,
késleltetése, batch-, file-open-, flush- és hibaszámai lekérdezhetők.

## Leállítási sorrend

Az alkalmazás leállításakor a vezérlési runtime áll le először, ezt követi a
safe-state/eszközleállítás, a mérési és export queue-k lezárása, a pumpaworkerek és
portok befejezése, majd az adatbázis/NAS erőforrások. A diagnosztikai író az utolsó:
véges timeouttal kiüríti a queue-t, flush-ol és bezárja a fájlokat. Egyetlen join
sem végtelen várakozás.

## Terhelési referencia

A korábbi szinkron HTML-íróval 1000 esemény mért ideje 50,1208 s volt
(19,95 esemény/s), eseményenként becsült egy fájlmegnyitással és flush-sal. Az
aktuális mérés a `scripts/benchmark_diagnostics.py` paranccsal ismételhető meg; az
eredmény külön mutatja az `emit` hívások idejét és a teljes queue-drain idejét,
valamint a tényleges batch/file-open/flush számlálókat.

A 2026-08-11-i fejlesztői gépes, 1000 eseményes referenciaeredmény:

| Mérőszám | Szinkron író | Aszinkron, kötegelt író |
|---|---:|---:|
| Producer/`emit` idő | 50,1208 s | 0,0160 s |
| Teljes tartós kiürítés | 50,1208 s | 0,2350 s |
| File open | kb. 1000 | 1 |
| Flush | kb. 1000 | 1 |
| Batch | — | 4 |

Ez a referencia a teljes tartós kiürítésben körülbelül 213-szoros gyorsulás; nem
valósidejű garancia, ezért a célgépen és fizikai I/O mellett is validálandó.

Az alap pumpaforgalom egy pumpán: `PRESS` 1 Hz + `STATUS` 0,25 Hz + a teljes
`FLOW/VOLA` kör 0,2 tranzakció/s, összesen 1,45 DASNET-tranzakció/s. Két pumpán ez
2,90 tranzakció/s a vezérlőparancsok, célzott ellenőrzések és retry-k nélkül.
