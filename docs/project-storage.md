# Projektenkénti mérési adattárolás

## Felelősségek és fájlszerkezet

A `projects.sqlite3` globális nyilvántartás. Projektet, szakaszdefiníciót,
újrafelhasználható PID-profilt és a per-projekt adatbázis abszolút útvonalát
tárolja. Másodpercenkénti telemetria nem kerül bele.

Minden élő vagy szimulációs mérési projekt saját mappát kap:

```text
YYYY-MM-DD_000001_Projekt_neve/
├── project.sqlite
├── Projekt_neve.xlsx
├── exports/
└── logs/
```

A szimuláció mappaneve `_simulation` végződésű, ezért élő és szimulált író nem
nyithatja meg egyszerre ugyanazt az adatbázist. A CSV csak kézi export és örökölt
migrációs forrás.

## Séma

A séma verziója 1. A `schema_info` tárolja a verziót, létrehozási időt,
alkalmazásverziót és az utolsó migráció verzióját. A `project` tárolja a projekt
metaadatait, mérési időhatárait, állapotát, beállítás- és mértékegység-pillanatképét.

A `phases` minden konkrét fázispéldányt külön `id` és növekvő `sequence` alapján
azonosít. A név nem egyedi: lezárás után azonos névvel új fázis indítható. A
`measurements` minden összehangolt ciklusból egy sort, az `events` a tartós
eseményeket, az `export_history` az exportnapló számára fenntartott szerkezetet
tartalmazza. Az Excel-export az adatbázist nem módosítja, ezért jelenleg nem ír az
`export_history` táblába.

Indexek:

- `idx_measurements_phase_time (phase_id, recorded_at)`;
- `idx_measurements_recorded_at (recorded_at)`;
- `idx_events_phase_time (phase_id, recorded_at)`;
- `idx_events_type_time (event_type, recorded_at)`.

Minden írható kapcsolat `WAL`, `foreign_keys=ON`, `busy_timeout=5000` és
`synchronous=FULL` beállítást használ. Az olvasók külön, csak olvasható kapcsolatot
nyitnak. Induláskor `quick_check`, sémaverzió-, tábla-, index- és idegenkulcs-
ellenőrzés fut. Korábban `running` állapotban maradt projekt `interrupted` jelölést
kap; ettől nem indul automatikus hardverművelet.

## Mezőleképezés

| Domain/korábbi CSV | SQLite | Egység / jelentés |
|---|---|---|
| `snapshot.recorded_at` | `recorded_at` | UTC ISO-8601 |
| első projektmintától eltelt idő | `project_elapsed_s` | s |
| fázis első mintájától eltelt idő | `phase_elapsed_s` | s |
| `jacket_pump.pressure_bar` | `jacket_pressure_bar` | bar |
| `jacket_pump.flow_ml_per_hour / 60` | `jacket_flow_ml_min` | mL/min |
| `jacket_pump.remaining_volume_ml` | `jacket_remaining_volume_ml` | mL |
| `jacket_net_volume_ml` | `jacket_injected_volume_ml` | mL, előjeles nettó változás |
| `injection_pump.pressure_bar` | `injection_pressure_bar` | bar |
| `injection_pump.flow_ml_per_hour / 60` | `injection_flow_ml_min` | mL/min |
| `injection_pump.remaining_volume_ml` | `injection_remaining_volume_ml` | mL |
| `injection_net_volume_ml` | `injection_injected_volume_ml` | mL, előjeles nettó változás |
| `line_pressure_bar` | `line_pressure_bar` | bar |
| `differential_pressure_bar` | `differential_pressure_bar` | bar |
| `valve_percent` | `valve_position_percent` | % |
| `quality` | négy `*_data_quality` mező | domain minőség; lekapcsolt pumpánál `disconnected` |
| `safety_reasons` | `safety_state`, `diagnostic_flags` | állapot és JSON-lista |
| nyers NI mezők | négy `raw_*` mező | V vagy bar |

A jelenlegi domain nem ad külön szenzor-mintakort, ezért a négy `*_sample_age_s`
mező `NULL`. A minta neve és azonosítója szintén `NULL`, amíg a UI/domain nem ad
egyértelmű forrást. Ezek jelentését a tárolóréteg nem találja ki.

## Adatfolyam és szálak

```text
eszközkommunikáció → MeasurementRecord → korlátozott Queue
                                      → eor-project-sqlite-writer → project.sqlite
```

A vezérlőszál `put_nowait` műveletet végez, SQLite-kapcsolatot nem használ. Egy
író szál legfeljebb 256 üzenetet és legfeljebb 0,5 másodpercnyi várakozást fog
össze egy tranzakcióba. Szabályos leállítás előbb kiüríti a sort.

Alapértékek: kapacitás 4096, figyelmeztetés 2048 (50%), kritikus határ 3686
(90%). A figyelmeztetési érték a `queue_metrics` felületen látható. A kritikus
szint vagy írószálhiba explicit `PersistenceQueueFullError`, illetve
`PersistenceWriterError`; nem történik csendes eldobás. A hiba a mérési ciklusba
jut vissza, ahol a meglévő runtime hibakezelés biztonságos mérésleállítást kér.

## Lekérdezés, Excel és NAS

A `query_measurements()` fázis-, kezdő-/záróidő-, oszlop-, lapozás- és
`max_points` szűrést végez SQL-ben. A történeti UI kompatibilitási nézete legfeljebb
20 000 pontot kér, nem olvassa be korlátlanul a teljes projektet. Az események
ugyanabból az adatbázisból kérdezhetők le a teljes projekt és az egyes fázisok
nézetéhez.

Az Excel minden futáskor új munkafüzet. `sequence` szerint egy lap készül minden
fázispéldányhoz, pontosan 13 felhasználói oszloppal. A technikai mezők nem kerülnek
bele. Az idő valódi Excel-dátum, az eltelt idő nap-tört érték `[h]:mm:ss`
formátummal. Az ideiglenes `.xlsx` fájlt visszaolvassuk és a fejlécet/lapszámot
ellenőrizzük; csak ezután történik atomikus csere.

Az aktív WAL-fájlt a NAS-szál soha nem másolja közvetlenül. Fázislezáráskor vagy
szabályos bezáráskor az SQLite backup API konzisztens helyi snapshotot készít;
csak ez kerül a tartós `nas_sync_queue.sqlite3` sorba. NAS-kiesés nem blokkolja a
mérési ciklust és nem állítja le a helyi mérést.

## Örökölt CSV migráció

A `migrate_legacy_measurement_csvs()` a régi fejlécverziókat a meglévő olvasóval
normalizálja. Forrásfájl és szakasz alapján stabil `source_key`, soronként pedig
sorszámos kulcs készül, ezért az ismételt futás nem duplikál. Az eredeti UTC
időbélyeg megmarad; a biztosan nem azonosítható szakaszú vagy hibás sor kimarad és
részletes figyelmeztetést kap. Ismeretlen fázistípus `unknown`, nem kikövetkeztetett
érték. A jelentés forrás-, beillesztett, duplikált és hibás sorszámot ad. A CSV-ket
a migráció nem törli.

## Teljesítménymérés

A mérés reprodukálható:

```powershell
$env:PYTHONPATH='src'
.venv\Scripts\python.exe scripts\benchmark_project_storage.py --samples 86400 --output benchmark
```

2026-08-10-én a fejlesztői gépen, 86 400 szintetikus mintával:

| Mérőszám | Eredmény |
|---|---:|
| átlagos sorba helyezés | 0,0149 ms |
| átlagos / maximális tranzakció | 111,7 / 180,7 ms |
| 24 órás, 20 000 pontra ritkított lekérdezés | 0,126 s; 17 280 sor |
| teljes Excel újraépítése | 27,49 s |
| adatbázis / Excel méret | 19,8 MB / 4,9 MB |
| írószál alatti Python-csúcsmemória | 1,27 MB |

A benchmark szándékosan a lehető leggyorsabban termelte a mintákat, ezért a sor
elérte a 2048-as figyelmeztetési szintet; ez nem üzemi 1 Hz terhelés. A Dell
OptiPlex 780 régi HDD-jének `FULL` fsync késleltetését ugyanezzel a paranccsal a
célgépen is meg kell mérni. Addig `NORMAL` módra váltani tilos. A hardveres
kommunikáció késését és stale/deadline viselkedését csak célgépes, fizikai I/O-s
validáció igazolhatja.
