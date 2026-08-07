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
4. Ellenőrzi és elfogadja a kezdőnyomásokat, a nyomáshatárokat, az
   előkészítési flow-kat és a stabilitási időt.
5. A rendszer automatikusan felépíti a nyomást, majd **ELŐKÉSZÍTVE**
   állapotban vár. Ekkor a BES pumpa STOP állapotú, a KÖP a megadott
   nyomást tartja. PID és mérési adatmentés még nem fut.
6. A kezelő megnyomja a **Mérés indítása** gombot. A rendszer friss
   biztonsági mintát vesz, majd elindítja a szelep PID-vezérlését és az
   adatrögzítési ciklust. Pumpaparancsot nem ad ki.

A **Mérés indítása** nem kéri be újra az előkészítési adatokat, és
befejezett előkészítés nélkül sem hardver-, sem szimulációs módban nem
használható. A szimuláció ugyanazt az állapotgépet és pumpaindítási sorrendet
gyakorolja, de fizikai kimeneti parancs nélkül.

## Rétegek és felelősségek

| Réteg | Feladat |
|---|---|
| `DashboardWindow` | Kezelői gombok, előellenőrzés, előkészítési terv, állapotkijelzés |
| `DeviceControlService` | Alkalmazás- és mérési állapotgép, hardverengedély, globális safe-state |
| `PumpControlService` | A KÖP/BES parancssorrendje, RUN-kapuk, előkészítés, STOP/rollback |
| `PollingPump` | Egyetlen időzített telemetria-cache pumpánként, soros hozzáférés kizárása |
| `IscoPump` | DASNET-parancsok, csatornák, egységek és válaszok feldolgozása |
| `SafetyMonitor` | Nyomás-, adatminőségi, kapcsolat-, túllövési és deadline-reteszek |

A UI nem beszél közvetlenül a soros driverrel. Minden fizikai pumpaművelet
az alkalmazási és pumpavezérlési szolgáltatáson keresztül fut.

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
- a kiválasztott projekt eszközprofilja egyezik az aktív hardverprofillal;
- mindkét pumpa sikeresen kapcsolódott és ISCO 260D-ként azonosítható;
- nincs aktív biztonsági retesz.

Kapcsolódáskor az adapter `RSVP` és `IDENTIFY` lekérdezést végez. A
`COM3` a célgépen Intel AMT/SOL port, ezért nem választható ki automatikusan
pumpaportként. A tényleges KÖP/BES port- és csatornakiosztást helyszínen kell
azonosítani.

## Telemetria és polling

Mindkét pumpához egyetlen `PollingPump` worker tartozik. Az előkészítés, az
előkészített várakozás és a mérés ugyanazt az időbélyegzett cache-t olvassa;
nem indít egymástól független soros lekérdezési ciklusokat.

Alapértékek:

| Adat | Polling | STALE-határ |
|---|---:|---:|
| `PRESS` | 0,5 s | 6 s |
| `FLOW` | 0,5 s | 3 s |
| `VOLA`/`VOL` | 0,5 s | 3 s |
| `STATUS` | 0,5 s | 3 s |
| Kezdő telemetria | — | 3 s timeout |

A mezők egymáshoz képest eltolva futnak. Az ütemező monotonic, abszolút
határidőket használ, ezért a soros tranzakció ideje nem adódik hozzá minden
következő periódushoz. Kimaradt slotnál a következő érvényes időpontra lép.
FLOW/VOLA/STATUS vagy vezérlőparancs után nincs rejtett extra `PRESS` kérés.

A nyomás biztonságkritikus: hibás vagy STALE nyomás reteszelt leállítást
okozhat. A kizárólag FLOW/VOLA mezőt érintő hiba `DEGRADED` kapcsolatot jelez,
de önmagában nem állítja le a nyomásszabályozást.

### Mit jelent a „lekérdezési idő”?

A rendszerben négy különböző időzítés van. Ezek nem helyettesítik
egymást:

| Időzítés | Alapérték | Mit csinál? |
|---|---:|---|
| Pumpa soros polling | 0,5 s mezőnként | DASNET `PRESS`, `FLOW`, `VOLA`, `STATUS` kéréseket ad ki |
| Vezérlési ciklus | 0,2 s | A pumpacache-t és az NI-adatokat biztonságilag kiértékeli; méréskor PID-et futtat |
| READY/ELŐKÉSZÍTVE dashboard | 1,0 s | A cache és az NI-adatok alapján frissíti a kijelzést; nem ad ki új pumpalekérdezést |
| Adatrögzítés | 1,0 s | Az esedékes vezérlési ciklus eredményét tartósan elmenti |

Példa: 0,5 s-os pumpapolling és 0,2 s-os vezérlési ciklus mellett a PID
0,2 másodpercenként lefut, de két egymást követő PID-ciklus ugyanazt a
legutóbb cache-elt pumpanyomást is láthatja. Ez szándékos: a PID nem indít
saját blokkoló soros olvasást.

### Egy 0,5 s-os pollingperiódus példája

A mezők egymáshoz képest eltolva futnak. Ideális, azonnali válasz esetén egy
pumpa hozzávetőleges üteme:

```text
t = 0,000 s   FLOW
t = 0,167 s   VOLA
t = 0,333 s   STATUS
t = 0,500 s   PRESS, majd FLOW
t = 0,667 s   VOLA
t = 0,833 s   STATUS
t = 1,000 s   PRESS, majd FLOW
```

A `PRESS` és a `FLOW` saját periódusa egyaránt 0,5 s; amikor egyszerre
esedékesek, a `PRESS` kap elsőbbséget. A KÖP és a BES saját workerrel és
saját időzítéssel rendelkezik.

### Mi módosítja a tényleges lekérdezési időt?

A 0,5 s tervezett indítási periódus, nem garantált DASNET-válaszidő. A
tényleges frissítést az alábbiak befolyásolják:

- a pumpa válaszideje;
- a soros baud rate, alapértelmezetten 9600 baud;
- a soros timeout és az ismétlési keret;
- részleges vagy hibás DASNET-válasz;
- ugyanazon pumpának kiadott `REMOTE`, `MAXPRESS`, `FLOW`, `PRESS`, `RUN`,
  `STOP`, `SETFLOW` vagy más vezérlőparancs;
- a Windows szálütemezése és a célgép terhelése.

Egy már futó soros olvasást a program nem szakít félbe. Utána a várakozó
kezelői vagy biztonsági parancs elsőbbséget kap a következő pollingkéréssel
szemben. A kimaradt polling-slotokat a rendszer nem próbálja gyors egymásutánban
„behozni”; a következő jövőbeli abszolút slotra lép. Az adat korát a STALE-határ
felügyeli.

Példa: ha egy `STOP` tranzakció 0,45 s-nál kezdődik és 0,30 s-ig tart, a
0,50 s-ra tervezett `PRESS` csak a STOP után futhat. A rendszer nem ad ki emiatt
extra `PRESS` kérést, hanem visszaáll az abszolút pollingütemre.

### Lekérdezés alkalmazási állapotonként

| Állapot | Pumpa soros polling | Cache/biztonsági olvasás | Konkrét példa |
|---|---|---|---|
| Szimuláció | Nincs fizikai DASNET-forgalom | A szimulátor adatait olvassa | A 0,2 s-os vezérlési ciklus szimulált nyomást kap, COM-port nem nyílik meg |
| `IDLE` | Nincs; a pumpaport zárva | Nincs aktív pumpacache | Programindítás után, hardveraktiválás előtt nincs `PRESS` kérés |
| Kapcsolódás | Először `RSVP`, `IDENTIFY`, majd kezdeti `PRESS` és `STATUS` | A kapcsolat legfeljebb a kezdő telemetria timeoutjáig, alapértelmezetten 3 s-ig vár | A `READY` csak az első nyomás- és státuszadat után jön létre; FLOW/VOLA később töltődhet fel |
| `READY` | Folyamatos 0,5 s-os mezőperiódusok | A dashboard alapértelmezetten 1 s-onként olvassa a cache-t és az NI-t | A pumpa nyomása 12,0 s-nál frissülhet, a dashboard 12,4 s-nál mutatja; a dashboard nem küld új `PRESS` parancsot |
| `PREPARING` | Ugyanaz a folyamatos 0,5 s-os polling | A beállított vezérlési ciklus, alapértelmezetten 0,2 s, olvassa a cache-t és az NI-t | KÖP felfutásnál 0,2 s-onként ellenőrzi a KÖP–BES margint, miközben új pumpanyomás rendszerint 0,5 s-onként érkezik |
| `WAITING_CONFIRMATION` / **ELŐKÉSZÍTVE** | A 0,5 s-os polling változatlanul fut | A dashboard/biztonsági státusz alapértelmezetten 1 s-onként olvas; PID és adatmentés nincs | A KÖP nyomástartásban, a BES STOP-ban van, de mindkettő `PRESS/FLOW/VOLA/STATUS` cache-e frissül |
| `RUNNING` | A 0,5 s-os polling változatlanul fut | A vezérlési/PID-ciklus alapértelmezetten 0,2 s, az adatrögzítés alapértelmezetten 1 s | 1 másodperc alatt kb. 5 vezérlési kiértékelés, mezőnként kb. 2 pumpalekérdezés és 1 mentett rekord esedékes |
| `PAUSED` | A 0,5 s-os polling tovább fut | A 0,2 s-os biztonsági hold-ciklus tovább fut; PID-változtatás és adatrögzítés szünetel | A nyomáshatár továbbra is felismerhető, miközben a szelep utolsó kimenete tartott |
| Normál STOP után `READY` | A STOP parancs elsőbbséget kap, utána a polling folytatódik | Ismét az 1 s-os dashboard-státusz olvassa a cache-t | A port nyitva marad, ezért új mérés előtt is látható az aktuális nyomás |
| Helyben reteszelt `FAULT` | Ha a kapcsolat megmaradt, a worker tovább frissítheti a cache-t | Automatikus újraindítás nincs; kezelői nyugtázás és friss biztonsági ellenőrzés kell | A rendelkezésre álló telemetria nem oldja fel magától a hibareteszt |
| Kapcsolatvesztéses/kritikus `FAULT` | A portok lezárása után megszűnik | Cache nem frissül, a rendszer safe-state-et kér | Új polling csak sikeres új hardveraktiválás és kapcsolódás után indul |
| Developer manuális vezérlés | Az ideiglenesen kapcsolt pumpa `PollingPump` workere a konfigurált periódust használja | A manuális ablak 1 s-onként kéri a cache-elt állapotot | Egy manuális RUN/STOP sorba áll a soros vonalon, az ablak bezárása pedig leállítja a pollingot és lezárja a portot |

### Melyik beállítás mit változtat?

- `developer/pump_pressure_poll_seconds`: csak a `PRESS` soros periódusát;
- `developer/pump_slow_poll_seconds`: a `FLOW`, `VOLA` és `STATUS` saját
  periódusát;
- `developer/pump_pressure_stale_seconds`: ennyi idős nyomás minősül STALE-nek;
- `developer/pump_slow_stale_seconds`: a lassú mezők STALE-határa;
- `developer/pump_startup_timeout_seconds`: a kapcsolódáskori első
  telemetria várakozása;
- `developer/control_interval_seconds`: előkészítési biztonsági és
  mérési PID-ciklus, nem soros polling;
- `hardware/status_poll_interval_seconds`: a `READY` és **ELŐKÉSZÍTVE**
  dashboard-frissítése, nem soros polling;
- `recording/interval_seconds`: tartós mérési rekordok gyakorisága.

A pumpapolling-beállítások a következő hardveraktiváláskor lépnek
életbe, mert a `PollingPump` workerek létrehozásakor kerülnek beolvasásra.

## Előkészítési parancssorrend

Az előkészítés előtt mindkét pumpa teljes cache-elt állapotának
rendelkezésre kell állnia. Ezután a sorrend:

### 1. KÖP nyomásfelépítés

```text
REMOTE
MAXPRESS = KÖP hardveres nyomáshatár
UNITS = ML/HR
CONST FLOW
FLOW = KÖP előkészítési flow
RUN
```

A BES pumpa ekkor még nem indul. A rendszer megvárja, amíg a
`KÖP nyomás − BES nyomás` legalább a konfigurált minimum, alapértelmezetten
20 bar, és a megadott stabilitási ideig fennáll. A beállítás 0,1 bar vagy
nagyobb pozitív érték lehet; a program nem helyettesíti fix 20 baros
konstanssal.

### 2. BES nyomásfelépítés

```text
REMOTE
MAXPRESS = BES hardveres nyomáshatár
UNITS = ML/HR
CONST FLOW
FLOW = BES előkészítési flow
RUN
```

A konfigurált különbség a BES `RUN` előtti indítási kapu. A BES indulása után
a rendszer nem próbálja folyamatosan fenntartani ezt a követési különbséget;
a KÖP a kezelő által megadott fix célnyomást tartja.

A kaput a rendszer három ponton biztosítja:

1. a KÖP felfutási ciklus addig nem fejeződik be, amíg nincs meg a stabil
   konfigurált vagy annál nagyobb különbség;
2. a BES `REMOTE` és flow-konfigurációja előtt újra ellenőrzi a különbséget;
3. közvetlenül a BES `RUN` előtt ismét ellenőrzi azt.

Ha a különbség a BES konfigurálása alatt visszaesik a konfigurált minimum alá, a BES nem
indul el, az előkészítés hibával megszakad, és mindkét pumpa STOP-ot kap.

Amikor a KÖP eléri saját célját:

```text
KÖP STOP → CONST PRESS → PRESS = KÖP célnyomás → RUN
```

Amikor a BES első alkalommal eléri vagy meghaladja a kezdőnyomást, azonnal
`STOP` parancsot kap. A stabilitási idő alatt is STOP állapotban marad, tehát az
előkészítési flow nem növeli tovább a nyomást. Sikeres befejezéskor a rendszer
`WAITING_CONFIRMATION`, a felületen **ELŐKÉSZÍTVE** állapotba kerül.

## Vezérlési ciklus az előkészítés alatt

Az előkészítési biztonsági ellenőrzés nem használ kódba égetett külön
időzítést. Ugyanazt a Developer beállítást kapja, mint a mérési runtime:

- `developer/control_interval_seconds`: vezérlési ciklusidő;
- `developer/watchdog_tolerance_seconds`: watchdog-tűrés.

A ciklus abszolút monotonic ütemen fut. Ha egy ellenőrzés hosszabb, mint
`ciklusidő + watchdog-tűrés`, `control cycle deadline missed` hiba keletkezik,
az előkészítés megszakad, és mindkét pumpán megkísérli a STOP-ot.

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
→ UNITS = ML/HR
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
Flow-beállítás előtt az adapter explicit `ML/HR` egységet programoz a pumpába,
majd változtatás nélkül ugyanazt a számértéket küldi ki. Például:

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
- KÖP és BES maximális nyomása;
- vonali és differenciálnyomás határa;
- a BES nyomása nem lehet nagyobb a KÖP nyomásánál;
- a BES `RUN` előtt a konfigurált minimális KÖP–BES nyomáskülönbség;
- a szabályozott nyomás célérték feletti megengedett túllövése;
- véges mérési adatok és megfelelő adatminőség;
- vezérlésiciklus-deadline;
- kézi vészleállítás.

A szoftveres nyomáshatárok mellett az előkészítés még a `RUN` előtt
mindkét pumpán beállítja a pumpa saját `MAXPRESS` határát. Ezt helyszíni
validációval kell összevetni a teljes hidraulikus rendszer leggyengébb elemének
határával.

## Hibakezelés és leállítás

Előkészítési timeout, telemetriahiba, nyomáshatár, deadline-túllépés vagy
más biztonsági ok esetén a szolgáltatás rollbacket hajt végre:

1. KÖP STOP megkísérlése;
2. BES STOP megkísérlése;
3. szelep/NI safe-state kérése az alkalmazási hibaútvonalon;
4. `FAULT` és `STOPPED_BY_FAULT` reteszelés;
5. részletes diagnosztikai esemény mentése.

A két STOP egymástól függetlenül megkísérlésre kerül, tehát az egyik
pumpa kommunikációs hibája nem akadályozhatja meg a másik STOP-ját. A STOP
parancs szoftveresen reteszelt, hogy hibás vagy LOCAL válasz esetén ne alakuljon
ki végtelen STOP/parancsválasz hurok. A hiba csak biztonságos friss ellenőrzés
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
| `pump_startup/jacket_pressure_limit_bar` | KÖP `MAXPRESS` |
| `pump_startup/injection_pressure_limit_bar` | BES `MAXPRESS` |
| `pump_startup/margin_stability_seconds` | Stabilitási idő |
| `developer/pump_pressure_poll_seconds` | Nyomáspolling |
| `developer/pump_slow_poll_seconds` | FLOW/VOLA/STATUS polling |
| `developer/pump_pressure_stale_seconds` | Nyomás STALE-határ |
| `developer/pump_slow_stale_seconds` | Lassú mezők STALE-határa |
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
