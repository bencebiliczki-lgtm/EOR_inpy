# Pumpavezérlés

Ez a dokumentum az AFKI EOR alkalmazás két Teledyne ISCO 260D pumpájának
normál hardveres vezérlését írja le. A **KÖP** a köpenypumpa
(`jacket`), a **BES** a besajtolópumpa (`injection`). A leírás a jelenlegi
implementációt követi; a szoftveres védelem nem helyettesíti a fizikai
vészleállítót és a pumpák saját nyomásvédelmét.

## Rövid kezelői folyamat

1. A kezelő kiválasztja a projektet és a mérési szakaszt.
2. Hardvermódban aktiválja a hardvereket, és sikeresen lefuttatja az
   eszközök csak olvasási kapcsolati ellenőrzését. Szimulációs módban ez a
   lépés nem szükséges.
3. Megnyomja az **Előkészítés** gombot.
4. Ellenőrzi és elfogadja a kezdőnyomásokat, a két külön pumpanyomás-határt, az
   előkészítési flow-kat, a minimális köpenynyomás-többletet és a stabilitási időt.
5. A rendszer automatikusan felépíti a nyomást, majd **ELŐKÉSZÍTVE**
   állapotban vár. Ekkor a BES pumpa STOP állapotú, a KÖP a megadott
   nyomást tartja. PID és mérési adatmentés még nem fut.
6. A kezelő megnyomja a **Mérés indítása** gombot. A rendszer friss
   biztonsági mintát vesz, majd elindítja a szelep PID-vezérlését és az
   adatrögzítési ciklust. Pumpaparancsot nem ad ki.

A **Mérés indítása** nem kéri be az előkészítési adatokat. READY állapotban is
használható, ha a kezelő előzőleg manuálisan állította be a pumpákat: ilyenkor a
rendszer friss biztonsági előellenőrzést kér, majd közvetlenül a szelepvezérlést
és az adatrögzítést indítja. Ez az út nem hívja a pumpa-előkészítést, és nem ad
ki pumpa-`STOP`, `FLOW`, `PRESS` vagy `RUN` parancsot. Az **Előkészítés** továbbra
is választható automatikus út; annak befejezése után ugyanez a közös mérési
runtime indul el.

## Rétegek és felelősségek

| Réteg | Feladat |
|---|---|
| `DashboardWindow` | Kezelői gombok, előellenőrzés, előkészítési terv, állapotkijelzés |
| `DeviceControlService` | Alkalmazás- és mérési állapotgép, hardverengedély, globális safe-state |
| `PumpControlService` | A KÖP/BES parancssorrendje, RUN-kapuk és előkészítés |
| `PollingPump` | Egyetlen időzített telemetria-cache pumpánként, soros hozzáférés kizárása |
| `IscoPump` | DASNET-parancsok, csatornák, egységek és válaszok feldolgozása |
| `SafetyMonitor` | Nyomás-, adatminőségi, kapcsolat- és deadline-reteszek |

A UI nem beszél közvetlenül a soros driverrel. Minden fizikai pumpaművelet
az alkalmazási és pumpavezérlési szolgáltatáson keresztül fut.

Az UI és a pumpaszolgáltatás ugyanazt a `PumpStartupPlan` adatmodellt
használja. A szolgáltatás egyetlen `prepare_measurement_pumps` belépési pontja
végzi a validálást, a friss olvasási kaput, a KÖP-felfutást, a BES-indítást,
a célértékek stabilizálását; hibánál a `DeviceControlService` egyetlen központi
safe-state útvonala végzi a leállítást. A korábbi
sokparaméteres `start_measurement_pumps` csak kompatibilitási adapter.

## Állapotgép

Az alkalmazás fő állapotai:

- `IDLE`: nincs aktív hardverkapcsolat;
- `READY`: a hardver kapcsolódik, a mérés nem fut;
- `RUNNING`: előkészítés, előkészített várakozás vagy mérés fut;
- `FAULT`: reteszelt hiba, amely kezelői nyugtázást igényel.

A mérési alállapotok sorrendje:

```text
IDLE → PREPARING → WAITING_CONFIRMATION → RUNNING ↔ PAUSED
                         └── kijelzés: ELŐKÉSZÍTVE

bármely kritikus hiba → STOPPED_BY_FAULT + alkalmazás FAULT
normál leállítás   → IDLE mérési állapot + alkalmazás READY
```

A `PumpControlService` pumpánként külön követi, hogy a pumpa kapcsolódik-e,
REMOTE módban van-e, konfigurált-e, fut-e, illetve mi az aktuális üzemmódja és
célértéke. Konfiguráció csak megállított REMOTE pumpán engedélyezett.

## Kapcsolódás és jogosultság

Fizikai parancs csak akkor adható ki, ha:

- a program hardvermódban van;
- a kezelő explicit engedélyezte a fizikai hardvert;
- az NI fizikai kimenet külön engedélye is rendelkezésre áll, ha szükséges;
- a globális eszközprofil egyezik az aktív hardverprofillal;
- mindkét pumpa sikeresen kapcsolódott és ISCO 260D-ként azonosítható;
- nincs aktív biztonsági retesz.

Kapcsolódáskor az adapter `RSVP` és `IDENTIFY` lekérdezést végez. A
`COM3` a célgépen Intel AMT/SOL port, ezért nem választható ki automatikusan
pumpaportként. A tényleges KÖP/BES port- és csatornakiosztást helyszínen kell
azonosítani.

## Telemetria és polling

Mindkét pumpához egyetlen, egymástól független `PollingPump` worker, condition,
prioritásos queue, cache és soros adapter tartozik. A KÖP és a BES külön
COM-portja ezért időben átfedő tranzakciókat futtathat; ugyanazon porton viszont
mindig pontosan egy worker végez szekvenciális DASNET-műveletet. Az előkészítés, az
előkészített várakozás és a mérés ugyanazt az időbélyegzett cache-t olvassa;
nem indít egymástól független soros lekérdezési ciklusokat.

Alapértékek:

| Adat | Polling | STALE-határ |
|---|---:|---:|
| `PRESS` | 1 s | 6 s |
| `FLOW` | a 10 s-os teljes lassú körben egyszer | legalább 33 s |
| `VOLA`/`VOL` | a 10 s-os teljes lassú körben egyszer | legalább 33 s |
| `STATUS` | 4 s | 8 s |
| Kezdő telemetria | — | 8 s timeout |

A `PRESS` kapja a legmagasabb telemetria-prioritást, utána az önálló `STATUS`,
majd a `FLOW → VOLA` körforgás következik. A polling monotonic időalapú, rögzített
határidőrácsot követ. Ha egy tranzakció miatt egy vagy több időpont kimarad, a
worker a következő jövőbeli időpontra ugrik; nem épít fel hátralékot és nem indít
felzárkózó burstöt.

A nyomás és a STATUS biztonságkritikus: hibás vagy STALE értékük reteszelt
leállítást okozhat. A kizárólag FLOW/VOLA mezőt érintő hiba `DEGRADED`
kapcsolatot jelez, de önmagában nem állítja le a nyomásszabályozást.

### Mit jelent a „lekérdezési idő”?

A rendszerben négy különböző időzítés van. Ezek nem helyettesítik
egymást:

| Időzítés | Alapérték | Mit csinál? |
|---|---:|---|
| Pumpa soros polling | `PRESS` 1 s; `STATUS` 4 s; `FLOW/VOLA` 10 s | Elsőbbségi vezérlés, majd `PRESS`/`STATUS` és körforgásos `FLOW`/`VOLA` DASNET-kérések |
| Vezérlési ciklus | 0,2 s | A pumpacache-t és az NI-adatokat biztonságilag kiértékeli; méréskor PID-et futtat |
| READY/ELŐKÉSZÍTVE dashboard | 1,0 s | A cache és az NI-adatok alapján frissíti a kijelzést; nem ad ki új pumpalekérdezést |
| Adatrögzítés | 1,0 s | Az esedékes vezérlési ciklus eredményét tartósan elmenti |

Példa: 1 s-os pumpapolling és 0,2 s-os vezérlési ciklus mellett a PID
0,2 másodpercenként lefut, de két egymást követő PID-ciklus ugyanazt a
legutóbb cache-elt pumpanyomást is láthatja. Ez szándékos: a PID nem indít
saját blokkoló soros olvasást.

### Külön PRESS- és STATUS-periódus példája

A mezők nem egyetlen közös periódust használnak. Ideális, azonnali válasz esetén
egy pumpa lehetséges üteme:

```text
t = 1,000 s   PRESS
t = 2,000 s   PRESS
t = 3,000 s   PRESS
t = 4,000 s   STATUS
t = 5,000 s   PRESS
t = 10,000 s  FLOW
```

A konkrét időpontokat a válaszidő módosítja; a táblázat csak a prioritást
szemlélteti. A KÖP és a BES saját workerrel és saját időzítéssel rendelkezik.

### Mi módosítja a tényleges lekérdezési időt?

Az 1 s tervezett indítási periódus, nem garantált DASNET-válaszidő. A
tényleges frissítést az alábbiak befolyásolják:

- a pumpa válaszideje;
- a soros baud rate, alapértelmezetten 9600 baud;
- a soros timeout és az ismétlési keret;
- részleges vagy hibás DASNET-válasz;
- ugyanazon pumpának kiadott `REMOTE`, `MAXPRESS`, `FLOW`, `PRESS`, `RUN`,
  `STOP`, `SETFLOW` vagy más vezérlőparancs;
- a Windows szálütemezése és a célgép terhelése.

Egy már futó soros olvasást a program nem szakít félbe. Utána a biztonsági STOP,
az esedékes PRESS/STATUS, majd a várakozó normál parancs kap lehetőséget. A
kimaradt pollingot a rendszer nem próbálja gyors egymásutánban „behozni”; a
következő határidőt a befejezés után számítja. Az adat korát a STALE-határ
felügyeli.

Példa: ha egy `STOP` tranzakció 0,45 s-nál kezdődik és 0,30 s-ig tart, a
0,50 s-ra tervezett `PRESS` csak a STOP után futhat. A rendszer nem ad ki emiatt
felzárkózó `PRESS`-csomagot, hanem a befejezéstől újraütemez.

### Lekérdezés alkalmazási állapotonként

| Állapot | Pumpa soros polling | Cache/biztonsági olvasás | Konkrét példa |
|---|---|---|---|
| Szimuláció | Nincs fizikai DASNET-forgalom | A szimulátor adatait olvassa | A 0,2 s-os vezérlési ciklus szimulált nyomást kap, COM-port nem nyílik meg |
| `IDLE` | Nincs; a pumpaport zárva | Nincs aktív pumpacache | Programindítás után, hardveraktiválás előtt nincs `PRESS` kérés |
| Kapcsolódás | Először `RSVP`, `IDENTIFY`, majd kezdeti `PRESS` és `STATUS` | A kezdő telemetria timeout legalább a két lekérdezés teljes retry-kerete; alapértelmezetten 8 s | A `READY` csak az első nyomás- és státuszadat után jön létre; FLOW/VOLA később töltődhet fel |
| `READY` | Elsőbbségi `PRESS/STATUS`; a `FLOW/VOLA` lassú körforgás | A dashboard alapértelmezetten 1 s-onként olvassa a cache-t és az NI-t | A dashboard nem küld soros parancsot; csak a worker cache-ét jeleníti meg |
| `PREPARING` | Ugyanez a polling, vezérlőparancs alatt szüneteltetve | A beállított vezérlési ciklus, alapértelmezetten 0,2 s, olvassa a cache-t és az NI-t | A soros válaszidő nem terheli a control-cycle watchdogot, a következő `PRESS` pedig elsőbbséget kap |
| `WAITING_CONFIRMATION` / **ELŐKÉSZÍTVE** | Elsőbbségi `PRESS` és körforgásos lassú telemetria | A dashboard/biztonsági státusz alapértelmezetten 1 s-onként olvas; PID és adatmentés nincs | A KÖP nyomástartásban, a BES STOP-ban van, a cache a port tényleges kapacitásával frissül |
| `RUNNING` | Elsőbbségi `PRESS` és körforgásos lassú telemetria | A vezérlési/PID-ciklus alapértelmezetten 0,2 s, az adatrögzítés alapértelmezetten 1 s | A vezérlés cache-t olvas; nem próbál a soros port válaszidejénél gyorsabb lekérdezési terhelést kikényszeríteni |
| `PAUSED` | A 0,5 s-os polling tovább fut | A 0,2 s-os biztonsági hold-ciklus tovább fut; PID-változtatás és adatrögzítés szünetel | A nyomáshatár továbbra is felismerhető, miközben a szelep utolsó kimenete tartott |
| Normál STOP után `READY` | A STOP parancs elsőbbséget kap, utána a polling folytatódik | Ismét az 1 s-os dashboard-státusz olvassa a cache-t | A port nyitva marad, ezért új mérés előtt is látható az aktuális nyomás |
| Helyben reteszelt `FAULT` | Ha a kapcsolat megmaradt, a worker tovább frissítheti a cache-t | Automatikus újraindítás nincs; kezelői nyugtázás és friss biztonsági ellenőrzés kell | A rendelkezésre álló telemetria nem oldja fel magától a hibareteszt |
| Kapcsolatvesztéses/kritikus `FAULT` | A portok lezárása után megszűnik | Cache nem frissül, a rendszer safe-state-et kér | Új polling csak sikeres új hardveraktiválás és kapcsolódás után indul |
| Developer manuális vezérlés | Az ideiglenesen kapcsolt pumpa `PollingPump` workere a konfigurált periódust használja | A manuális ablak 1 s-onként kéri a cache-elt állapotot | Egy manuális RUN/STOP sorba áll a soros vonalon, az ablak bezárása pedig leállítja a pollingot és lezárja a portot |

### Melyik beállítás mit változtat?

- `developer/pump_pressure_poll_seconds`: csak a `PRESS` soros periódusát;
- `developer/pump_status_poll_seconds`: a `STATUS` külön, alapértelmezetten
  4 s-os periódusát;
- `developer/pump_slow_poll_seconds`: a teljes `FLOW → VOLA` kör névleges
  periódusát; a két mező ideális esetben fél periódusra követi egymást, és
  mindegyik egyszer frissül egy teljes periódus alatt;
- `developer/pump_pressure_stale_seconds`: ennyi idős nyomás minősül STALE-nek;
- `developer/pump_slow_stale_seconds`: a `FLOW/VOLA` mezők STALE-határa;
- `developer/pump_status_stale_seconds`: a biztonságkritikus `STATUS`
  explicit jóváhagyott, legalább 8 s-os STALE-határa;
- `developer/pump_startup_timeout_seconds`: a kapcsolódáskori első
  telemetria várakozása;
- `developer/control_interval_seconds`: előkészítési biztonsági és
  mérési PID-ciklus, nem soros polling;
- `hardware/status_poll_interval_seconds`: a `READY` és **ELŐKÉSZÍTVE**
  dashboard-frissítése, nem soros polling;
- `recording/interval_seconds`: tartós mérési rekordok gyakorisága.

A pumpapolling-beállítások a következő hardveraktiváláskor lépnek
életbe, mert a `PollingPump` workerek létrehozásakor kerülnek beolvasásra.
A beállítási oldal ezért külön mutatja a lemezen **mentett** és a workerekben
ténylegesen **aktív** értékeket.

Leválasztáskor a soros port csak a worker igazolt befejezése után zárható le.
Ha a worker egy aktív DASNET-tranzakcióban nem áll meg a shutdown timeoutig, a
port nyitva marad, a hiba láthatóvá válik, és reconnect nem engedélyezett. A
worker későbbi befejezése után külön disconnect-cleanup szükséges.

A pumpánkénti worker egyszerre csak egy DASNET-tranzakciót futtat. A sorrend
`emergency STOP → vezérlési STOP/REMOTE/MAXPRESS/CONFIG/RUN → PRESS → STATUS → FLOW/VOLA`.
Az esedékes STATUS-t normál parancsfolyam sem éheztetheti ki. Egy mező
befejezése után a következő határidő a tényleges befejezési időből indul; nincs
elmaradást behozó lekérdezéscsomag. Vezérlőparancs alatt a normál polling
szünetel, majd a worker újraértékeli a biztonsági telemetria esedékességét.

## Előkészítési parancssorrend

Az előkészítés előtt mindkét pumpa teljes cache-elt állapotának
rendelkezésre kell állnia. Ezután a sorrend:

### 1. Közös előkészítés és KÖP-indítás

```text
KÖP REMOTE
BES REMOTE
KÖP MAXPRESS
BES MAXPRESS
CONST FLOW
FLOW = KÖP előkészítési flow
KÖP RUN
```

A rendszer minden parancs sikeres eredményét megvárja. A BES ekkor még nem kap
flow- vagy `RUN` parancsot. Amint a `KÖP nyomás − BES nyomás` eléri a
konfigurált minimumot, alapértelmezetten 20 bart, megkezdődhet a BES indítása;
ehhez a köpenynek nem kell előbb elérnie a saját végső célját.

### 2. BES nyomásfelépítés

```text
REMOTE
MAXPRESS = BES hardveres nyomáshatár
CONST FLOW
FLOW = BES előkészítési flow
RUN
```

A BES `RUN` után mindkét pumpa párhuzamosan halad a saját célja felé. A
nyomáskülönbség minden cache-alapú felügyeleti ciklusban aktív interlock. Ha a
minimum alá esik, a BES STOP-ot kap, miközben a köpeny tovább épít vagy tart.
A BES csak a minimum + 1 bar hiszterézis elérése után indul újra.

Az állapotgép parancsonként külön aszinkron állapotot használ:

```text
mindkét REMOTE → mindkét MAXPRESS → KÖP FLOW/RUN
→ megfelelő margin → BES FLOW/RUN
→ párhuzamos célkövetés
→ KÖP cél: STOP/CONST PRESS/RUN
→ BES cél: STOP
→ mindkét friss cél + biztonságos margin → siker
```

Amikor a BES eléri vagy meghaladja a kezdőnyomást, STOP parancsot kap. Ha a
köpeny céljának elérése előtt visszaesik, biztonságos margin mellett újraindulhat.
Sikeres befejezéshez mindkét aktuális nyomásnak el kell érnie saját célját,
mindkét nyomásminőségnek `GOOD`-nak és a marginnak biztonságosnak kell lennie.
Ekkor a rendszer `WAITING_CONFIRMATION`, a felületen **ELŐKÉSZÍTVE** állapotba
kerül.

A célnyomások várakozásának nincs hard, soft vagy figyelmeztetési timeoutja.
Csak célteljesülés, safety/kommunikációs hiba vagy explicit kezelői megszakítás
zárhatja le. A konkrét soros, queue-, execution- és verification-timeoutok
változatlanul megmaradnak.

Az előkészítés indításakor nem jelenik meg külön folyamatablak. A dashboard
**Pumpa-előkészítés állapota** panelje folyamatosan megjeleníti a fázist, a két
aktuális és cél-nyomást, a margin aktuális/minimum értékét, a pumpák RUN/STOP és
REMOTE/LOCAL állapotát, a nyomástelemetria minőségét és korát, valamint az
esetleges függő parancsot. Hátralévő időt nem mutat. A kezelő ugyanitt külön
**Előkészítés megszakítása** gombbal kérhet biztonsági leállítást.
Ugyanez az aktív vagy legutolsó állapotkép Developer módban az
**Eszközkommunikáció → Előkészítés állapota…** gombbal ismét megnyitható.

Az Eszközkommunikáció ablak mellette külön **Pontos parancs-queue…** gombot ad.
Ez pumpánként, tényleges végrehajtási sorrendben mutatja a RUNNING és QUEUED
elemek azonosítóját, állapotát, parancstípusát, értékét, prioritását,
queue-várakozását, végrehajtási és ellenőrzési idejét, mindhárom timeoutját,
valamint a recovery okát vagy hibáját. A nézet csak a workerek memóriabeli
állapotát olvassa, ezért nem terheli a soros kommunikációt.
Ugyanitt a pumpánkénti élő workertábla mutatja a worker- és szálazonosítót,
COM-portot, queue-méretet, futó parancsot, PRESS/STATUS életkort, az utolsó
tranzakció idejét és a polling deadline miss számlálóját; ez is kizárólag
memóriabeli állapotot olvas.

## Vezérlési ciklus az előkészítés alatt

Az előkészítési biztonsági ellenőrzés nem használ kódba égetett külön
időzítést. Ugyanazt a Developer beállítást kapja, mint a mérési runtime:

- `developer/control_interval_seconds`: vezérlési ciklusidő;
- `developer/watchdog_tolerance_seconds`: watchdog-tűrés.

A tisztán telemetriai és biztonsági ciklus abszolút monotonic ütemen fut. Ha
egy ilyen ellenőrzés hosszabb, mint
`ciklusidő + watchdog-tűrés`, `control cycle deadline missed` hiba keletkezik,
az előkészítés megszakad, és mindkét pumpán megkísérli a STOP-ot.

A DASNET `STOP`, konfigurációs és `RUN` tranzakciók saját soros timeout- és
retry-kerettel rendelkeznek. A vezérlési szál csak `PumpCommand` objektumot tesz
queue-ba, majd későbbi ciklusokban olvassa a `CommandResult` állapotát
(`QUEUED/RUNNING/SUCCEEDED/FAILED/TIMED_OUT/CANCELLED`), ezért a soros művelet
ideje nem kerül a control-cycle watchdog alá. Az érintett pumpa workerének
pollingja a tranzakció idejére szünetel, de a másik pumpa workerét ez nem
blokkolja. A `Condition` a queue-ba helyezéskor azonnal felébreszti a workert.
A worker minden egyes pollingtranzakció után ellenőrzi a queue-t, és minden
vezérlőparancsot a következő telemetria-tranzakció előtt indít el. A prioritási
sorrend: biztonsági `STOP`; előkészítési/kezelői parancsok; majd
`PRESS/STATUS/FLOW/VOLA` telemetria. Előkészítés közben a `FLOW/VOLA` szünetel.

Ha egy parancs a queue-várakozási határig még nem indult el, `TIMED_OUT`
állapotba kerül és a worker a heap-bejegyzését végrehajtás nélkül eldobja. Így a
hívó által már timeoutként kezelt parancs a blokkoló DASNET-tranzakció után sem
futhat le későn. A queue-, execution- és verification-timeout egymástól független;
alapértékük jelenleg 5 másodperc. A már futó soros keret fizikailag nem szakítható
meg biztonságosan, ezért a késői befejezés külön naplóeseményt kap.

`REMOTE`, `RUN` és `STOP` csak célzott STATUS-visszaolvasás után sikeres. A
REMOTE ellenőrzés csak explicit Remote állapotot fogad el, például
`STOP REMOTE`; a puszta `STATUS=STOP/RUN`, a `LOCAL` és a problémajelzés nem
elegendő. Sikertelen váltáskor az előkészítés a pumpaszerepet megnevező hibával
áll le, és nem folytatja a `MAXPRESS → CONST_FLOW → RUN` sorozatot. Minden
parancs egyedi azonosítóval naplózza a pumpaszerepet, COM-portot, worker- és
szálazonosítót, queue-méretet, queue-ba kerülési monotonic időt, parancstípust, prioritást,
queue-várakozást, teljes tranzakcióidőt, végrehajtási időt, ellenőrzési időt,
recovery-okot és eredményt. A timeout megnevezi a fázist,
például `injection command execution timeout: STOP`; nem jelenhet meg
control-cycle deadline-ként.
A célzott STATUS-visszaellenőrzés után a következő periodikus STATUS határideje
egy teljes `status_poll_seconds` időközzel későbbre kerül.

A `STOP LOCAL` és `RUN LOCAL` érvényes STATUS parsereredmény, ezért adatminősége
`GOOD`. A Local mód nem szenzorhiba, hanem érvényes, távolról nem vezérelhető
állapot. Az előkészítés explicit Remote STATUS esetén kihagyja a redundáns
`REMOTE` parancsot. Local vagy nem egyértelmű állapotnál `REMOTE` paranccsal és
célzott STATUS-visszaolvasással vált vezérelhető állapotba. Safe-state alatt a
cache szerint már `STOP LOCAL` pumpa
nem kap újabb STOP-ot; a többi pumpa és a szelep biztonsági művelete ettől még
függetlenül lefut.

A Remote-felügyelet csak a teljes pumpa-előkészítés sikeres lezárása után indul.
Három egymást követő periodikus `LOCAL` STATUS után magas prioritású `REMOTE`
parancsot tesz a worker parancssorába, és célzott STATUS-visszaolvasással
ellenőrzi a helyreállítást. Egy vagy két átmeneti Local minta ezért nem okoz
recoveryt. Sikeres helyreállítás után a következő periodikus STATUS egy teljes,
alapértelmezetten 4 másodperces intervallummal későbbre kerül. A dashboard és az
üresjárati telemetria kizárólag olvas: Local állapotban sem küld `REMOTE` vagy
más vezérlőparancsot.

A vezérlési ciklus és a pumpa polling nem ugyanaz:

- a polling a soros telemetria-cache frissítési gyakorisága;
- a vezérlési ciklus a cache és az NI-adatok biztonsági kiértékelésének,
  illetve a szabályozásnak a gyakorisága.

## Mérés indítása és külön flow-váltás

A **Mérés indítása** megnyomásakor a rendszer friss biztonsági kiértékelést
végez, konfigurálja a PID-et, majd elindítja a szelepvezérlést és az
adatrögzítést. A pumpák konfigurációját és futási állapotát nem módosítja;
ezért a kézzel beállított kezdőállapot is változatlan marad.

A külön, explicit futás közbeni BES-flow módosítás sorrendje:

```text
BES STOP
→ CONST FLOW
→ FLOW = mérési flow
→ SETFLOW visszaolvasás
→ egyezés esetén RUN
```

Ha a
`SETFLOW` visszaolvasás nem egyezik a kért értékkel, a BES nem kap `RUN`
parancsot, és a rendszer kritikus hibaként kezeli az eltérést.

## Mértékegységek és pontosság

Az alkalmazás teljes pumpavezérlési útvonala **ml/h** egységben dolgozik.
Az adapter csak akkor küld `UNITS=ML/HR` parancsot, ha a nyilvántartott aktuális
egység még nem `ML/HR`; azonos egységnél a redundáns tranzakció kimarad. Ezután
változtatás nélkül ugyanazt a számértéket küldi ki. Például:

```text
kezelői cél: 20 ml/h
kiküldött FLOW: 20
visszaolvasott érték: 20 ml/h
```

Ha a pumpa a válaszban explicit `ML/MIN` egységet ad, a rendszer azt
60-nal megszorozva alakítja ml/h-ra. Egység nélküli választ az utoljára
sikeresen beállított pumpaegység szerint értelmez; `ML/HR` beállítás után nem
alkalmaz hibás ×60 szorzót. A kiküldött célérték legfeljebb három tizedesjegyet
tart meg fixpontos formában.

## Biztonsági feltételek

A pumpaparancsoknál és a vezérlési ciklusban ellenőrzött fontosabb feltételek:

- érvényes, friss nyomástelemetria;
- mindkét pumpa kapcsolata;
- a KÖP és BES külön maximális nyomása;
- vonali és differenciálnyomás határa;
- a BES nyomása nem lehet nagyobb a KÖP nyomásánál;
- a BES `RUN` előtt a konfigurált minimális KÖP–BES nyomáskülönbség;
- véges mérési adatok és megfelelő adatminőség;
- vezérlésiciklus-deadline;
- kézi vészleállítás.

A szoftveres nyomáshatárok mellett az előkészítés még a `RUN` előtt
mindkét pumpán a hozzá külön beállított `MAXPRESS` határt állítja be. Ezeket helyszíni
validációval kell összevetni a teljes hidraulikus rendszer leggyengébb elemének
határával.

## Hibakezelés és leállítás

Előkészítési timeout, telemetriahiba, nyomáshatár, deadline-túllépés vagy
más biztonsági ok esetén egyetlen safe-state tulajdonos, a
`DeviceControlService` hajtja végre a leállítást:

1. KÖP STOP megkísérlése;
2. BES STOP megkísérlése;
3. szelep/NI safe-state kérése az alkalmazási hibaútvonalon;
4. `FAULT` és `STOPPED_BY_FAULT` reteszelés;
5. részletes diagnosztikai esemény mentése.

A két STOP egymástól függetlenül megkísérlésre kerül, tehát az egyik
pumpa kommunikációs hibája nem akadályozhatja meg a másik STOP-ját. A STOP
parancs szoftveresen reteszelt. Az egymással versenyző hibaútvonalak ugyanazt a
STOP-parancsazonosítót kapják vissza, ezért nem küldenek ismételt fizikai STOP-ot.
A hiba csak biztonságos friss ellenőrzés
és kezelői nyugtázás után oldható fel.

Normál **Mérés leállítása** esetén a runtime leáll, az aktuális adatszakasz
lezárul, mindkét pumpa STOP-ot kap, a szelep safe-state-be kerül, de a sikeresen
fenntartott hardverkapcsolat `READY` állapotban megmarad.

## Fontos beállításkulcsok

| Kulcs | Jelentés |
|---|---|
| `pump_startup/jacket_target_pressure_bar` | KÖP célnyomás |
| `pump_startup/jacket_buildup_flow_ml_per_hour` | KÖP előkészítési flow |
| `pump_startup/injection_start_pressure_bar` | BES kezdőnyomás |
| `pump_startup/injection_startup_flow_ml_per_hour` | BES előkészítési flow |
| `pump_startup/injection_measurement_flow_ml_per_hour` | Korábbi kompatibilitási kulcs; a mérésindítás nem alkalmazza |
| `safety/max_jacket` | Köpenypumpára alkalmazott `MAXPRESS` |
| `safety/max_injection` | Besajtolópumpára alkalmazott `MAXPRESS` |
| `safety/minimum_margin` | Előkészítéskor szerkeszthető minimális KÖP–BES többlet |
| `pump_startup/margin_stability_seconds` | Stabilitási idő |
| `developer/pump_pressure_poll_seconds` | Nyomáspolling |
| `developer/pump_slow_poll_seconds` | Teljes FLOW/VOLA kör névleges periódusa |
| `developer/pump_status_poll_seconds` | Külön STATUS-polling, alapérték 4 s |
| `developer/pump_pressure_stale_seconds` | Nyomás STALE-határ |
| `developer/pump_slow_stale_seconds` | FLOW/VOLA STALE-határa |
| `developer/pump_status_stale_seconds` | STATUS STALE-határa |
| `developer/pump_startup_timeout_seconds` | Kezdő telemetria timeout |
| `developer/control_interval_seconds` | Előkészítési és mérési vezérlési ciklus |
| `developer/watchdog_tolerance_seconds` | Vezérlésiciklus-watchdog tűrése |

## Kapcsolódó dokumentumok

- [DASNET parancsok](DASNET_COMMANDS.md)
- [Biztonsági modell](safety.md)
- [Hardver és interfészek](hardware.md)
- [Rendszerarchitektúra](architecture.md)
- [Stabil kiinduló profil](stable-profile.md)
- [Tesztstratégia](testing.md)
